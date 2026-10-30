"""Agricultural Bank of China (农业银行) credit card statement provider."""

from __future__ import annotations

import email
import logging
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import dropwhile
from pathlib import Path
from typing import NamedTuple

from ....config.schema import ProviderConfig
from ....core.coverage import Coverage
from ....core.types import ReconcileContext, ReconcileResult, Transaction
from ... import register_provider
from ..._pdf import pdf_lines
from ...base import BaseProvider

logger = logging.getLogger(__name__)

# Statement's own masked card number: "620000******1234", or with five
# asterisks in the older mail template
_MASKED_CARD_RE = re.compile(r"^\d{6}\*{5,6}\d{4}$")
# Transaction date cell: YYMMDD, or YYYYMMDD in the older mail template
_ROW_DATE_RE = re.compile(r"^\d{6}(?:\d{2})?$")
# "2030/01/01-2030/01/31", or "20300101-20300131" in the older mail template
_CYCLE_RE = re.compile(r"(\d{4})/?(\d{2})/?(\d{2})-(\d{4})/?(\d{2})/?(\d{2})")
_FORMULA_OPERATORS = ("-", "=", "+")
_CARD_CELL_RE = re.compile(r"^(\d{4})[主附]?$")
_ROW_AMOUNT_RE = re.compile(r"^-?[\d,]+\.\d+/[A-Z]{3}$")
_SUMMARY_AMOUNT_RE = re.compile(r"^-?[\d,]+\.\d{2}$")
_PDF_TITLE = "农业银行信用卡对账单"


class _MailTemplate(NamedTuple):
    """One layout of the mailed statement.

    ``header`` holds the prefixes of the detail header's cells, whitespace
    removed; the 账务说明 labels name the formula cells the report reads.
    Detail rows share one shape in every layout (two dates, the card cell,
    the description cells, two amounts), so a layout is told apart only by
    these labels.
    """

    header: tuple[str, ...]
    new_charges_label: str
    adjustment_label: str | None


_TEMPLATES: tuple[_MailTemplate, ...] = (
    _MailTemplate(
        header=(
            "交易日期",
            "入账日期",
            "卡号后四位",
            "交易描述",
            "交易金额/币种",
            "入账金额/币种",
        ),
        new_charges_label="本期账单金额",
        adjustment_label="本期调整金额",
    ),
    # Older mails split the description into 交易摘要 and 交易地点 and print
    # no 本期调整金额; their 刷卡金 arrives as itemised 刷卡金转入 rows.
    _MailTemplate(
        header=(
            "交易日",
            "入账日期",
            "卡号后四位",
            "交易摘要",
            "交易地点",
            "交易金额/币种",
            "入账金额/币种",
        ),
        new_charges_label="本期新增应还款额",
        adjustment_label=None,
    ),
)


@dataclass
class StatementSummary:
    """Summary information extracted from ABC statement."""

    card_number: str | None = None  # 卡号 (masked, e.g., 620000******1234)
    statement_period: tuple[date, date] | None = None  # 账单周期
    statement_balance: Decimal | None = None  # 本期应还款额
    new_charges: Decimal | None = None  # 本期账单金额 (from 账务说明)
    adjustment: Decimal | None = None  # 本期调整金额 (from 账务说明)
    rebate_used: Decimal = Decimal(0)  # 本期使用刷卡金

    @property
    def rebate_offset(self) -> Decimal:
        """Rebate that actually reduced the debt this cycle.

        本期使用刷卡金 is gross: when a purchase paid with 刷卡金 is refunded,
        the bank reclaims that 刷卡金 and only the net reaches 本期调整金额,
        which is the figure in the statement's balance formula.
        """
        if self.adjustment is None:
            return self.rebate_used
        return min(self.rebate_used, self.adjustment)


