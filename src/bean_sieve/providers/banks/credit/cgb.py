"""CGB credit card statement provider."""

from __future__ import annotations

import logging
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple

from ....core.coverage import Coverage
from ....core.metadata_keys import ORIGINAL_AMOUNT, ORIGINAL_CURRENCY
from ....core.types import Transaction
from ... import register_provider
from ..._pdf import pdf_lines
from ..._tabular import CURRENCY_CODES, currency_code
from ...base import BaseProvider

logger = logging.getLogger(__name__)

# Issuer name written as escapes
_ISSUER_SHORT = "\u5e7f\u53d1"
_ISSUER = f"{_ISSUER_SHORT}\u94f6\u884c"

_DATE = r"\d{4}/\d{2}/\d{2}"
_AMOUNT = r"[-\d,.]+"
_CJK = r"[\u4e00-\u9fff]+"
_KNOWN_CURRENCY = "|".join(
    map(re.escape, sorted(CURRENCY_CODES, key=len, reverse=True))
)

_CYCLE = re.compile(rf"账单周期[:：]?\s*({_DATE})\s*[-至]\s*({_DATE})")
# 卡号末四位 本期账单金额 最低还款额 最后还款日 入账货币 ...; a card that owes
# nothing prints words in place of the two amounts.
_CARD_CURRENCY = re.compile(rf"(?<![\d.,/*])(\d{{4}})\s+\S+\s+\S+\s+{_DATE}\s+({_CJK})")
_ROW_START = re.compile(rf"(?={_DATE}\s+{_DATE}\s+\()")
_POSTING_CURRENCY_COLUMN = re.compile(r"入账金额\s+入账货币")


_ROW_HEAD = re.compile(rf"({_DATE})\s+({_DATE})\s+\(([^)]+)\)")


def _amounts_pattern(currency: str) -> re.Pattern[str]:
    # 交易金额 交易货币 入账金额 [入账货币]
    return re.compile(
        rf"(?<!\S)({_AMOUNT})\s+({currency})\s+({_AMOUNT})(?:\s+({_CJK}))?"
    )


# The first pattern keeps a summary's "number word number" in the summary;
# the second reads a row in an unknown currency so it can be reported.
_AMOUNTS_PATTERNS = (_amounts_pattern(_KNOWN_CURRENCY), _amounts_pattern(_CJK))
_INTEREST = re.compile(rf"({_CJK}利息)\s+({_AMOUNT})\s+({_CJK})\s+({_AMOUNT})")
_INTEREST_LABEL = re.compile(rf"{_CJK}利息\s*$")


def _row_amounts(chunk: str, start: int) -> re.Match[str] | None:
    """Find a row's amount cells: the later of each pattern's last match."""
    candidates = [m for p in _AMOUNTS_PATTERNS if (m := _last_on_row(p, chunk, start))]
    # A known currency wins a tie; an unknown one later on the row is the
    # row's own and is reported downstream.
    return max(candidates, key=lambda m: m.end(3), default=None)


def _last_on_row(
    pattern: re.Pattern[str], chunk: str, start: int
) -> re.Match[str] | None:
    """Return the last match of pattern reached along the row.

    A summary may hold "number currency number" itself, so a later match
    takes over while the text between them stays on the row: text, then at
    most one line break, since some mails print each cell on a line of its
    own. The notes after a card's last row hold such triples too and carry
    no fixed label to stop at, so a gap running onto a later line of text
    ends the row, as does an interest line printed on the row's own line.
    """
    found: re.Match[str] | None = None
    end = start
    for match in pattern.finditer(chunk, start):
        text, line_break, rest = chunk[end : match.start()].partition("\n")
        if rest.strip() or "\n" in rest:
            break
        if found and _INTEREST_LABEL.search(text):
            break
        # A match carrying its 入账货币 holds every cell of the row, so a
        # match on a later line belongs to whatever follows the row.
        if found and found[4] and line_break:
            break
        found, end = match, match.end(3)
    return found


class _Row(NamedTuple):
    """One row's cells; posting_currency is None when the row omits 入账货币."""

    date: date
    post_date: date
    description: str
    amount: str
    currency_name: str
    posting_amount: str
    posting_currency: str | None
    metadata: dict[str, Any]


