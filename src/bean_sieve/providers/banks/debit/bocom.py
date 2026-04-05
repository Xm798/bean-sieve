"""Bank of Communications (交通银行) debit card statement provider."""

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
class BOCOMDebitProvider(BaseProvider):
    """
    Provider for Bank of Communications (交通银行) debit card XLS statements.

    Downloaded from: https://pbank.bankcomm.com/personbank/
    (个人网上银行 → 交易明细 → 下载)

    File format:
    - Format: XLS (BIFF8)
    - Row 0: Title "交通银行银行卡交易明细查询表"
    - Row 1: Account metadata (card number, name, period, totals)
    - Row 2: Column headers
    - Row 3+: Data rows
    - Columns: 记账日期, 交易时间, 交易地点, 交易方式, 支出金额, 收入金额,
               余额, 对方户名, 对方账户, 对方开户行, 摘要

    Note: Default export filename "交易明细列表.xls" is generic.
    For auto-detection, rename to include "交通银行" in the filename.
    """

    provider_id = "bocom_debit"
    provider_name = "交通银行借记卡"
    supported_formats = [".xls"]
    filename_keywords = ["交通银行"]
    content_keywords = []  # Binary XLS (BIFF8), content detection not possible

    COL_DATE = 0  # 记账日期
    COL_TIME = 1  # 交易时间
    COL_LOCATION = 2  # 交易地点
    COL_METHOD = 3  # 交易方式
    COL_EXPENSE = 4  # 支出金额
    COL_INCOME = 5  # 收入金额
    COL_COUNTERPARTY_NAME = 7  # 对方户名
    COL_COUNTERPARTY_ACCOUNT = 8  # 对方账户
    COL_COUNTERPARTY_BANK = 9  # 对方开户行
    COL_SUMMARY = 10  # 摘要

    HEADER_KEYWORDS = ("记账日期", "交易时间")
    TITLE_MARKER = "交通银行"

    EXPECTED_COLS = 11

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse BOCOM debit card XLS statement."""
        wb = xlrd.open_workbook(str(file_path))
        sheet = wb.sheet_by_index(0)

        if sheet.ncols < self.EXPECTED_COLS:
            logger.warning(
                "Expected %d+ columns, found %d in %s",
                self.EXPECTED_COLS,
                sheet.ncols,
                file_path,
            )
            return []

        # Validate this is a BOCOM statement
        if not self._is_bocom_statement(sheet):
            logger.warning("Not a BOCOM debit statement: %s", file_path)
            return []

        card_last4 = self._extract_card_last4(sheet)
        header_row = self._find_header_row(sheet)

        transactions: list[Transaction] = []
        for row_idx in range(header_row + 1, sheet.nrows):
            row = [sheet.cell_value(row_idx, col) for col in range(sheet.ncols)]
            txn = self._parse_row(row, row_idx + 1, card_last4, file_path)
            if txn:
                transactions.append(txn)

        return transactions

    def _is_bocom_statement(self, sheet: xlrd.sheet.Sheet) -> bool:
        """Verify this is a BOCOM statement by checking the title row."""
        if sheet.nrows < 1:
            return False
        title = str(sheet.cell_value(0, 0)).strip()
        return self.TITLE_MARKER in title

    def _find_header_row(self, sheet: xlrd.sheet.Sheet) -> int:
        """Find header row by scanning for column header keywords."""
        for row_idx in range(min(10, sheet.nrows)):
            if all(kw in self._row_text(sheet, row_idx) for kw in self.HEADER_KEYWORDS):
                return row_idx
        return 2  # fallback

    def _row_text(self, sheet: xlrd.sheet.Sheet, row_idx: int) -> str:
        """Get concatenated text of a row for keyword matching."""
        return " ".join(
            str(sheet.cell_value(row_idx, col)) for col in range(sheet.ncols)
        )

    def _extract_card_last4(self, sheet: xlrd.sheet.Sheet) -> str | None:
        """Extract card last 4 digits from metadata row."""
        for row_idx in range(min(5, sheet.nrows)):
            for col_idx in range(sheet.ncols):
                cell = str(sheet.cell_value(row_idx, col_idx))
                if "银行卡号" in cell:
                    # Extract all digits, take last 4
                    digits = re.sub(r"\D", "", cell.split("姓名")[0])
                    if len(digits) >= 4:
                        return digits[-4:]
        return None

    def _parse_row(
        self,
        row: list,
        source_line: int,
        card_last4: str | None,
        file_path: Path,
    ) -> Transaction | None:
        """Parse a single data row into a Transaction."""
        date_str = str(row[self.COL_DATE]).strip()
        if not date_str or not date_str[0].isdigit():
            return None

        tx_date = self._parse_date(date_str)
        if tx_date is None:
            return None

        tx_time = self._parse_time(row[self.COL_TIME])

        amount = self._parse_amount(row)
        if amount is None:
            return None

        counterparty = str(row[self.COL_COUNTERPARTY_NAME]).strip() or None
        counterparty_account = str(row[self.COL_COUNTERPARTY_ACCOUNT]).strip()
        counterparty_bank = str(row[self.COL_COUNTERPARTY_BANK]).strip()
        summary = str(row[self.COL_SUMMARY]).strip()
        location = str(row[self.COL_LOCATION]).strip()
        method = str(row[self.COL_METHOD]).strip()

        description = self._build_description(method, location, summary)
        order_id = self._extract_order_id(summary)

        metadata: dict[str, str] = {}
        if summary:
            metadata["summary"] = summary
        if method:
            metadata["method"] = method
        if location:
            metadata["location"] = location
        if counterparty_account:
            metadata["counterparty_account"] = counterparty_account
        if counterparty_bank:
            metadata["counterparty_bank"] = counterparty_bank

        return Transaction(
            date=tx_date,
            time=tx_time,
            amount=amount,
            currency="CNY",
            description=description,
            payee=counterparty,
            order_id=order_id,
            card_last4=card_last4,
            provider=self.provider_id,
            source_file=file_path,
            source_line=source_line,
            metadata=metadata,
        )

    @staticmethod
    def _parse_date(value: str) -> date | None:
        """Parse date from YYYY-MM-DD string."""
        try:
            parts = value.split("-")
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _parse_time(value: object) -> time | None:
        """Parse time from 'YYYY-MM-DD HH:MM:SS' datetime string."""
        time_str = str(value).strip()
        if not time_str:
            return None
        # Extract HH:MM:SS from datetime string
        match = re.search(r"(\d{2}):(\d{2}):(\d{2})", time_str)
        if match:
            return time(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        return None

    def _parse_amount(self, row: list) -> Decimal | None:
        """Parse amount from expense/income columns. Expense=positive, income=negative."""
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

    @staticmethod
    def _to_decimal(value: object) -> Decimal | None:
        """Convert cell value to Decimal, returning None for '--' or empty."""
        text = str(value).strip()
        if not text or text == "--":
            return None
        try:
            cleaned = text.replace(",", "")
            d = Decimal(cleaned)
            return d if d != 0 else None
        except (ValueError, InvalidOperation):
            return None

    @staticmethod
    def _build_description(method: str, location: str, summary: str) -> str:
        """Build description from transaction method, location, and summary."""
        # For structured summaries (pipe-delimited), extract key info
        if summary.startswith("|") and "交易说明:" in summary:
            match = re.search(r"交易说明:([^|]*)", summary)
            if match:
                summary = match.group(1).strip()

        parts = [p for p in [method, location, summary] if p]
        return " | ".join(parts) if parts else "Unknown"

    @staticmethod
    def _extract_order_id(summary: str) -> str | None:
        """Extract order ID from structured summary field."""
        if not summary:
            return None
        # Pattern: 订单编号:XXXXX or 流水号:XXXXX
        match = re.search(r"(?:订单编号|流水号):([^|]+)", summary)
        if match:
            return match.group(1).strip()
        # Pattern: 交易流水号XXXXX (e.g., WeChat transfers)
        match = re.search(r"交易流水号(\S+)", summary)
        if match:
            return match.group(1).strip()
        return None
