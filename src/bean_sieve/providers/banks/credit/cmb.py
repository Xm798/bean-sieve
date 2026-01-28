"""China Merchants Bank (招商银行) credit card statement provider."""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ....core.types import Transaction
from ... import register_provider
from ...base import BaseProvider


@register_provider
class CMBCreditProvider(BaseProvider):
    """
    Provider for China Merchants Bank (招商银行) credit card email statements.

    Parses .eml files containing HTML statements with transaction tables.

    File format:
    - Encoding: UTF-8
    - Transaction tables have class="bgTable"
    - Row structure (9 cells):
      - [2]: 交易日 (MMDD, may be empty for repayments)
      - [3]: 记账日 (MMDD)
      - [4]: 交易描述
      - [6]: 卡号后4位
      - [7]: 入账金额

    CMB uses unified account management (按户管理), all cards share one statement.
    """

    provider_id = "cmb_credit"
    provider_name = "招商银行信用卡"
    supported_formats = [".eml"]
    filename_keywords = ["招商银行信用卡", "招行信用卡"]
    content_keywords = ["招商银行信用卡"]

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse CMB credit card email statement."""
        html = self.extract_html_from_eml(file_path)
        soup = self.parse_html(html)

        statement_period = self._extract_statement_period(html, file_path)
        statement_year, statement_month = self._extract_statement_month(html, file_path)

        transactions: list[Transaction] = []

        for table in soup.find_all("table", class_="bgTable"):
            transactions.extend(
                self._parse_table(
                    table, statement_year, statement_month, file_path, statement_period
                )
            )

        return transactions

    def _extract_statement_month(self, html: str, file_path: Path) -> tuple[int, int]:
        """Extract statement year and month from HTML or filename.

        Returns:
            Tuple of (year, month).
        """
        # Pattern: YYYY年MM月.*账单
        match = re.search(r"(\d{4})年(\d{1,2})月.*?账单", html)
        if match:
            return int(match.group(1)), int(match.group(2))

        # Try filename
        match = re.search(r"(\d{4})年(\d{1,2})月", file_path.name)
        if match:
            return int(match.group(1)), int(match.group(2))

        # Fallback to current date
        today = date.today()
        return today.year, today.month

    def _extract_statement_period(
        self, html: str, file_path: Path
    ) -> tuple[date, date] | None:
        """Extract statement period from HTML or filename.

        CMB statements typically cover one calendar month.
        """
        year, month = self._extract_statement_month(html, file_path)

        # CMB statement covers the full month
        start = date(year, month, 1)
        if month == 12:
            end = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(year, month + 1, 1) - timedelta(days=1)

        return (start, end)

    def _parse_table(
        self,
        table,
        statement_year: int,
        statement_month: int,
        file_path: Path,
        statement_period: tuple[date, date] | None,
    ) -> list[Transaction]:
        """Parse a transaction table."""
        transactions: list[Transaction] = []
        rows = table.find_all("tr")

        for row_idx, row in enumerate(rows):
            cells = row.find_all("td")
            # Only process 9-cell rows (8-cell rows are duplicates)
            if len(cells) != 9:
                continue

            txn = self._parse_row(
                cells,
                statement_year,
                statement_month,
                file_path,
                row_idx,
                statement_period,
            )
            if txn:
                transactions.append(txn)

        return transactions

    def _parse_row(
        self,
        cells,
        statement_year: int,
        statement_month: int,
        file_path: Path,
        row_idx: int,
        statement_period: tuple[date, date] | None,
    ) -> Transaction | None:
        """Parse a single transaction row (9 cells).

        Structure:
        - [2]: 交易日 (MMDD, may be empty for repayments)
        - [3]: 记账日 (MMDD)
        - [4]: 交易描述
        - [6]: 卡号后4位
        - [7]: 入账金额
        """
        try:
            # Extract posting date (always present)
            post_date_str = self.clean_text(cells[3].get_text())
            if not re.match(r"^\d{4}$", post_date_str):
                return None

            post_month = int(post_date_str[:2])
            post_day = int(post_date_str[2:])
            post_year = self._determine_year(
                post_month, statement_year, statement_month
            )
            post_date = date(post_year, post_month, post_day)

            # Extract transaction date (may be empty)
            trans_date_str = self.clean_text(cells[2].get_text())
            trans_date = None
            if re.match(r"^\d{4}$", trans_date_str):
                trans_month = int(trans_date_str[:2])
                trans_day = int(trans_date_str[2:])
                trans_year = self._determine_year(
                    trans_month, statement_year, statement_month
                )
                trans_date = date(trans_year, trans_month, trans_day)

            # Use transaction date if available, otherwise posting date
            txn_date = trans_date if trans_date else post_date

            # Extract description
            description = self.clean_text(cells[4].get_text())
            if not description:
                return None

            # Extract amount
            amount_str = self.clean_text(cells[7].get_text())
            amount_str = amount_str.replace(",", "").replace("\xa0", "")
            if not amount_str:
                return None

            amount = Decimal(amount_str)

            # Extract card last 4 digits
            card_last4 = self.clean_text(cells[6].get_text())
            if not re.match(r"^\d{4}$", card_last4):
                card_last4 = None

            metadata: dict = {}
            if trans_date and trans_date != post_date:
                metadata["posting_date"] = post_date.isoformat()

            return Transaction(
                date=txn_date,
                amount=amount,
                currency="CNY",
                description=description,
                card_last4=card_last4,
                provider=self.provider_id,
                source_file=file_path,
                source_line=row_idx + 1,
                statement_period=statement_period,
                metadata=metadata,
            )
        except (ValueError, IndexError, AttributeError, InvalidOperation):
            return None

    def _determine_year(
        self, trans_month: int, statement_year: int, statement_month: int
    ) -> int:
        """Determine the year for a transaction based on statement month.

        For December statements, transactions in January belong to next year.
        For January statements, transactions in December belong to previous year.
        """
        if statement_month == 12 and trans_month == 1:
            return statement_year + 1
        if statement_month == 1 and trans_month == 12:
            return statement_year - 1
        return statement_year
