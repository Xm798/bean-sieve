"""China Minsheng Bank (民生银行) credit card statement provider."""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ....core.types import Transaction
from ... import register_provider
from ...base import BaseProvider


@register_provider
class CMBCCreditProvider(BaseProvider):
    """
    Provider for China Minsheng Bank (民生银行) credit card email statements.

    Parses .eml files containing HTML statements with transaction tables.

    File format:
    - Encoding: gb18030
    - Transaction rows in loopBand3 section
    - Date format: MM/DD
    - Columns: 交易日, 记账日, 交易摘要, 交易金额, 卡号末四位

    CMBC uses unified account management (按户管理), all cards share one statement.
    """

    provider_id = "cmbc_credit"
    provider_name = "民生银行信用卡"
    supported_formats = [".eml"]
    filename_keywords = ["民生信用卡", "民生银行信用卡"]
    content_keywords = ["民生银行", "cmbc.com.cn"]

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse CMBC credit card email statement."""
        html = self.extract_html_from_eml(file_path)
        soup = self.parse_html(html)

        statement_period = self._extract_statement_period(html, file_path)
        statement_year, statement_month = self._extract_statement_month(html, file_path)

        transactions: list[Transaction] = []

        # Find transaction rows - look for loopBand3 section which contains transactions
        # Each transaction row has: 交易日, 记账日, 交易摘要, 交易金额, 卡号末四位
        loop_band = soup.find("span", id="loopBand3")
        if loop_band:
            transactions.extend(
                self._parse_loop_band(
                    loop_band,
                    statement_year,
                    statement_month,
                    file_path,
                    statement_period,
                )
            )

        return transactions

    def _extract_statement_date(self, html: str, file_path: Path) -> date:
        """Extract the statement date (账单日) from HTML or filename.

        Returns:
            The statement date.
        """
        # Try finding statement date in HTML: 本期账单日 ... YYYY/MM/DD
        match = re.search(
            r"Statement\s*Date.*?(\d{4})/(\d{2})/(\d{2})", html, re.DOTALL
        )
        if match:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))

        # Fallback: derive from filename YYYY年MM月
        match = re.search(r"(\d{4})年(\d{1,2})月", file_path.name)
        if match:
            year, month = int(match.group(1)), int(match.group(2))
            # Assume 10th as default statement day
            return date(year, month, 10)

        # Last fallback
        today = date.today()
        return date(today.year, today.month, 10)

    def _extract_statement_month(self, html: str, file_path: Path) -> tuple[int, int]:
        """Extract statement year and month.

        Returns:
            Tuple of (year, month).
        """
        stmt_date = self._extract_statement_date(html, file_path)
        return stmt_date.year, stmt_date.month

    def _extract_statement_period(
        self, html: str, file_path: Path
    ) -> tuple[date, date] | None:
        """Extract statement period based on statement date.

        CMBC statement period: previous statement day + 1 to current statement day.
        E.g., statement date 12/10 -> period is 11/11 ~ 12/10.
        """
        stmt_date = self._extract_statement_date(html, file_path)
        end = stmt_date

        # Start = previous month's statement day + 1
        if stmt_date.month == 1:
            prev_stmt_date = date(stmt_date.year - 1, 12, stmt_date.day)
        else:
            prev_stmt_date = date(stmt_date.year, stmt_date.month - 1, stmt_date.day)

        start = prev_stmt_date + timedelta(days=1)

        return (start, end)

    def _parse_loop_band(
        self,
        loop_band,
        statement_year: int,
        statement_month: int,
        file_path: Path,
        statement_period: tuple[date, date] | None,
    ) -> list[Transaction]:
        """Parse transactions from loopBand3 section."""
        transactions: list[Transaction] = []

        # Find all fixBand9 spans which contain individual transaction rows
        for row_idx, fix_band in enumerate(loop_band.find_all("span", id="fixBand9")):
            txn = self._parse_transaction_row(
                fix_band,
                statement_year,
                statement_month,
                file_path,
                row_idx,
                statement_period,
            )
            if txn:
                transactions.append(txn)

        return transactions

    def _parse_transaction_row(
        self,
        fix_band,
        statement_year: int,
        statement_month: int,
        file_path: Path,
        row_idx: int,
        statement_period: tuple[date, date] | None,
    ) -> Transaction | None:
        """Parse a single transaction row.

        Row structure:
        - TD 1: 交易日 (MM/DD)
        - TD 2: 记账日 (MM/DD)
        - TD 3: 交易摘要 (in nested fixBand22)
        - TD 4: 交易金额 (in nested fixBand8)
        - TD 5: 卡号末四位 (in nested fixBand2)
        """
        try:
            # Find all TD cells in the row
            tds = fix_band.find_all("td", recursive=True)

            # Extract dates - look for MM/DD pattern
            dates = []
            for td in tds:
                text = self.clean_text(td.get_text())
                if re.match(r"^\d{2}/\d{2}$", text):
                    dates.append(text)

            if len(dates) < 2:
                return None

            trans_date_str = dates[0]  # 交易日
            post_date_str = dates[1]  # 记账日

            # Parse transaction date
            trans_month, trans_day = map(int, trans_date_str.split("/"))
            trans_year = self._determine_year(
                trans_month, statement_year, statement_month
            )
            trans_date = date(trans_year, trans_month, trans_day)

            # Parse posting date
            post_month, post_day = map(int, post_date_str.split("/"))
            post_year = self._determine_year(
                post_month, statement_year, statement_month
            )
            post_date = date(post_year, post_month, post_day)

            # Extract description from fixBand22
            description = ""
            desc_band = fix_band.find("span", id="fixBand22")
            if desc_band:
                description = self.clean_text(desc_band.get_text())

            if not description:
                return None

            # Extract amount from fixBand8
            amount = None
            amt_band = fix_band.find("span", id="fixBand8")
            if amt_band:
                amt_text = self.clean_text(amt_band.get_text())
                amt_text = amt_text.replace(",", "").replace("\xa0", "")
                if amt_text:
                    amount = Decimal(amt_text)

            if amount is None:
                return None

            # Extract card last 4 digits from fixBand2
            card_last4 = None
            card_band = fix_band.find("span", id="fixBand2")
            if card_band:
                card_text = self.clean_text(card_band.get_text())
                if re.match(r"^\d{4}$", card_text):
                    card_last4 = card_text

            metadata: dict = {}
            if trans_date != post_date:
                metadata["posting_date"] = post_date.isoformat()

            return Transaction(
                date=trans_date,
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
