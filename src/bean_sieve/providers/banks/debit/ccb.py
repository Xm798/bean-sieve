"""China Construction Bank (建设银行) debit card statement provider."""

from __future__ import annotations

import logging
import re
from datetime import date, time
from decimal import Decimal, InvalidOperation
from pathlib import Path

import xlrd

from ....core.types import Transaction
from ... import register_provider
from ...base import BaseProvider

logger = logging.getLogger(__name__)


@register_provider
class CCBDebitProvider(BaseProvider):
    """
    Provider for China Construction Bank (建设银行) debit card XLS statements.

    File format:
    - Format: XLS (BIFF8)
    - Row 0: "China Construction Bank"
    - Row 1-3: metadata (branch, currency, account number)
    - Row 4: blank
    - Row 5: column headers
    - Row 6+: data rows
    - Last row: disclaimer text
    - Columns: 记账日, 交易日期, 交易时间, 支出, 收入, 账户余额, 币种, 摘要, 对方账号, 对方户名, 交易地点
    """

    provider_id = "ccb_debit"
    provider_name = "建设银行借记卡"
    supported_formats = [".xls"]
    filename_pattern = re.compile(r"交易明细_\d{4}_\d{8}_\d{8}")
    content_keywords = []  # Binary XLS (BIFF8), content detection not possible

    COL_TX_DATE = 1  # 交易日期
    COL_TX_TIME = 2  # 交易时间
    COL_EXPENSE = 3  # 支出
    COL_INCOME = 4  # 收入
    COL_BALANCE = 5  # 账户余额
    COL_SUMMARY = 7  # 摘要
    COL_COUNTERPARTY_NAME = 9  # 对方户名
    COL_LOCATION = 10  # 交易地点

    HEADER_KEYWORDS = ("记账日", "交易日期")
    FOOTER_MARKER = "以上数据仅供参考"

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse CCB debit card XLS statement."""
        wb = xlrd.open_workbook(str(file_path))
        sheet = wb.sheet_by_index(0)

        card_last4 = self._extract_card_last4(sheet, file_path)
        header_row = self._find_header_row(sheet)

        transactions = []
        for row_idx in range(header_row + 1, sheet.nrows):
            row = [sheet.cell_value(row_idx, col) for col in range(sheet.ncols)]

            if self._is_footer(row):
                break

            txn = self._parse_row(row, row_idx + 1, card_last4, file_path)
            if txn:
                transactions.append(txn)

        return transactions

    def _find_header_row(self, sheet) -> int:
        """Find header row by scanning for column header keywords."""
        for row_idx in range(min(10, sheet.nrows)):
            cell = str(sheet.cell_value(row_idx, 0)).strip()
            if any(kw in cell for kw in self.HEADER_KEYWORDS):
                return row_idx
        return 5  # fallback to default position

    def _extract_card_last4(self, sheet, file_path: Path) -> str | None:
        """Extract card last 4 digits from account row or filename."""
        for row_idx in range(min(5, sheet.nrows)):
            cell = str(sheet.cell_value(row_idx, 1) if sheet.ncols > 1 else "")
            match = re.search(r"\d{4}\*+(\d{4})", cell)
            if match:
                return match.group(1)

        # Fallback: extract from filename like "交易明细_6789_..."
        match = re.search(r"交易明细_(\d{4})", file_path.name)
        if match:
            return match.group(1)

        return None

    def _is_footer(self, row: list) -> bool:
        """Check if row is the footer disclaimer."""
        return isinstance(row[0], str) and self.FOOTER_MARKER in row[0]

    def _normalize_cell_str(self, value) -> str:
        """Convert cell value to string, handling xlrd float-as-int values."""
        if isinstance(value, float) and value == int(value):
            return str(int(value))
        return str(value).strip()

    def _parse_row(
        self,
        row: list,
        source_line: int,
        card_last4: str | None,
        file_path: Path,
    ) -> Transaction | None:
        """Parse a single data row."""
        date_str = self._normalize_cell_str(row[self.COL_TX_DATE])
        if not date_str or not date_str.isdigit() or len(date_str) != 8:
            return None

        tx_date = date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))

        tx_time = self._parse_time(row[self.COL_TX_TIME])

        amount = self._parse_amount(row)
        if amount is None:
            return None

        summary = self._normalize_cell_str(row[self.COL_SUMMARY])
        counterparty = self._normalize_cell_str(row[self.COL_COUNTERPARTY_NAME])
        location = self._normalize_cell_str(row[self.COL_LOCATION])
        description = self._build_description(summary, location)

        return Transaction(
            date=tx_date,
            time=tx_time,
            amount=amount,
            currency="CNY",
            description=description,
            payee=counterparty if counterparty and counterparty != "0" else None,
            card_last4=card_last4,
            provider=self.provider_id,
            source_file=file_path,
            source_line=source_line,
            metadata=self._build_metadata(summary, row),
        )

    def _build_metadata(self, summary: str, row: list) -> dict[str, str]:
        metadata: dict[str, str] = {"summary": summary}
        balance = self._normalize_cell_str(row[self.COL_BALANCE])
        if balance and balance != "0":
            metadata["balance"] = balance
        return metadata

    def _parse_time(self, value) -> time | None:
        """Parse time from HH:MM:SS string."""
        time_str = str(value).strip()
        if not time_str:
            return None
        try:
            parts = time_str.split(":")
            return time(int(parts[0]), int(parts[1]), int(parts[2]))
        except (ValueError, IndexError):
            return None

    def _parse_amount(self, row: list) -> Decimal | None:
        """Parse amount. Expense=positive, income=negative."""
        expense = self._to_decimal(row[self.COL_EXPENSE])
        income = self._to_decimal(row[self.COL_INCOME])

        if expense is not None and income is not None:
            logger.warning(
                "Row has both expense (%s) and income (%s), using expense",
                expense,
                income,
            )

        if expense is not None:
            return expense
        if income is not None:
            return -income
        return None

    def _to_decimal(self, value) -> Decimal | None:
        """Convert value to Decimal, returning None for zero/empty."""
        try:
            d = Decimal(str(value).strip())
            return d if d != 0 else None
        except (ValueError, InvalidOperation):
            return None

    def _build_description(self, summary: str, location: str) -> str:
        """Build description from summary and location."""
        parts = [p for p in [summary, location] if p]
        return " | ".join(parts) if parts else "Unknown"
