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
    - Three transaction sections inside loopBand3:
      - loopBand1: purchases (消费) in fixBand1 rows
      - loopBand7: refunds (退货)
      - loopBand5: payments (还款)
    - Date format: MM/DD (concatenated in fixBand8 full text for purchases)
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

        stmt_date = self._extract_statement_date(html, file_path)
        statement_year, statement_month = stmt_date.year, stmt_date.month
        statement_period = self._compute_statement_period(stmt_date)

        transactions: list[Transaction] = []

        loop_band = soup.find("span", id="loopBand3")
        if not loop_band:
            return transactions

        for fix_band1 in loop_band.find_all("span", id="fixBand1"):
            txn = self._parse_transaction_row(
                fix_band1,
                statement_year,
                statement_month,
                file_path,
                len(transactions),
                statement_period,
            )
            if txn:
                transactions.append(txn)

        for loop_id, desc_band, amt_band, card_band, date_band in [
            ("loopBand7", "fixBand65", "fixBand64", "fixBand41", "fixBand39"),
            ("loopBand5", "fixBand19", "fixBand18", "fixBand17", "fixBand15"),
        ]:
            loop_span = soup.find("span", id=loop_id)
            if loop_span:
                txn = self._parse_refund_payment_row(
                    loop_span,
                    statement_year,
                    statement_month,
                    file_path,
                    len(transactions),
                    statement_period,
                    desc_band=desc_band,
                    amt_band=amt_band,
                    card_band=card_band,
                    date_band=date_band,
                )
                if txn:
                    transactions.append(txn)

        return transactions

    def _extract_statement_date(self, html: str, file_path: Path) -> date:
        """Extract the statement date (账单日) from HTML or filename."""
        match = re.search(
            r"Statement(?:\s|&nbsp;)*Date.*?(\d{4})/(\d{2})/(\d{2})", html, re.DOTALL
        )
        if match:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))

        match = re.search(r"(\d{4})年(\d{1,2})月", file_path.name)
        if match:
            return date(int(match.group(1)), int(match.group(2)), 10)

        return date(date.today().year, date.today().month, 10)

    @staticmethod
    def _compute_statement_period(stmt_date: date) -> tuple[date, date]:
        """Compute statement period: previous statement day + 1 to current statement day."""
        if stmt_date.month == 1:
            prev = date(stmt_date.year - 1, 12, stmt_date.day)
        else:
            prev = date(stmt_date.year, stmt_date.month - 1, stmt_date.day)
        return (prev + timedelta(days=1), stmt_date)

    def _parse_transaction_row(
        self,
        fix_band1,
        statement_year: int,
        statement_month: int,
        file_path: Path,
        row_idx: int,
        statement_period: tuple[date, date] | None,
    ) -> Transaction | None:
        """Parse a single purchase transaction row (fixBand1).

        Row structure inside fixBand1:
        - fixBand8: full text "MM/DDMM/DD描述金额卡号" (dates concatenated)
        - fixBand82: description (交易摘要)
        - fixBand22: amount (交易金额)
        - fixBand9: card last 4 digits (卡号末四位)
        """
        try:
            full_text_band = fix_band1.find("span", id="fixBand8")
            if not full_text_band:
                return None
            full_text = self.clean_text(full_text_band.get_text())

            date_match = re.match(r"(\d{2}/\d{2})(\d{2}/\d{2})", full_text)
            if not date_match:
                return None

            trans_date = self._parse_mm_dd_date(
                date_match.group(1), statement_year, statement_month
            )
            post_date = self._parse_mm_dd_date(
                date_match.group(2), statement_year, statement_month
            )

            description = self._extract_text(fix_band1, "fixBand82")
            if not description:
                return None

            amount = self._parse_amount_band(fix_band1, "fixBand22")
            if amount is None:
                return None

            card_last4 = self._parse_card_band(fix_band1, "fixBand9")

            metadata: dict = {}
            if trans_date != post_date:
                metadata["posting_date"] = post_date.isoformat()

            return self._build_transaction(
                trans_date,
                amount,
                description,
                card_last4,
                file_path,
                row_idx,
                statement_period,
                metadata,
            )
        except (ValueError, IndexError, AttributeError, InvalidOperation):
            return None

    def _parse_refund_payment_row(
        self,
        row_span,
        statement_year: int,
        statement_month: int,
        file_path: Path,
        row_idx: int,
        statement_period: tuple[date, date] | None,
        *,
        desc_band: str,
        amt_band: str,
        card_band: str,
        date_band: str,
    ) -> Transaction | None:
        """Parse a refund (退货) or payment (还款) row."""
        try:
            date_text = self._extract_text(row_span, date_band)
            if not date_text:
                return None

            date_match = re.match(r"(\d{2}/\d{2})", date_text)
            if not date_match:
                return None

            trans_date = self._parse_mm_dd_date(
                date_match.group(1), statement_year, statement_month
            )

            description = self._extract_text(row_span, desc_band)
            if not description:
                return None

            amount = self._parse_amount_band(row_span, amt_band)
            if amount is None:
                return None

            card_last4 = self._parse_card_band(row_span, card_band)

            return self._build_transaction(
                trans_date,
                amount,
                description,
                card_last4,
                file_path,
                row_idx,
                statement_period,
                {},
            )
        except (ValueError, IndexError, AttributeError, InvalidOperation):
            return None

    def _build_transaction(
        self,
        trans_date: date,
        amount: Decimal,
        description: str,
        card_last4: str | None,
        file_path: Path,
        row_idx: int,
        statement_period: tuple[date, date] | None,
        metadata: dict,
    ) -> Transaction:
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

    def _parse_mm_dd_date(
        self, mm_dd: str, statement_year: int, statement_month: int
    ) -> date:
        month, day = map(int, mm_dd.split("/"))
        year = self._determine_year(month, statement_year, statement_month)
        return date(year, month, day)

    @staticmethod
    def _extract_text(parent, band_id: str) -> str | None:
        band = parent.find("span", id=band_id)
        if not band:
            return None
        return band.get_text().strip() or None

    def _parse_amount_band(self, parent, band_id: str) -> Decimal | None:
        band = parent.find("span", id=band_id)
        if not band:
            return None
        amt_text = self.clean_text(band.get_text())
        amt_text = amt_text.replace(",", "").replace("\xa0", "")
        if not amt_text:
            return None
        return Decimal(amt_text)

    def _parse_card_band(self, parent, band_id: str) -> str | None:
        band = parent.find("span", id=band_id)
        if not band:
            return None
        card_text = self.clean_text(band.get_text())
        if re.match(r"^\d{4}$", card_text):
            return card_text
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