@register_provider
class ABCCreditProvider(BaseProvider):
    """
    Provider for Agricultural Bank of China (农业银行) credit card statements.

    Two formats carry the same detail rows:
    - .eml: the mailed statement (金穗信用卡电子对账单), HTML in one of the
      layouts in ``_TEMPLATES``, told apart by the detail header
    - .pdf: the reissued statement (农业银行信用卡对账单YYYY年MM月（补）), a
      text PDF without the mail's 账务说明 formula or 刷卡金 summary

    Each layout's columns are listed in ``_TEMPLATES``; the settlement amount
    is negative for expenses (支出为-).
    """

    provider_id = "abc_credit"
    provider_name = "农业银行信用卡"
    supported_formats = [".eml", ".pdf"]
    filename_keywords = ["农业银行", "金穗信用卡"]
    content_keywords = ["金穗信用卡电子对账单", _PDF_TITLE]
    per_card_statement = True  # ABC sends separate statements per card
    coverage_priority = ("statement", "reissue")

    def __init__(self) -> None:
        super().__init__()
        # A run calls parse() and post_output() on the same instance, so the
        # report reads each file's summary from here.
        self._summaries: dict[Path, StatementSummary] = {}

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse an ABC credit card statement, mailed or reissued."""
        lines = self._lines(file_path)
        summary = _read_summary(lines)
        self._summaries[file_path] = summary
        statement_period = summary.statement_period
        statement_card = summary.card_number[-4:] if summary.card_number else None
        if file_path.suffix.lower() == ".pdf":
            transactions = self._pdf_transactions(lines, file_path, statement_card)
        else:
            transactions = self._mail_transactions(lines, file_path, statement_card)
        if statement_period is None:
            logger.warning(
                "No statement cycle found in ABC statement %s", file_path.name
            )

        self.stamp_cycle(transactions, statement_period)
        return transactions

    def coverage(
        self, file_path: Path, transactions: list[Transaction]
    ) -> list[Coverage]:
        """Cover each card for the printed cycle; the mail outranks its reissue."""
        source = "reissue" if file_path.suffix.lower() == ".pdf" else "statement"
        return self.cycle_coverages(transactions, source=source)

    def _pdf_transactions(
        self,
        lines: list[list[str]],
        file_path: Path,
        statement_card: str | None,
    ) -> list[Transaction]:
        """Read detail rows: two dates, the card cell, the description, two amounts.

        A blank cell prints no span, so a row without a card number has one
        cell fewer; the cells are put back in the mail's six columns.
        """
        transactions: list[Transaction] = []
        for line in lines:
            if not (
                len(line) >= 4
                and _ROW_DATE_RE.match(line[0])
                and _ROW_DATE_RE.match(line[1])
                and _ROW_AMOUNT_RE.match(line[-2])
                and _ROW_AMOUNT_RE.match(line[-1])
            ):
                continue
            middle = line[2:-2]
            card = middle.pop(0) if middle and _CARD_CELL_RE.match(middle[0]) else ""
            cells = [line[0], line[1], card, " ".join(middle), line[-2], line[-1]]
            txn = self._parse_transaction_row(
                cells, file_path, len(transactions) + 1, statement_card
            )
            if txn:
                transactions.append(txn)
        return transactions

    def _lines(self, file_path: Path) -> list[list[str]]:
        if file_path.suffix.lower() == ".pdf":
            return pdf_lines(file_path)
        return self._mail_lines(file_path)

    def _mail_lines(self, file_path: Path) -> list[list[str]]:
        """Return every table row of the mail, as its direct cells' texts.

        Layout rows wrap whole blocks in one cell, so the readers match cells
        by their full text and rows by their cell count.
        """
        with open(file_path, "rb") as f:
            msg = email.message_from_binary_file(f)
        if not any(part.get_content_type() == "text/html" for part in msg.walk()):
            logger.warning("No HTML body in ABC mail %s", file_path.name)
        soup = self.parse_html(self._extract_html_from_message(msg))
        return [
            [
                self.clean_text(td.get_text())
                for td in tr.find_all("td", recursive=False)
            ]
            for tr in soup.find_all("tr")
        ]

    def _mail_transactions(
        self,
        lines: list[list[str]],
        file_path: Path,
        statement_card: str | None,
    ) -> list[Transaction]:
        """Read the detail rows that follow a template's detail header.

        A row starts with its two dates, after spacer cells in the older
        template, and ends with its two amounts; the cells between them are
        the card number and the description, which the older template
        prints in two cells (交易摘要, 交易地点) or one.
        """
        start = next(
            (
                i + 1
                for i, line in enumerate(lines)
                if any(_is_header(line, template) for template in _TEMPLATES)
            ),
            None,
        )
        if start is None:
            # Dated rows under no known header mean the bank changed its
            # layout; an empty result alone would look like a quiet month.
            if any(_first_date_index(line) is not None for line in lines):
                logger.warning(
                    "Unrecognised ABC statement layout in %s", file_path.name
                )
            return []

        transactions: list[Transaction] = []
        for line in lines[start:]:
            first = _first_date_index(line)
            if first is None:
                continue
            cells = line[first:]
            if not (
                len(cells) >= 6
                and _ROW_AMOUNT_RE.match(cells[-2])
                and _ROW_AMOUNT_RE.match(cells[-1])
            ):
                logger.warning(
                    "Skipping ABC transaction-like row in %s: %s",
                    file_path.name,
                    " | ".join(cells),
                )
                continue
            description = " ".join(cell for cell in cells[3:-2] if cell)
            row = [cells[0], cells[1], cells[2], description, cells[-2], cells[-1]]
            txn = self._parse_transaction_row(
                row, file_path, len(transactions) + 1, statement_card
            )
            if txn:
                transactions.append(txn)
        return transactions

    def _parse_transaction_row(
        self,
        cells: list[str],
        file_path: Path,
        row_idx: int,
        statement_card: str | None,
    ) -> Transaction | None:
        """Parse a single transaction row from its six cell texts."""
        try:
            trans_date_str = cells[0]
            card_cell = cells[2]
            description = cells[3]
            settle_amount = cells[5]

            if not _ROW_DATE_RE.match(trans_date_str):
                return None

            trans_date = self._parse_date(trans_date_str)
            if not trans_date:
                return None
            post_date = (
                self._parse_date(cells[1]) if _ROW_DATE_RE.match(cells[1]) else None
            )

            # Card field may carry a 主/附 suffix (e.g. "5678附"); keep the 4 digits.
            # Account-level rows (e.g. 约定还款) leave it empty — those belong to
            # the statement's own card, since each statement covers a single card.
            card_last4: str | None
            card_match = _CARD_CELL_RE.match(card_cell)
            if card_match:
                card_last4 = card_match.group(1)
            elif not card_cell:
                card_last4 = statement_card
            else:
                logger.warning(
                    "Skipping ABC row %d in %s: unrecognised card field %r",
                    row_idx,
                    file_path.name,
                    card_cell,
                )
                return None

            # Parse amount and currency (e.g., "-10.00/CNY" or "14.00/CNY")
            amount, currency = self._parse_amount(settle_amount)
            if amount is None:
                return None

            # Negate: source uses negative for expenses, we use positive
            amount = -amount

            return Transaction(
                date=trans_date,
                post_date=post_date,
                amount=amount,
                currency=currency,
                description=description,
                card_last4=card_last4,
                provider=self.provider_id,
                source_file=file_path,
                source_line=row_idx + 1,
            )

        except Exception as exc:
            logger.warning(
                "Failed to parse ABC row %d in %s: %s", row_idx, file_path.name, exc
            )
            return None

    def _parse_date(self, date_str: str) -> date | None:
        """Parse a YYMMDD or YYYYMMDD date."""
        try:
            if len(date_str) == 8:
                return date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:]))
            yy = int(date_str[0:2])
            mm = int(date_str[2:4])
            dd = int(date_str[4:6])

            # Determine century: if YY > 50, assume 1900s, else 2000s
            full_year = 1900 + yy if yy > 50 else 2000 + yy
            return date(full_year, mm, dd)
        except ValueError:
            return None

    def _parse_amount(self, amount_str: str) -> tuple[Decimal | None, str]:
        """Parse amount string like '-10.00/CNY' or '14.00/CNY'."""
        try:
            # Split amount and currency
            match = re.match(r"^([-\d,.]+)/(\w+)$", amount_str)
            if not match:
                return None, "CNY"

            amount_part = match.group(1).replace(",", "")
            currency = match.group(2)

            return Decimal(amount_part), currency
        except Exception:
            return None, "CNY"

    def _extract_summary(self, file_path: Path) -> StatementSummary:
        summary = self._summaries.get(file_path)
        if summary is None:
            summary = self._summaries[file_path] = _read_summary(self._lines(file_path))
        return summary

    def post_output(
        self,
        content: str,
        result: ReconcileResult,
        context: ReconcileContext,
    ) -> str:
        """Append statement summary comparison and rebate entries to output.

        Relies on the run calling this on the instance that ran parse(), whose
        stored summaries it reads instead of reopening each file.
        """
        source_files = self.own_statement_paths(context)
        # A file statement selection dropped repeats the one adopted for its
        # cycle; reporting it would list the card twice.
        if result.coverage is not None:
            adopted = {entry.path for entry in result.coverage.adopted}
            dropped = {drop.entry.path for drop in result.coverage.drops} - adopted
            source_files = [path for path in source_files if path not in dropped]

        if not source_files:
            return content

        # Collect rebate entries and summary lines
        rebate_entries: list[str] = []
        summary_lines = ["\n; " + "=" * 60]
        summary_lines.append(f"; {self.provider_name} 账单核对")
        summary_lines.append("; " + "=" * 60)

        for file_path in sorted(source_files):
            parsed = context.parsed.get(file_path)
            txns = self.parse(file_path) if parsed is None else parsed
            summary = self._extract_summary(file_path)

            # Get card info
            card_last4 = summary.card_number[-4:] if summary.card_number else "????"
            card_display = summary.card_number or "未知卡号"

            # Calculate totals from parsed transactions (expenses only)
            expenses = sum(t.amount for t in txns if t.amount > 0)

            summary_lines.append(";")
            summary_lines.append(f"; 卡号: {card_display} (尾号 {card_last4})")
            if summary.statement_period:
                start, end = summary.statement_period
                summary_lines.append(f"; 账单周期: {start:%Y/%m/%d}-{end:%Y/%m/%d}")
            fields: list[tuple[str, object]] = [
                ("卡号", summary.card_number),
                ("账单周期", summary.statement_period),
                ("本期应还款额", summary.statement_balance),
            ]
            # The reissue never prints the 账务说明 formula.
            if file_path.suffix.lower() != ".pdf":
                fields.append(("账务说明", summary.new_charges))
            unread = [name for name, value in fields if value is None]
            if unread:
                summary_lines.append(f";   未读出: {'、'.join(unread)}")
            summary_lines.append(";")

            # Statement balance (negative in source = debt)
            stmt_balance = (
                -summary.statement_balance if summary.statement_balance else Decimal(0)
            )
            rebate = summary.rebate_offset
            rebate_used = summary.rebate_used
            new_charges = summary.new_charges

            summary_lines.append(f";   解析消费:       {expenses:>12.2f} CNY")
            if new_charges is not None:
                summary_lines.append(f";   账单消费:       {new_charges:>12.2f} CNY")
            summary_lines.append(f";   账单应还:       {stmt_balance:>12.2f} CNY")
            if rebate > 0:
                summary_lines.append(f";   刷卡金抵扣:     {rebate:>12.2f} CNY")
            if rebate < rebate_used:
                summary_lines.append(
                    f";     (本期使用刷卡金 {rebate_used:.2f}, 本期调整 {rebate:.2f})"
                )
            elif summary.adjustment is not None and summary.adjustment > rebate_used:
                summary_lines.append(
                    f";   ⚠️ 调整金额 {summary.adjustment:.2f} 超出刷卡金使用"
                    f" {rebate_used:.2f}，差额需手工核对"
                )

            # Compare parsed expenses against 本期账单金额 (new charges)
            # This is more accurate than comparing against 本期应还 which includes
            # carry-over balance, payments, refunds, and adjustments
            if new_charges is not None:
                diff = expenses - new_charges
            else:
                # Fallback: compare against stmt_balance + rebate (inaccurate
                # if there are carry-over balances or payments)
                diff = expenses - (stmt_balance + rebate)
            summary_lines.append(";")

            if stmt_balance == Decimal(0) and expenses == Decimal(0):
                summary_lines.append(";   状态: ✅ 无消费")
            elif abs(diff) < Decimal("0.01"):
                if rebate > 0 and summary.statement_period is None:
                    # The entry is dated on the cycle's last day.
                    summary_lines.append(
                        f";   状态: ✅ 平账 (刷卡金 {rebate:.2f}，"
                        "账单周期未读出，未生成分录)"
                    )
                elif rebate > 0:
                    # Check if rebate already recorded in ledger
                    rebate_entry = self._generate_rebate_entry(
                        summary, rebate, result, context
                    )
                    if rebate_entry:
                        summary_lines.append(f";   状态: ✅ 平账 (刷卡金 {rebate:.2f})")
                        rebate_entries.append(rebate_entry)
                    else:
                        summary_lines.append(
                            f";   状态: ✅ 平账 (刷卡金 {rebate:.2f} 已记录)"
                        )
                else:
                    summary_lines.append(";   状态: ✅ 平账")
            else:
                summary_lines.append(f";   差额: {diff:>+.2f} CNY")

        summary_lines.append("; " + "=" * 60)

        # Combine: rebate entries first, then summary
        output_parts = [content]
        if rebate_entries:
            output_parts.append("\n; --- 刷卡金抵扣 ---\n")
            output_parts.append("\n".join(rebate_entries))
        output_parts.append("\n".join(summary_lines) + "\n")

        return "".join(output_parts)

    def _generate_rebate_entry(
        self,
        summary: StatementSummary,
        rebate: Decimal,
        result: ReconcileResult,
        context: ReconcileContext,
    ) -> str | None:
        """Generate a beancount entry for rebate usage.

        Returns None if rebate already recorded in ledger (matched by keywords/account).
        """
        if rebate <= 0:
            return None

        card_last4 = summary.card_number[-4:] if summary.card_number else None
        if not card_last4:
            return None

        # Get provider config
        provider_config = None
        if context.config:
            provider_config = context.config.get_provider_config(self.provider_id)

        # Check if rebate already recorded in ledger
        period = summary.statement_period
        if period is None or self._rebate_exists_in_ledger(
            card_last4, rebate, period, result, provider_config
        ):
            return None

        entry_date = period[1]

        # Get liability account from config
        liability_account = f"Liabilities:Credit:ABC:{card_last4}"
        if provider_config:
            accounts = provider_config.accounts or {}
            if card_last4 in accounts:
                liability_account = accounts[card_last4]

        # Get income account from config or use default
        income_account = "Income:Rebate:ABC"
        if provider_config and provider_config.rebate_income_account:
            income_account = provider_config.rebate_income_account

        # Generate entry
        lines = [
            f'{entry_date} * "农业银行" "刷卡金抵扣 (尾号{card_last4})"',
            f"  {liability_account}  {rebate:.2f} CNY",
            f"  {income_account}",
        ]
        return "\n".join(lines)

    def _rebate_exists_in_ledger(
        self,
        card_last4: str,
        rebate: Decimal,
        period: tuple[date, date],
        result: ReconcileResult,
        provider_config: ProviderConfig | None,
    ) -> bool:
        """Check if rebate transaction already exists in ledger.

        Matches by:
        1. Keywords in narration (from rebate_keywords config)
        2. Income account (from rebate_income_account config)
        3. Amount matches (within 0.01)
        4. Account contains card_last4
        """
        # Get keywords and income account from config
        keywords: list[str] = []
        income_account: str | None = None
        if provider_config:
            keywords = provider_config.rebate_keywords or []
            income_account = provider_config.rebate_income_account

        # If no keywords or income account configured, can't detect existing rebates
        if not keywords and not income_account:
            return False

        # Check extra entries (ledger entries not matched to statement)
        for txn_posting in result.match_result.extra:
            txn, posting = txn_posting

            # Check if within statement period
            if not (period[0] <= txn.date <= period[1]):
                continue

            # Check if posting matches card (by account containing card_last4)
            if card_last4 not in posting.account:
                continue

            # Check amount matches (rebate reduces liability, so positive posting)
            posting_amount = Decimal(0)
            if posting.units and posting.units.number is not None:
                posting_amount = abs(posting.units.number)
            if posting_amount != rebate:
                continue

            # Check narration for keywords
            narration = txn.narration or ""
            payee = txn.payee or ""
            text = f"{payee} {narration}"

            if keywords and any(kw in text for kw in keywords):
                return True

            # Check if any posting uses the rebate income account
            if income_account:
                for p in txn.postings:
                    if p.account == income_account:
                        return True

        return False


def _parse_cycle(text: str) -> tuple[date, date] | None:
    match = _CYCLE_RE.fullmatch(text)
    if not match:
        return None
    y1, m1, d1, y2, m2, d2 = map(int, match.groups())
    return date(y1, m1, d1), date(y2, m2, d2)


def _compact(cell: str) -> str:
    return re.sub(r"\s+", "", cell)


def _is_header(line: list[str], template: _MailTemplate) -> bool:
    return len(line) == len(template.header) and all(
        _compact(cell).startswith(label)
        for cell, label in zip(line, template.header, strict=True)
    )


def _first_date_index(line: list[str]) -> int | None:
    """Index of a row's first cell when it and the next are dates.

    Only empty spacer cells may come before them.
    """
    for i, cell in enumerate(line[:-1]):
        if cell:
            dated = _ROW_DATE_RE.match(cell) and _ROW_DATE_RE.match(line[i + 1])
            return i if dated else None
    return None


def _read_summary(lines: list[list[str]]) -> StatementSummary:
    """Read card, cycle, 本期应还款额, 本期使用刷卡金 and the 账务说明 formula.

    Card and cycle are whole cells; each amount is the first amount cell
    after its label, which holds whether the value sits beside the label or
    in a row below it. The reissue prints neither the formula nor
    本期使用刷卡金.
    """
    cells = [cell for line in lines for cell in line]
    summary = StatementSummary(
        card_number=next((c for c in cells if _MASKED_CARD_RE.match(c)), None),
        statement_period=next(filter(None, map(_parse_cycle, cells)), None),
        statement_balance=_amount_after(cells, "本期应还款额"),
        rebate_used=_amount_after(cells, "本期使用刷卡金") or Decimal(0),
    )
    for template in _TEMPLATES:
        formula = _read_formula(lines, template.new_charges_label)
        if formula is not None:
            summary.new_charges = _formula_value(formula, template.new_charges_label)
            if template.adjustment_label:
                summary.adjustment = _formula_value(formula, template.adjustment_label)
            break
    return summary


def _amount_after(cells: list[str], label: str) -> Decimal | None:
    following = dropwhile(lambda cell: not cell.startswith(label), cells)
    return next(
        (_decimal(cell) for cell in following if _SUMMARY_AMOUNT_RE.match(cell)), None
    )


def _decimal(cell: str) -> Decimal:
    return Decimal(cell.replace(",", ""))


def _read_formula(lines: list[list[str]], anchor: str) -> list[tuple[str, str]] | None:
    """Pair each 账务说明 label with its value, or None without the formula.

    The label row holds operator cells that have no value cell; the value
    row is the first later row with one cell per label and an amount.
    """
    for i, line in enumerate(lines):
        labels = [_compact(cell) for cell in line if cell not in _FORMULA_OPERATORS]
        if not any(label.startswith(anchor) for label in labels):
            continue
        for values in lines[i + 1 :]:
            if len(values) == len(labels) and any(
                _SUMMARY_AMOUNT_RE.match(value) for value in values
            ):
                return list(zip(labels, values, strict=True))
        return None
    return None


def _formula_value(formula: list[tuple[str, str]], label: str) -> Decimal | None:
    for name, value in formula:
        if name.startswith(label) and _SUMMARY_AMOUNT_RE.match(value):
            return _decimal(value)
    return None
