"""Agricultural Bank of China (农业银行) credit card statement provider."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from ....config.schema import ProviderConfig
from ....core.types import ReconcileContext, ReconcileResult, Transaction
from ... import register_provider
from ...base import BaseProvider


@dataclass
class StatementSummary:
    """Summary information extracted from ABC statement."""

    card_number: str | None = None  # 卡号 (masked, e.g., 620000******1234)
    statement_cycle: str | None = None  # 账单周期
    statement_balance: Decimal | None = None  # 本期应还款额
    new_charges: Decimal | None = None  # 本期账单金额 (from 账务说明)
    rebate_used: Decimal | None = None  # 本期使用刷卡金


@register_provider
class ABCCreditProvider(BaseProvider):
    """
    Provider for Agricultural Bank of China (农业银行) credit card email statements.

    File format:
    - Format: EML email with HTML content
    - Columns: 交易日期, 入账日期, 卡号后四位, 交易描述, 交易金额/币种, 入账金额/币种
    - Date format: YYMMDD (e.g., "251103" = 2025-11-03)
    - Amount sign: Settlement amount negative for expenses (支出为-)
    """

    provider_id = "abc_credit"
    provider_name = "农业银行信用卡"
    supported_formats = [".eml"]
    filename_keywords = ["农业银行", "金穗信用卡"]
    content_keywords = ["金穗信用卡电子对账单"]
    per_card_statement = True  # ABC sends separate statements per card

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse ABC credit card email statement."""
        html = self.extract_html_from_eml(file_path)
        soup = self.parse_html(html)

        # Extract statement period for per-card statement support
        statement_period = self._extract_statement_period(soup)

        return self._parse_transactions(soup, file_path, statement_period)

    def _extract_statement_period(self, soup) -> tuple[date, date] | None:
        """Extract statement period (e.g., '2025/10/24-2025/11/23')."""
        for span in soup.find_all("span"):
            text = span.get_text(strip=True)
            match = re.search(r"(\d{4})/(\d{2})/(\d{2})-(\d{4})/(\d{2})/(\d{2})", text)
            if match:
                start = date(
                    int(match.group(1)), int(match.group(2)), int(match.group(3))
                )
                end = date(
                    int(match.group(4)), int(match.group(5)), int(match.group(6))
                )
                return (start, end)
        return None

    def _parse_transactions(
        self,
        soup,
        file_path: Path,
        statement_period: tuple[date, date] | None,
    ) -> list[Transaction]:
        """Parse transactions from HTML tables."""
        transactions = []
        line_num = 0

        # Find transaction tables - use recursive=False to avoid duplicates
        # from nested tables
        for table in soup.find_all("table"):
            # Get only direct tr children (not nested)
            direct_rows = table.find_all("tr", recursive=False)
            for row in direct_rows:
                cells = row.find_all("td", recursive=False)
                if len(cells) != 6:
                    continue

                line_num += 1
                txn = self._parse_transaction_row(
                    cells, file_path, line_num, statement_period
                )
                if txn:
                    transactions.append(txn)

        return transactions

    def _parse_transaction_row(
        self,
        cells,
        file_path: Path,
        row_idx: int,
        statement_period: tuple[date, date] | None,
    ) -> Transaction | None:
        """Parse a single transaction row."""
        try:
            # Extract cell contents
            trans_date_str = self.clean_text(cells[0].get_text())
            # post_date_str = self.clean_text(cells[1].get_text())  # Not used
            card_last4 = self.clean_text(cells[2].get_text())
            description = self.clean_text(cells[3].get_text())
            # trans_amount = self.clean_text(cells[4].get_text())  # Original amount
            settle_amount = self.clean_text(cells[5].get_text())  # Settlement amount

            # Validate date format (YYMMDD)
            if not re.match(r"^\d{6}$", trans_date_str):
                return None

            # Parse date (YYMMDD -> YYYY-MM-DD)
            trans_date = self._parse_date(trans_date_str)
            if not trans_date:
                return None

            # Validate card number (4 digits)
            if not re.match(r"^\d{4}$", card_last4):
                return None

            # Parse amount and currency (e.g., "-10.00/CNY" or "14.00/CNY")
            amount, currency = self._parse_amount(settle_amount)
            if amount is None:
                return None

            # Negate: source uses negative for expenses, we use positive
            amount = -amount

            return Transaction(
                date=trans_date,
                amount=amount,
                currency=currency,
                description=description,
                card_last4=card_last4,
                provider=self.provider_id,
                source_file=file_path,
                source_line=row_idx + 1,
                statement_period=statement_period,
            )

        except Exception:
            return None

    def _parse_date(self, date_str: str) -> date | None:
        """Parse YYMMDD format to date object."""
        try:
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
        """Extract statement summary information."""
        html = self.extract_html_from_eml(file_path)
        soup = self.parse_html(html)
        summary = StatementSummary()

        for span in soup.find_all("span"):
            text = span.get_text(strip=True)

            # 卡号 (e.g., 620000******1234)
            if re.match(r"^\d{6}\*{6}\d{4}$", text):
                summary.card_number = text

            # 账单周期
            if re.match(r"\d{4}/\d{2}/\d{2}-\d{4}/\d{2}/\d{2}", text):
                summary.statement_cycle = text

            # 本期应还款额
            if "本期应还款额" in text:
                parent = span.find_parent("tr")
                if parent:
                    next_row = parent.find_next_sibling("tr")
                    if next_row:
                        for cell in next_row.find_all("td"):
                            cell_text = cell.get_text(strip=True)
                            if re.match(r"^-?[\d,]+\.\d{2}$", cell_text):
                                summary.statement_balance = Decimal(
                                    cell_text.replace(",", "")
                                )
                                break

            # 本期账单金额 (from 账务说明 formula table)
            # Labels and values are in sibling tables; labels include operator
            # cells (-, =, +) that have no corresponding value cell
            if "本期账单金额" in text and summary.new_charges is None:
                td = span.find_parent("td")
                tr = td.find_parent("tr") if td else None
                label_table = tr.find_parent("table") if tr else None
                if label_table:
                    value_table = label_table.find_next_sibling("table")
                    if value_table and tr:
                        label_cells = tr.find_all("td", recursive=False)
                        # Find index of target label, skipping operator cells
                        value_idx = 0
                        for cell in label_cells:
                            cell_text = cell.get_text(strip=True)
                            if "本期账单金额" in cell_text:
                                break
                            if cell_text not in ("-", "=", "+"):
                                value_idx += 1
                        value_row = value_table.find("tr")
                        if value_row:
                            vals = value_row.find_all("td", recursive=False)
                            if value_idx < len(vals):
                                val_text = vals[value_idx].get_text(strip=True)
                                if re.match(r"^[\d,]+\.\d{2}$", val_text):
                                    summary.new_charges = Decimal(
                                        val_text.replace(",", "")
                                    )

            # 本期使用刷卡金
            if "本期使用刷卡金" in text:
                parent_td = span.find_parent("td")
                if parent_td:
                    next_td = parent_td.find_next_sibling("td")
                    if next_td:
                        amount_text = next_td.get_text(strip=True)
                        if re.match(r"^[\d,]+\.\d{2}$", amount_text):
                            summary.rebate_used = Decimal(amount_text.replace(",", ""))

        return summary

    def post_output(
        self,
        content: str,
        result: ReconcileResult,
        context: ReconcileContext,
    ) -> str:
        """Append statement summary comparison and rebate entries to output."""
        # Get source files from context
        source_files = [
            p for p in context.statement_paths if p.suffix.lower() == ".eml"
        ]

        # Filter to only files that this provider can handle
        source_files = [p for p in source_files if self.can_handle(p)]

        if not source_files:
            return content

        # Collect rebate entries and summary lines
        rebate_entries: list[str] = []
        summary_lines = ["\n; " + "=" * 60]
        summary_lines.append(f"; {self.provider_name} 账单核对")
        summary_lines.append("; " + "=" * 60)

        for file_path in sorted(source_files):
            summary = self._extract_summary(file_path)
            txns = self.parse(file_path)

            # Get card info
            card_last4 = summary.card_number[-4:] if summary.card_number else "????"
            card_display = summary.card_number or "未知卡号"

            # Calculate totals from parsed transactions (expenses only)
            expenses = sum(t.amount for t in txns if t.amount > 0)

            summary_lines.append(";")
            summary_lines.append(f"; 卡号: {card_display} (尾号 {card_last4})")
            if summary.statement_cycle:
                summary_lines.append(f"; 账单周期: {summary.statement_cycle}")
            summary_lines.append(";")

            # Statement balance (negative in source = debt)
            stmt_balance = (
                -summary.statement_balance if summary.statement_balance else Decimal(0)
            )
            rebate = summary.rebate_used or Decimal(0)
            new_charges = summary.new_charges

            summary_lines.append(f";   解析消费:       {expenses:>12.2f} CNY")
            if new_charges is not None:
                summary_lines.append(f";   账单消费:       {new_charges:>12.2f} CNY")
            summary_lines.append(f";   账单应还:       {stmt_balance:>12.2f} CNY")
            if rebate > 0:
                summary_lines.append(f";   刷卡金抵扣:     {rebate:>12.2f} CNY")

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
                if rebate > 0:
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
        if self._rebate_exists_in_ledger(
            card_last4, rebate, summary.statement_cycle, result, provider_config
        ):
            return None

        # Get statement end date for the entry date
        entry_date = date.today()
        if summary.statement_cycle:
            match = re.search(r"(\d{4})/(\d{2})/(\d{2})$", summary.statement_cycle)
            if match:
                entry_date = date(
                    int(match.group(1)), int(match.group(2)), int(match.group(3))
                )

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
        statement_cycle: str | None,
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

        # Parse statement period for date range check
        period_start, period_end = None, None
        if statement_cycle:
            match = re.search(
                r"(\d{4})/(\d{2})/(\d{2})-(\d{4})/(\d{2})/(\d{2})", statement_cycle
            )
            if match:
                period_start = date(
                    int(match.group(1)), int(match.group(2)), int(match.group(3))
                )
                period_end = date(
                    int(match.group(4)), int(match.group(5)), int(match.group(6))
                )

        # Check extra entries (ledger entries not matched to statement)
        for txn_posting in result.match_result.extra:
            txn, posting = txn_posting

            # Check if within statement period
            if (
                period_start
                and period_end
                and not (period_start <= txn.date <= period_end)
            ):
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
