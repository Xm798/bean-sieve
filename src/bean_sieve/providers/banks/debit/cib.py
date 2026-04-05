"""Industrial Bank (兴业银行) debit card statement provider."""

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
class CIBDebitProvider(BaseProvider):
    """
    Provider for Industrial Bank (兴业银行) debit card XLS statements.

    Downloaded from: https://personalbank.cib.com.cn/
    Path: 查询 → 交易明细查询 → 流水下载 → Excel 格式

    File format:
    - Format: XLS (BIFF8)
    - Row 0: "兴业银行交易明细"
    - Row 2-8: metadata (账户别名, 账户户名, 账户账号, 卡内账户, 起始/截止日期, 下载日期)
    - Row 10: column headers
    - Row 11+: data rows
    - Last row: disclaimer "说明..."
    - Columns: 交易时间, 记账日, 支出, 收入, 账户余额, 摘要, 对方户名, 对方银行, 对方账号, 用途, 交易渠道, 备注
    - Cells are typically text type, but _normalize_cell_str handles xlrd float values defensively
    """

    provider_id = "cib_debit"
    provider_name = "兴业银行借记卡"
    supported_formats = [".xls"]
    filename_pattern = re.compile(r"的交易明细 \d{8}-\d{8}")
    content_keywords = []  # Binary XLS (BIFF8), content detection not possible

    COL_TX_TIME = 0  # 交易时间 (datetime string)
    COL_EXPENSE = 2  # 支出
    COL_INCOME = 3  # 收入
    COL_BALANCE = 4  # 账户余额
    COL_SUMMARY = 5  # 摘要
    COL_COUNTERPARTY_NAME = 6  # 对方户名
    COL_COUNTERPARTY_BANK = 7  # 对方银行
    COL_COUNTERPARTY_ACCOUNT = 8  # 对方账号
    COL_PURPOSE = 9  # 用途
    COL_CHANNEL = 10  # 交易渠道
    COL_REMARK = 11  # 备注

    EXPECTED_COLS = 12
    HEADER_KEYWORDS = ("交易时间", "记账日")
    FOOTER_MARKER = "说明"
    TITLE_MARKER = "兴业银行"

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse CIB debit card XLS statement."""
        wb = xlrd.open_workbook(str(file_path))
        sheet = wb.sheet_by_index(0)

        # Validate this is a CIB statement (guard against filename_pattern false positives)
        if sheet.nrows > 0 and self.TITLE_MARKER not in str(sheet.cell_value(0, 0)):
            logger.warning(
                "Row 0 does not contain '%s' in %s, skipping",
                self.TITLE_MARKER,
                file_path,
            )
            return []

        if sheet.ncols < self.EXPECTED_COLS:
            logger.warning(
                "Expected %d+ columns, found %d in %s",
                self.EXPECTED_COLS,
                sheet.ncols,
                file_path,
            )
            return []

        card_last4 = self._extract_card_last4(sheet)
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
        for row_idx in range(min(15, sheet.nrows)):
            cell = str(sheet.cell_value(row_idx, 0)).strip()
            if any(kw in cell for kw in self.HEADER_KEYWORDS):
                return row_idx
        return 10  # fallback to default position

    def _extract_card_last4(self, sheet) -> str | None:
        """Extract card last 4 digits from account number row."""
        for row_idx in range(min(10, sheet.nrows)):
            label = str(sheet.cell_value(row_idx, 0)).strip()
            if label == "账户账号":
                account_no = self._normalize_cell_str(sheet.cell_value(row_idx, 1))
                if len(account_no) >= 4:
                    return account_no[-4:]
        return None

    @staticmethod
    def _normalize_cell_str(value) -> str:
        """Convert cell value to string, handling xlrd float-as-int values."""
        if isinstance(value, float) and value == int(value):
            return str(int(value))
        return str(value).strip()

    def _is_footer(self, row: list) -> bool:
        """Check if row is the footer disclaimer."""
        cell = str(row[0]).strip()
        return cell.startswith(self.FOOTER_MARKER)

    def _parse_row(
        self,
        row: list,
        source_line: int,
        card_last4: str | None,
        file_path: Path,
    ) -> Transaction | None:
        """Parse a single data row."""
        tx_time_str = str(row[self.COL_TX_TIME]).strip()
        if not tx_time_str or not tx_time_str[0].isdigit():
            return None

        tx_date, tx_time = self._parse_datetime(tx_time_str)
        if tx_date is None:
            return None

        amount = self._parse_amount(row)
        if amount is None:
            return None

        summary = str(row[self.COL_SUMMARY]).strip()
        counterparty = str(row[self.COL_COUNTERPARTY_NAME]).strip()
        counterparty_bank = str(row[self.COL_COUNTERPARTY_BANK]).strip()
        counterparty_account = str(row[self.COL_COUNTERPARTY_ACCOUNT]).strip()
        purpose = str(row[self.COL_PURPOSE]).strip()
        channel = str(row[self.COL_CHANNEL]).strip()
        remark = str(row[self.COL_REMARK]).strip() if len(row) > self.COL_REMARK else ""
        description = self._build_description(summary, purpose)

        metadata: dict[str, str] = {"summary": summary}
        balance = str(row[self.COL_BALANCE]).strip()
        if balance:
            metadata["balance"] = balance
        if counterparty_bank:
            metadata["counterparty_bank"] = counterparty_bank
        if counterparty_account:
            metadata["counterparty_account"] = counterparty_account
        if purpose:
            metadata["purpose"] = purpose
        if channel:
            metadata["channel"] = channel
        if remark:
            metadata["remark"] = remark

        return Transaction(
            date=tx_date,
            time=tx_time,
            amount=amount,
            currency="CNY",
            description=description,
            payee=counterparty or None,
            card_last4=card_last4,
            provider=self.provider_id,
            source_file=file_path,
            source_line=source_line,
            metadata=metadata,
        )

    @staticmethod
    def _parse_datetime(value: str) -> tuple[date | None, time | None]:
        """Parse datetime from 'YYYY-MM-DD HH:MM:SS' string."""
        try:
            parts = value.split(" ")
            date_parts = parts[0].split("-")
            tx_date = date(int(date_parts[0]), int(date_parts[1]), int(date_parts[2]))
            tx_time = None
            if len(parts) > 1:
                time_parts = parts[1].split(":")
                tx_time = time(
                    int(time_parts[0]),
                    int(time_parts[1]),
                    int(time_parts[2]) if len(time_parts) > 2 else 0,
                )
            return tx_date, tx_time
        except (ValueError, IndexError):
            return None, None

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

    @staticmethod
    def _to_decimal(value) -> Decimal | None:
        """Convert value to Decimal, returning None for empty."""
        try:
            cleaned = str(value).replace(",", "").strip()
            if not cleaned:
                return None
            d = Decimal(cleaned)
            return d if d != 0 else None
        except (ValueError, InvalidOperation):
            return None

    @staticmethod
    def _build_description(summary: str, purpose: str) -> str:
        """Build description from summary and purpose."""
        parts = [p for p in [summary, purpose] if p]
        return " | ".join(parts) if parts else "Unknown"
