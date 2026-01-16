"""Bank of Communications (交通银行) credit card statement provider."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from ....core.types import Transaction
from ... import register_provider
from ...base import BaseProvider


@register_provider
class BOCOMCreditProvider(BaseProvider):
    """
    Provider for Bank of Communications (交通银行) credit card email statements.

    Parses .eml files containing HTML statements with transaction tables.

    Transaction sections:
    - 还款、退货、费用返还明细: payments/refunds (negative amounts)
    - 消费、取现、其他费用明细: spending/cash advances (positive amounts)
    """

    provider_id = "bocom_credit"
    provider_name = "交通银行信用卡"
    supported_formats = [".eml"]
    filename_keywords = ["交通银行"]
    content_keywords = ["交通银行信用卡电子账单"]
    per_card_statement = True  # BOCOM sends separate statements per card

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse BOCOM credit card email statement."""
        html = self.extract_html_from_eml(file_path)
        soup = self.parse_html(html)

        # Extract statement period (e.g., 2025/10/14-2025/11/13)
        statement_period = self._extract_statement_period(soup)

        transactions: list[Transaction] = []
        tables = soup.find_all("table")
        row_counter = 0

        for table in tables:
            # Only process leaf tables (no nested tables)
            if table.find_all("table"):
                continue

            # Find transaction rows in this table
            rows = table.find_all("tr")
            trans_rows = []
            for row in rows:
                cells = row.find_all("td")
                if len(cells) >= 6:
                    cell_texts = [c.get_text(strip=True) for c in cells]
                    if self._is_date(cell_texts[1]):
                        trans_rows.append(cell_texts)

            if not trans_rows:
                continue

            # Determine section by checking previous sibling rows in parent
            section = self._detect_section(table)

            # Parse each transaction row
            for cell_texts in trans_rows:
                row_counter += 1
                txn = self._parse_row(
                    cell_texts, section, file_path, row_counter, statement_period
                )
                if txn:
                    transactions.append(txn)

        return transactions

    def _detect_section(self, table) -> str | None:
        """Detect section type by checking previous sibling rows."""
        parent_row = table.find_parent("tr")
        if not parent_row:
            return None

        for prev_row in parent_row.find_previous_siblings("tr"):
            text = prev_row.get_text()
            if "消费、取现、其他费用明细" in text:
                return "spending"
            if "还款、退货、费用返还明细" in text:
                return "payment"
        return None

    def _extract_statement_period(self, soup) -> tuple[date, date] | None:
        """Extract statement period (e.g., '2025/10/14-2025/11/13')."""
        text = soup.get_text()
        match = re.search(r"(\d{4})/(\d{2})/(\d{2})-(\d{4})/(\d{2})/(\d{2})", text)
        if match:
            start = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            end = date(int(match.group(4)), int(match.group(5)), int(match.group(6)))
            return (start, end)
        return None

    def _is_date(self, text: str) -> bool:
        """Check if text is a date in MM/DD format."""
        return bool(re.match(r"^\d{2}/\d{2}$", text))

    def _parse_row(
        self,
        cells: list[str],
        section: str | None,
        file_path: Path,
        row_idx: int,
        statement_period: tuple[date, date] | None,
    ) -> Transaction | None:
        """Parse a single transaction row."""
        try:
            trans_date_str = cells[1]  # MM/DD
            post_date_str = cells[2]  # MM/DD
            card_last4 = cells[3]  # e.g., "1234"
            description = cells[4]
            amount_str = cells[5]  # e.g., "CNY9974.12" or "USD100.00"

            # Parse dates using statement period to determine correct year
            trans_date = self._parse_date_with_period(trans_date_str, statement_period)
            post_date = self._parse_date_with_period(post_date_str, statement_period)

            # Parse amount and currency
            amount, currency = self._parse_amount(amount_str)
            if amount is None:
                return None

            # payment section = payments to card = negative (income for cardholder)
            amount = -abs(amount) if section == "payment" else abs(amount)

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
                statement_period=statement_period,
                metadata={
                    "original_date": trans_date_str,
                    "section": section or "unknown",
                },
            )
        except (IndexError, ValueError):
            return None

    def _parse_date_with_period(
        self, date_str: str, statement_period: tuple[date, date] | None
    ) -> date:
        """Parse MM/DD date string using statement period to determine year.

        For cross-year periods (e.g., 12/14-1/13), uses start year for months
        >= start month, and end year for months <= end month.
        """
        month, day = map(int, date_str.split("/"))

        if not statement_period:
            return date(date.today().year, month, day)

        start, end = statement_period
        # For cross-year periods, determine which year this month belongs to
        if start.year != end.year:
            # Cross-year: months >= start month use start year
            if month >= start.month:
                return date(start.year, month, day)
            else:
                return date(end.year, month, day)
        else:
            # Same year
            return date(start.year, month, day)

    def _parse_amount(self, amount_str: str) -> tuple[Decimal | None, str]:
        """Parse amount string like 'CNY9974.12' or 'USD100.00'."""
        match = re.match(r"([A-Z]{3})([\d,]+\.?\d*)", amount_str)
        if not match:
            return None, "CNY"

        currency = match.group(1)
        amount_num = match.group(2).replace(",", "")

        try:
            return Decimal(amount_num), currency
        except Exception:
            return None, currency
