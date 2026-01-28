"""China Construction Bank (建设银行) credit card statement provider."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from ....core.types import Transaction
from ... import register_provider
from ...base import BaseProvider


@register_provider
class CCBCreditProvider(BaseProvider):
    """
    Provider for China Construction Bank (建设银行) credit card email statements.

    Parses .eml files containing HTML statements with transaction tables.

    File format:
    - Format: EML with HTML content
    - Transaction table: Table 16 with columns [交易日, 银行记账日, 卡号后四位, 交易描述, CNY, 金额, CNY, 金额]
    - Amount sign: Negative amounts indicate payments/refunds
    - Statement period: Found in Table 8 (e.g., "2025/11/24-2025/12/23")
    """

    provider_id = "ccb_credit"
    provider_name = "建设银行信用卡"
    supported_formats = [".eml"]
    filename_keywords = ["建设银行", "中国建设银行"]
    content_keywords = ["中国建设银行信用卡", "龙卡信用卡对账单"]
    per_card_statement = True  # CCB sends separate statements per card

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse CCB credit card email statement."""
        html = self.extract_html_from_eml(file_path)
        soup = self.parse_html(html)

        statement_period = self._extract_statement_period(soup)
        transactions: list[Transaction] = []
        row_counter = 0

        # Find the transaction table (contains "【交易明细】")
        tables = soup.find_all("table")
        for table in tables:
            first_row = table.find("tr")
            if not first_row:
                continue

            first_text = first_row.get_text(strip=True)
            if "【交易明细】" not in first_text:
                continue

            # Found the transaction table, parse rows
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all("td")
                if len(cells) < 6:
                    continue

                cell_texts = [c.get_text(strip=True) for c in cells]

                # Transaction rows have date format YYYY-MM-DD in first column
                if not self._is_transaction_date(cell_texts[0]):
                    continue

                row_counter += 1
                txn = self._parse_row(
                    cell_texts, file_path, row_counter, statement_period
                )
                if txn:
                    transactions.append(txn)

        return transactions

    def _extract_statement_period(self, soup) -> tuple[date, date] | None:
        """Extract statement period (e.g., '2025/11/24-2025/12/23')."""
        text = soup.get_text()
        match = re.search(r"(\d{4})/(\d{2})/(\d{2})-(\d{4})/(\d{2})/(\d{2})", text)
        if match:
            start = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            end = date(int(match.group(4)), int(match.group(5)), int(match.group(6)))
            return (start, end)
        return None

    def _is_transaction_date(self, text: str) -> bool:
        """Check if text is a date in YYYY-MM-DD format."""
        return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", text))

    def _parse_row(
        self,
        cells: list[str],
        file_path: Path,
        row_idx: int,
        statement_period: tuple[date, date] | None,
    ) -> Transaction | None:
        """Parse a single transaction row.

        Row format: [交易日, 银行记账日, 卡号后四位, 交易描述, CNY, 金额, CNY, 金额]
        or: [交易日, 银行记账日, 卡号后四位, 交易描述, 交易币/金额, 结算币/金额]
        """
        try:
            trans_date_str = cells[0]  # YYYY-MM-DD
            post_date_str = cells[1]  # YYYY-MM-DD
            card_last4 = cells[2]  # e.g., "0800"
            description = cells[3]

            # Parse dates
            trans_date = self._parse_date(trans_date_str)
            post_date = self._parse_date(post_date_str)

            # Parse amount - can be in different positions depending on table structure
            # Format 1: [..., CNY, 金额, CNY, 金额] (8 cells)
            # Format 2: [..., CNY金额, CNY金额] (6 cells with combined currency+amount)
            amount, currency = self._parse_amount_from_cells(cells[4:])
            if amount is None:
                return None

            return Transaction(
                date=trans_date,
                post_date=post_date,
                amount=amount,
                currency=currency,
                description=description,
                card_last4=card_last4,
                provider=self.provider_id,
                source_file=file_path,
                source_line=row_idx,
                statement_period=statement_period,
            )
        except (IndexError, ValueError):
            return None

    def _parse_date(self, date_str: str) -> date:
        """Parse date string in YYYY-MM-DD format."""
        year, month, day = map(int, date_str.split("-"))
        return date(year, month, day)

    def _parse_amount_from_cells(self, cells: list[str]) -> tuple[Decimal | None, str]:
        """Parse amount from remaining cells.

        Handles two formats:
        1. [CNY, 金额, CNY, 金额] - currency and amount in separate cells
        2. [CNY金额, CNY金额] - combined currency+amount
        """
        if not cells:
            return None, "CNY"

        # Try format 1: separate currency and amount cells
        if len(cells) >= 2 and cells[0] in ("CNY", "USD", "EUR"):
            currency = cells[0]
            amount_str = cells[1]
            return self._parse_amount(amount_str), currency

        # Try format 2: combined currency+amount (e.g., "CNY2,989.70")
        first_cell = cells[0]
        match = re.match(r"([A-Z]{3})([\d,.-]+)", first_cell)
        if match:
            currency = match.group(1)
            amount_str = match.group(2)
            return self._parse_amount(amount_str), currency

        return None, "CNY"

    def _parse_amount(self, amount_str: str) -> Decimal | None:
        """Parse amount string, handling commas and negative signs."""
        try:
            # Remove commas and whitespace
            cleaned = amount_str.replace(",", "").strip()
            return Decimal(cleaned)
        except Exception:
            return None
