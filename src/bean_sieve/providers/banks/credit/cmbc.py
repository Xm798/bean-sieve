"""China Minsheng Bank (民生银行) credit card statement provider."""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, NamedTuple

from ....core.types import Transaction
from ... import register_provider
from ...base import BaseProvider

logger = logging.getLogger(__name__)


class _Section(NamedTuple):
    """Span ids of one transaction section: its loop, rows and cells.

    ``loop_id`` is None when the rows sit directly in loopBand3.
    """

    loop_id: str | None
    row_id: str
    description_id: str
    amount_id: str
    card_id: str


# Templates in detection order; the first one with rows inside loopBand3 is
# used. The two templates reuse span ids with different meanings (fixBand9 is
# a whole row in the single-table template and the card cell of a purchase in
# the sectioned one), so the sectioned template must be tried first.
_TEMPLATES: tuple[tuple[_Section, ...], ...] = (
    # Sectioned: 消费, 退货, 还款 and 奖励返还 each in a loop of their own.
    (
        _Section("loopBand1", "fixBand1", "fixBand82", "fixBand22", "fixBand9"),
        _Section("loopBand7", "fixBand39", "fixBand65", "fixBand64", "fixBand41"),
        _Section("loopBand5", "fixBand15", "fixBand19", "fixBand18", "fixBand17"),
        _Section("loopBand9", "fixBand77", "fixBand81", "fixBand80", "fixBand79"),
    ),
    # Single table (earlier statements): refunds and payments are rows with
    # negative amounts.
    (_Section(None, "fixBand9", "fixBand22", "fixBand8", "fixBand2"),),
)

_ROW_DATES_RE = re.compile(r"(\d{2}/\d{2}) ?(\d{2}/\d{2})")


@register_provider
class CMBCCreditProvider(BaseProvider):
    """
    Provider for China Minsheng Bank (民生银行) credit card email statements.

    Parses .eml files containing HTML statements with transaction tables. The
    rows sit in loopBand3, laid out by one of the templates in ``_TEMPLATES``.
    Every row starts with the 交易日 and 记账日 (MM/DD), followed by cells for
    交易摘要, 交易金额 (negative for refunds and payments) and 卡号末四位.

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
        statement_period = self._compute_statement_period(stmt_date)

        transactions: list[Transaction] = []
        loop_band = soup.find("span", id="loopBand3")
        if not loop_band:
            return transactions

        rows = self._template_rows(loop_band)
        if not rows and _ROW_DATES_RE.search(self.clean_text(loop_band.get_text())):
            # Dated rows that no template claims mean the bank changed its
            # layout; an empty result alone would look like a quiet month.
            logger.warning("Unrecognised CMBC statement layout in %s", file_path)
        for row, section in rows:
            txn = self._parse_row(row, section, stmt_date, file_path, len(transactions))
            if txn:
                transactions.append(txn)

        self.assign_statement_periods(transactions, statement_period)
        return transactions

    @staticmethod
    def _template_rows(loop_band) -> list[tuple[Any, _Section]]:
        for sections in _TEMPLATES:
            rows = []
            for section in sections:
                container = (
                    loop_band
                    if section.loop_id is None
                    else loop_band.find("span", id=section.loop_id)
                )
                if container:
                    rows += [
                        (row, section)
                        for row in container.find_all("span", id=section.row_id)
                    ]
            if rows:
                return rows
        return []

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

    def _parse_row(
        self,
        row,
        section: _Section,
        stmt_date: date,
        file_path: Path,
        row_idx: int,
    ) -> Transaction | None:
        try:
            date_match = _ROW_DATES_RE.match(self.clean_text(row.get_text()))
            if not date_match:
                return None
            trans_date = self._parse_mm_dd_date(date_match.group(1), stmt_date)
            post_date = self._parse_mm_dd_date(date_match.group(2), stmt_date)

            description = self._extract_text(row, section.description_id)
            if not description:
                return None

            amount = self._parse_amount_band(row, section.amount_id)
            if amount is None:
                return None

            metadata: dict = {}
            if trans_date != post_date:
                metadata["posting_date"] = post_date.isoformat()

            return Transaction(
                date=trans_date,
                amount=amount,
                currency="CNY",
                description=description,
                card_last4=self._parse_card_band(row, section.card_id),
                provider=self.provider_id,
                source_file=file_path,
                source_line=row_idx + 1,
                metadata=metadata,
            )
        except (ValueError, InvalidOperation):
            return None

    def _parse_mm_dd_date(self, mm_dd: str, stmt_date: date) -> date:
        month, day = map(int, mm_dd.split("/"))
        year = self._determine_year(month, stmt_date.year, stmt_date.month)
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
