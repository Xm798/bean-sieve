"""Shanghai Pudong Development Bank (浦发银行) credit card provider."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import xlrd
from xlrd.biffh import XLRDError

from ....core.types import Transaction
from ... import register_provider
from ...base import BaseProvider

logger = logging.getLogger(__name__)


@register_provider
class SPDBCreditProvider(BaseProvider):
    """
    Provider for SPDB credit card transaction-detail XLS reports.

    File format:
    - Format: XLS (BIFF8)
    - Header: first 10 rows, normally row 0
    - Columns: 交易日期, 记账日期, 交易摘要, 卡号末四位, 卡片类型,
      交易币种, 交易金额, 原始交易金额&币种
    - Amount sign: positive = expense, negative = payment/refund
    - Date format: YYYYMMDD
    - One monthly report may contain transactions from multiple cards
    """

    provider_id = "spdb_credit"
    provider_name = "浦发银行信用卡"
    supported_formats = [".xls"]
    filename_keywords = ["浦发", "spdb"]
    content_keywords: list[str] = []

    EXPECTED_HEADERS = (
        "交易日期",
        "记账日期",
        "交易摘要",
        "卡号末四位",
        "卡片类型",
        "交易币种",
        "交易金额",
        "原始交易金额&币种",
    )
    EXPECTED_COLS = len(EXPECTED_HEADERS)

    COL_TRANS_DATE = 0
    COL_POST_DATE = 1
    COL_DESCRIPTION = 2
    COL_CARD_LAST4 = 3
    COL_CARD_TYPE = 4
    COL_CURRENCY = 5
    COL_AMOUNT = 6
    COL_ORIGINAL_AMOUNT = 7

    @classmethod
    def can_handle(cls, file_path: Path) -> bool:
        """Detect explicit SPDB filenames or the report's unique XLS header."""
        if file_path.suffix.lower() not in cls.supported_formats:
            return False

        filename = file_path.name.lower()
        filename_matches = any(
            keyword.lower() in filename for keyword in cls.filename_keywords
        )
        if not file_path.is_file():
            return filename_matches

        try:
            workbook = xlrd.open_workbook(str(file_path), on_demand=True)
            sheet = workbook.sheet_by_index(0)
            return (
                sheet.ncols >= cls.EXPECTED_COLS
                and cls._find_header_row(sheet) is not None
            )
        except (OSError, XLRDError):
            return False

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse an SPDB credit card transaction-detail XLS report."""
        try:
            workbook = xlrd.open_workbook(str(file_path))
            sheet = workbook.sheet_by_index(0)
        except (OSError, XLRDError):
            logger.warning("Unable to read SPDB statement")
            return []

        if sheet.ncols < self.EXPECTED_COLS:
            logger.warning(
                "SPDB statement: expected %d+ columns, found %d",
                self.EXPECTED_COLS,
                sheet.ncols,
            )
            return []

        header_row = self._find_header_row(sheet)
        if header_row is None:
            logger.warning("No SPDB transaction header found")
            return []

        transactions: list[Transaction] = []
        for row_idx in range(header_row + 1, sheet.nrows):
            row = [sheet.cell_value(row_idx, col) for col in range(sheet.ncols)]
            transaction = self._parse_row(
                row,
                source_line=row_idx + 1,
                file_path=file_path,
                datemode=workbook.datemode,
            )
            if transaction:
                transactions.append(transaction)

        if transactions:
            # The export has no explicit query period, so use the observed
            # transaction-date range as the narrowest safe approximation.
            transaction_dates = [transaction.date for transaction in transactions]
            statement_period = (min(transaction_dates), max(transaction_dates))
            for transaction in transactions:
                transaction.statement_period = statement_period

        return transactions

    @classmethod
    def _find_header_row(cls, sheet: xlrd.sheet.Sheet) -> int | None:
        """Find the exact report header within the first 10 rows."""
        for row_idx in range(min(10, sheet.nrows)):
            headers = tuple(
                cls._normalize_cell_str(sheet.cell_value(row_idx, col))
                for col in range(cls.EXPECTED_COLS)
            )
            if headers == cls.EXPECTED_HEADERS:
                return row_idx
        return None

    def _parse_row(
        self,
        row: Sequence[object],
        source_line: int,
        file_path: Path,
        datemode: int,
    ) -> Transaction | None:
        """Parse one transaction row, skipping malformed rows with a warning."""
        if not any(self._normalize_cell_str(value) for value in row):
            return None

        try:
            transaction_date = self._parse_date(row[self.COL_TRANS_DATE], datemode)
            post_date = self._parse_date(row[self.COL_POST_DATE], datemode)
            description = self._normalize_cell_str(row[self.COL_DESCRIPTION])
            if not description:
                raise ValueError("empty description")

            amount = self._parse_amount(row[self.COL_AMOUNT])
            if amount is None:
                raise ValueError("invalid amount")

            currency = self._map_currency(
                self._normalize_cell_str(row[self.COL_CURRENCY])
            )
            if not currency:
                raise ValueError("empty currency")

            card_last4 = self._normalize_card_last4(row[self.COL_CARD_LAST4])
            metadata: dict[str, str | Decimal] = {}

            card_type = self._normalize_cell_str(row[self.COL_CARD_TYPE])
            if card_type and card_type != "-":
                metadata["card_type"] = card_type

            original_value = self._normalize_cell_str(row[self.COL_ORIGINAL_AMOUNT])
            original_amount, original_currency = self._parse_original_amount(
                original_value
            )
            if original_amount is not None:
                metadata["original_amount"] = original_amount
            if original_currency:
                metadata["original_currency"] = original_currency
            if (
                original_value
                and original_value != "-"
                and original_amount is None
                and not original_currency
            ):
                metadata["original_transaction"] = original_value

            return Transaction(
                date=transaction_date,
                post_date=post_date,
                amount=amount,
                currency=currency,
                description=description,
                card_last4=card_last4,
                provider=self.provider_id,
                source_file=file_path,
                source_line=source_line,
                metadata=metadata,
            )
        except (IndexError, ValueError):
            logger.warning("Failed to parse SPDB row %d", source_line)
            return None

    @staticmethod
    def _normalize_cell_str(value: object) -> str:
        """Convert an XLS cell to a trimmed string without a trailing .0."""
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value).strip()

    @classmethod
    def _normalize_card_last4(cls, value: object) -> str | None:
        """Normalize a card suffix, restoring leading zeroes for numeric cells."""
        card_last4 = cls._normalize_cell_str(value)
        if not card_last4 or not card_last4.isdigit() or len(card_last4) > 4:
            return None
        return card_last4.zfill(4)

    @classmethod
    def _parse_date(cls, value: object, datemode: int = 0) -> date:
        """Parse YYYYMMDD text/numbers or an Excel date cell."""
        if isinstance(value, float) and value < 10_000_000:
            year, month, day, _, _, _ = xlrd.xldate_as_tuple(
                value,
                datemode,  # type: ignore[arg-type]
            )
            return date(year, month, day)

        date_value = cls._normalize_cell_str(value)
        compact = date_value.replace("-", "").replace("/", "")
        if len(compact) != 8 or not compact.isdigit():
            raise ValueError("invalid date")
        return date(int(compact[:4]), int(compact[4:6]), int(compact[6:8]))

    @staticmethod
    def _parse_amount(value: object) -> Decimal | None:
        """Parse a signed transaction amount."""
        try:
            if isinstance(value, float):
                return Decimal(str(value))
            cleaned = str(value).replace(",", "").strip()
            return Decimal(cleaned)
        except (InvalidOperation, ValueError):
            return None

    @classmethod
    def _parse_original_amount(cls, value: str) -> tuple[Decimal | None, str | None]:
        """Parse an original amount formatted as ``amount(currency)``."""
        if not value or value == "-" or not value.endswith(")") or "(" not in value:
            return None, None

        amount_value, currency_value = value.rsplit("(", 1)
        amount = cls._parse_amount(amount_value)
        currency = cls._map_currency(currency_value[:-1].strip())
        if amount is None or not currency:
            return None, None
        return amount, currency

    @staticmethod
    def _map_currency(value: str) -> str:
        """Map statement currency labels to ISO currency codes."""
        mapping = {
            "人民币": "CNY",
            "美元": "USD",
            "欧元": "EUR",
            "英镑": "GBP",
            "日元": "JPY",
            "港币": "HKD",
        }
        return mapping.get(value, value.upper())
