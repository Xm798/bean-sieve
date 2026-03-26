"""China CITIC Bank (中信银行) credit card statement provider."""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import xlrd

from ....core.types import Transaction
from ... import register_provider
from ...base import BaseProvider

logger = logging.getLogger(__name__)


@register_provider
class CITICCreditProvider(BaseProvider):
    """
    Provider for China CITIC Bank (中信银行) credit card XLS statements.

    Downloaded from: https://e.creditcard.ecitic.com/citiccard/ebank-ocp/ebankpc/bill.html

    File format:
    - Format: XLS (BIFF8)
    - Row 0: Title "本期账单明细(人民币)" (merged cells)
    - Row 1: Column headers
    - Row 2+: Data rows
    - Columns: 交易日期, 入账日期, 交易描述, 卡末四位, 交易币种, 结算币种, 交易金额, 结算金额
    - Amount sign: Positive = expense, Negative = income (e.g., payments)
    - Date format: YYYY-MM-DD
    - One file per card (per-card statement)
    """

    provider_id = "citic_credit"
    provider_name = "中信银行信用卡"
    supported_formats = [".xls"]
    filename_keywords = ["中信", "citic", "已出账单明细"]
    content_keywords = []  # Binary XLS (BIFF8), content detection not possible
    per_card_statement = True  # CITIC sends separate statements per card

    COL_TRANS_DATE = 0  # 交易日期
    COL_POST_DATE = 1  # 入账日期
    COL_DESCRIPTION = 2  # 交易描述
    COL_CARD_LAST4 = 3  # 卡末四位
    COL_TRANS_CURRENCY = 4  # 交易币种
    COL_SETTLE_CURRENCY = 5  # 结算币种
    COL_TRANS_AMOUNT = 6  # 交易金额
    COL_SETTLE_AMOUNT = 7  # 结算金额

    HEADER_KEYWORDS = ("交易日期", "入账日期")

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse CITIC credit card XLS statement."""
        wb = xlrd.open_workbook(str(file_path))
        sheet = wb.sheet_by_index(0)

        header_row = self._find_header_row(sheet)
        if header_row is None:
            logger.warning("No header row found in %s", file_path)
            return []

        if sheet.ncols < 8:
            logger.warning(
                "Expected 8+ columns, found %d in %s", sheet.ncols, file_path
            )
            return []

        transactions: list[Transaction] = []
        for row_idx in range(header_row + 1, sheet.nrows):
            row = [sheet.cell_value(row_idx, col) for col in range(sheet.ncols)]
            txn = self._parse_row(row, row_idx, file_path, wb.datemode)
            if txn:
                transactions.append(txn)

        # Infer statement_period from transaction date range
        if transactions:
            dates = [t.date for t in transactions]
            statement_period = (min(dates), max(dates))
            for t in transactions:
                t.statement_period = statement_period

        return transactions

    def _find_header_row(self, sheet: xlrd.sheet.Sheet) -> int | None:
        """Find the header row by matching keywords."""
        for row_idx in range(min(10, sheet.nrows)):
            cell_value = str(sheet.cell_value(row_idx, 0))
            if any(kw in cell_value for kw in self.HEADER_KEYWORDS):
                return row_idx
        return None

    def _parse_row(
        self,
        row: list,
        row_idx: int,
        file_path: Path,
        datemode: int,
    ) -> Transaction | None:
        """Parse a single data row into a Transaction."""
        try:
            raw_date = row[self.COL_TRANS_DATE]
            # Skip non-data rows
            date_str = self._normalize_cell_str(raw_date)
            if not date_str or not date_str[0].isdigit():
                return None

            trans_date = self._parse_date(raw_date, datemode)
            post_date = self._parse_date(row[self.COL_POST_DATE], datemode)
            description = str(row[self.COL_DESCRIPTION]).strip()
            card_last4 = self._normalize_cell_str(row[self.COL_CARD_LAST4])
            currency = self._map_currency(str(row[self.COL_SETTLE_CURRENCY]).strip())

            # Use settlement amount (结算金额)
            amount = self._parse_amount(row[self.COL_SETTLE_AMOUNT])
            if amount is None:
                logger.warning("Invalid amount at row %d in %s", row_idx, file_path)
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
            )
        except (IndexError, ValueError) as e:
            logger.warning("Failed to parse row %d in %s: %s", row_idx, file_path, e)
            return None

    @staticmethod
    def _normalize_cell_str(value) -> str:
        """Convert cell value to string, handling xlrd float-as-int values."""
        if isinstance(value, float) and value == int(value):
            return str(int(value))
        return str(value).strip()

    @staticmethod
    def _parse_date(value: object, datemode: int = 0) -> date:
        """Parse date from string 'YYYY-MM-DD' or Excel serial date number."""
        if isinstance(value, float):
            y, m, d, _, _, _ = xlrd.xldate_as_tuple(value, datemode)  # type: ignore[arg-type]
            return date(y, m, d)
        date_str = str(value).strip()
        parts = date_str.split("-")
        return date(int(parts[0]), int(parts[1]), int(parts[2]))

    def _parse_amount(self, value) -> Decimal | None:
        """Parse amount value from cell."""
        try:
            if isinstance(value, float):
                return Decimal(str(value))
            cleaned = str(value).replace(",", "").strip()
            return Decimal(cleaned)
        except (InvalidOperation, ValueError):
            return None

    @staticmethod
    def _map_currency(currency_str: str) -> str:
        """Map Chinese currency name to ISO code."""
        mapping = {
            "人民币": "CNY",
            "美元": "USD",
            "欧元": "EUR",
            "英镑": "GBP",
            "日元": "JPY",
            "港币": "HKD",
        }
        return mapping.get(currency_str, currency_str)