@register_provider
class CGBCreditProvider(BaseProvider):
    """
    Provider for CGB credit card statements.

    Two formats print the same text:
    - .eml: the mailed statement, base64-encoded GBK HTML
    - .pdf: the reissued statement, a text PDF whose issuer name is only in
      its logo image

    Layout:
    - Statement period: 账单周期:YYYY/MM/DD-YYYY/MM/DD (the PDF prints
      "账单周期 YYYY/MM/DD - YYYY/MM/DD", mails of 2024 and earlier
      "本期账单周期：YYYY/MM/DD至YYYY/MM/DD")
    - Account table: one row per card, its billing currency (入账货币) fifth
    - Card sections: 卡号：6200********1234 followed by transaction table
    - Transaction row: 交易日期 入账日期 (类型)摘要 金额 货币 入账金额 入账货币;
      mails of 2024 and earlier leave 入账货币 out and print the card's interest
      on an undated line after its rows: 消费利息 金额 货币 入账金额
    - A foreign-currency card bills in its own currency (e.g. 美元) and lists
      spending in a third currency (e.g. 港币) with the original amount

    Transaction types:
    - (消费): spending, positive amount
    - (还款): payment/refund, negative amount
    - (赠送): bonus/reward, negative amount
    """

    provider_id = "cgb_credit"
    provider_name = f"{_ISSUER}信用卡"
    supported_formats = [".eml", ".pdf"]
    filename_keywords = [f"{_ISSUER_SHORT}信用卡", _ISSUER]
    # 发现精彩 is the issuer's app, which the PDF's notes name.
    content_keywords = [f"{_ISSUER}信用卡", f"{_ISSUER_SHORT}信用卡", "发现精彩"]
    per_card_statement = (
        True  # CGB sends combined statement but needs per-card tracking
    )
    coverage_priority = ("statement", "reissue")

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse a CGB credit card statement, mailed or reissued."""
        if file_path.suffix.lower() == ".pdf":
            # Joining a line's cells with spaces gives the mail's text:
            # "date date (type)summary amount currency ...".
            text = "\n".join(" ".join(line) for line in pdf_lines(file_path))
        else:
            html = self.extract_html_from_eml(file_path)
            text = self.parse_html(html).get_text()

        statement_period = self._extract_statement_period(text)
        head, *sections = re.split(r"(卡号：\d{4}\*{8}\d{4})", text)
        # Without that column, a word after 入账金额 is whatever follows the row.
        posting_column = bool(_POSTING_CURRENCY_COLUMN.search(text))
        # A dual-currency card has one account-table row per currency.
        card_currencies: dict[str, list[str]] = {}
        for card, name in _CARD_CURRENCY.findall(head):
            codes = card_currencies.setdefault(card, [])
            if (code := currency_code(name)) and code not in codes:
                codes.append(code)
        transactions: list[Transaction] = []
        for header, section in zip(sections[::2], sections[1::2], strict=True):
            card = header[-4:]
            transactions.extend(
                self._parse_card_section(
                    section,
                    card,
                    card_currencies.get(card, []),
                    posting_column,
                    statement_period,
                    file_path,
                )
            )
        for line, txn in enumerate(transactions, 1):
            txn.source_line = line

        if statement_period is None:
            logger.warning(
                "%s: no statement cycle found; the file covers no period",
                file_path.name,
            )
        self.stamp_cycle(transactions, statement_period)
        return transactions

    def coverage(
        self, file_path: Path, transactions: list[Transaction]
    ) -> list[Coverage]:
        """Cover each card for the printed cycle; the mail outranks its reissue."""
        source = "reissue" if file_path.suffix.lower() == ".pdf" else "statement"
        return self.cycle_coverages(transactions, source=source)

    def _extract_statement_period(self, text: str) -> tuple[date, date] | None:
        match = _CYCLE.search(text)
        if match is None:
            return None
        try:
            return (self._parse_date(match[1]), self._parse_date(match[2]))
        except ValueError:
            return None

    def _parse_card_section(
        self,
        section: str,
        card_last4: str,
        card_currencies: list[str],
        posting_column: bool,
        statement_period: tuple[date, date] | None,
        file_path: Path,
    ) -> list[Transaction]:
        """Parse one card's dated rows and its undated interest lines.

        Each row is matched within its own chunk, so a row whose cells fail to
        match cannot run on into the next row.
        """
        rows: list[_Row] = []
        for chunk in _ROW_START.split(section):
            row_end = 0
            head = _ROW_HEAD.match(chunk)
            if head:
                amounts = _row_amounts(chunk, head.end())
                if amounts is None:
                    logger.warning(
                        "%s: skipped the (%s) row of %s on card %s; its amount"
                        " and currency cells are unreadable",
                        file_path.name,
                        head.group(3),
                        head.group(1),
                        card_last4,
                    )
                else:
                    trans_date, post_date, trans_type = head.group(1, 2, 3)
                    summary = chunk[head.end() : amounts.start()]
                    amount, currency_name, posting_amount, posting_currency = (
                        amounts.group(1, 2, 3, 4)
                    )
                    if currency_code(posting_currency) is None and not posting_column:
                        posting_currency = None
                    row_end = amounts.end() if posting_currency else amounts.end(3)
                    description = f"({trans_type}){summary.strip()}"
                    try:
                        rows.append(
                            _Row(
                                date=self._parse_date(trans_date),
                                post_date=self._parse_date(post_date),
                                description=description,
                                amount=amount,
                                currency_name=currency_name,
                                posting_amount=posting_amount,
                                posting_currency=posting_currency,
                                metadata={
                                    "original_date": trans_date,
                                    "trans_type": trans_type,
                                },
                            )
                        )
                    except ValueError:
                        logger.warning(
                            "%s: skipped %s of card %s; %s or %s is not a date",
                            file_path.name,
                            description,
                            card_last4,
                            trans_date,
                            post_date,
                        )
            for interest in _INTEREST.finditer(chunk, row_end):
                if statement_period is None:
                    logger.warning(
                        "%s: skipped %s of card %s; no statement cycle dates it",
                        file_path.name,
                        interest.group(1),
                        card_last4,
                    )
                    continue
                name, amount, currency_name, posting_amount = interest.group(1, 2, 3, 4)
                cycle_end = statement_period[1]
                rows.append(
                    _Row(
                        date=cycle_end,
                        post_date=cycle_end,
                        description=name,
                        amount=amount,
                        currency_name=currency_name,
                        posting_amount=posting_amount,
                        posting_currency=None,
                        metadata={},
                    )
                )

        transactions: list[Transaction] = []
        skipped = 0
        for row in rows:
            currency = currency_code(row.posting_currency)
            if row.posting_currency and currency is None:
                logger.warning(
                    "%s: unknown 入账货币 %s in %s of card %s; billed in the card's"
                    " currency from the account table",
                    file_path.name,
                    row.posting_currency,
                    row.description,
                    card_last4,
                )
            if currency is None and len(card_currencies) == 1:
                currency = card_currencies[0]
            elif currency is None:
                # A dual-currency card bills each row in its 交易货币's account.
                original = currency_code(row.currency_name)
                currency = original if original in card_currencies else None
            if currency is None:
                skipped += 1
            elif txn := self._build_transaction(file_path, card_last4, currency, row):
                transactions.append(txn)
        if skipped:
            logger.warning(
                "%s: skipped %d rows of card %s; no billing currency fits them"
                " (account table lists: %s)",
                file_path.name,
                skipped,
                card_last4,
                ", ".join(card_currencies) or "none",
            )
        return transactions

    def _build_transaction(
        self, file_path: Path, card_last4: str, currency: str, row: _Row
    ) -> Transaction | None:
        amount = self._parse_amount(row.posting_amount)
        if amount is None:
            return None

        metadata = dict(row.metadata)
        original_currency = currency_code(row.currency_name)
        if original_currency is None:
            logger.warning(
                "%s: unknown currency %s in %s of card %s; original amount dropped",
                file_path.name,
                row.currency_name,
                row.description,
                card_last4,
            )
        elif original_currency != currency:
            original = self._parse_amount(row.amount)
            if original is not None:
                metadata[ORIGINAL_AMOUNT] = original
                metadata[ORIGINAL_CURRENCY] = original_currency

        return Transaction(
            date=row.date,
            post_date=row.post_date,
            amount=amount,
            currency=currency,
            description=row.description,
            card_last4=card_last4,
            provider=self.provider_id,
            source_file=file_path,
            metadata=metadata,
        )

    def _parse_date(self, date_str: str) -> date:
        """Parse date from YYYY/MM/DD format."""
        parts = date_str.split("/")
        return date(int(parts[0]), int(parts[1]), int(parts[2]))

    def _parse_amount(self, amount_str: str) -> Decimal | None:
        """Parse amount string, handling commas and negative values."""
        try:
            cleaned = amount_str.replace(",", "")
            return Decimal(cleaned)
        except Exception:
            return None
