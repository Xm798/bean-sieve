"""China CITIC Bank (中信银行) debit card statement provider."""

from __future__ import annotations

import logging
import re
from datetime import date, time
from decimal import Decimal
from pathlib import Path

import xlrd

from ....core.types import Transaction
from ... import register_provider
from ..._tabular import (
    CURRENCY_CODES,
    join_description,
    normalize_cell_str,
    parse_stamp,
    row_cells,
    sheet_head_contains,
    to_decimal,
)
from ...base import BaseProvider

logger = logging.getLogger(__name__)

_ACCOUNT_RE = re.compile(r"账号[:：]\s*[\d* ]*(\d{4})(?!\d)")
# Names the title's currency word when no known currency name appears in it.
_CURRENCY_WORD_RE = re.compile(r"账号[:：][\d* ]*(\S+)")
_PERIOD_RE = re.compile(r"起始日期[:：]\s*(\d{8}).*?截止日期[:：]\s*(\d{8})")

_COLUMN_FIELDS = {
    "交易日期": "date",
    "交易时间": "time",
    "收入金额": "income",
    "支出金额": "expense",
    "账户余额": "balance",
    "对方名称": "counterparty_name",
    "对方账号": "counterparty_account",
    "受理机构": "institution",
    "摘要": "summary",
    # Both legs of one operation can share a serial, so it cannot be order_id.
    "交易流水号": "reference",
    "交易卡号": "card_number",
    "状态": "status",
}
_REQUIRED_FIELDS = frozenset({"date", "income", "expense"})
_NUMERIC_FIELDS = frozenset({"income", "expense", "balance"})
_METADATA_FIELDS = (
    "summary",
    "balance",
    "counterparty_account",
    "institution",
    "reference",
    "status",
)
_HEAD_ROWS = 10


def _moment(txn: Transaction) -> tuple[date, time]:
    return txn.date, txn.time or time.min


@register_provider
class CNCBDebitProvider(BaseProvider):
    """
    Provider for China CITIC Bank (中信银行) debit card XLS statements.

    File format:
    - Format: XLS (BIFF8), one sheet
    - Row 0: "账号：<masked account> <currency> 起始日期:YYYYMMDD 截止日期:YYYYMMDD"
    - Row 1: Column headers 交易日期 (YYYYMMDD), 交易时间 (HH:MM:SS), 收入金额,
      支出金额 (text with thousands separators, "__" when empty), 账户余额,
      对方名称, 对方账号, 受理机构, 摘要, 交易流水号, 交易卡号 ("--" when
      empty), 状态
    - Data rows, usually newest first, no footer
    """

    provider_id = "cncb_debit"
    provider_name = "中信银行借记卡"
    supported_formats = [".xls"]
    per_card_statement = True  # CNCB exports one statement per account

    # The export names no bank, so its column names identify it.
    CONTENT_MARKERS = ("收入金额", "支出金额", "受理机构", "交易流水号", "交易卡号")

    @classmethod
    def _match_content(cls, file_path: Path) -> bool:
        return sheet_head_contains(file_path, cls.CONTENT_MARKERS, nrows=_HEAD_ROWS)

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse a CNCB debit card XLS statement."""
        sheet = xlrd.open_workbook(str(file_path)).sheet_by_index(0)
        header_row, columns = self._find_columns(sheet, file_path)
        title = " ".join(
            normalize_cell_str(v)
            for r in range(header_row)
            for v in sheet.row_values(r)
        )

        account = _ACCOUNT_RE.search(title)
        card_last4 = account.group(1) if account else None
        if (currency := self._currency(title, file_path)) is None:
            return []

        transactions: list[Transaction] = []
        for row_idx in range(header_row + 1, sheet.nrows):
            row = sheet.row_values(row_idx)
            cells = row_cells(
                row, columns, _COLUMN_FIELDS.values(), _NUMERIC_FIELDS, currency
            )
            txn = self._parse_row(cells, row_idx + 1, card_last4, currency, file_path)
            if txn:
                transactions.append(txn)
        # The export runs newest first unless its first row is strictly older
        # than its last; booking order keeps same-moment rows in the order
        # their balances follow, so a newest-first export is read bottom-up.
        if transactions and _moment(transactions[0]) >= _moment(transactions[-1]):
            transactions.reverse()

        cycle = None
        if period := _PERIOD_RE.search(title):
            start = parse_stamp(period.group(1), "%Y%m%d")
            end = parse_stamp(period.group(2), "%Y%m%d")
            if start and end:
                cycle = (start.date(), end.date())
        self.assign_statement_periods(transactions, cycle)
        return transactions

    @staticmethod
    def _currency(title: str, file_path: Path) -> str | None:
        for name, code in CURRENCY_CODES.items():
            if name in title:
                return code
        word = _CURRENCY_WORD_RE.search(title)
        logger.warning(
            "Skipping %s: unrecognised account currency %r",
            file_path.name,
            word.group(1) if word else None,
        )
        return None

    @staticmethod
    def _find_columns(sheet, file_path: Path) -> tuple[int, dict[str, int]]:
        for row_idx in range(min(_HEAD_ROWS, sheet.nrows)):
            header = [normalize_cell_str(v) for v in sheet.row_values(row_idx)]
            columns = {
                _COLUMN_FIELDS[cell]: col
                for col, cell in enumerate(header)
                if cell in _COLUMN_FIELDS
            }
            if columns.keys() >= _REQUIRED_FIELDS:
                return row_idx, columns
        raise ValueError(f"No column header row found in {file_path}")

    def _parse_row(
        self,
        cells: dict[str, str],
        source_line: int,
        card_last4: str | None,
        currency: str,
        file_path: Path,
    ) -> Transaction | None:
        tx_date = parse_stamp(cells["date"], "%Y%m%d")
        if tx_date is None:
            return None
        amount = self._amount(cells)
        if amount is None:
            return None
        tx_time = parse_stamp(cells["time"], "%H:%M:%S")

        card_number = " ".join(cells["card_number"].split())
        metadata = {key: cells[key] for key in _METADATA_FIELDS}
        metadata["card_number"] = "" if card_number == "--" else card_number

        return Transaction(
            date=tx_date.date(),
            time=tx_time.time() if tx_time else None,
            amount=amount,
            currency=currency,
            description=join_description(cells["summary"]),
            payee=cells["counterparty_name"] or None,
            card_last4=card_last4,
            provider=self.provider_id,
            source_file=file_path,
            source_line=source_line,
            metadata={key: val for key, val in metadata.items() if val},
        )

    @staticmethod
    def _amount(cells: dict[str, str]) -> Decimal | None:
        """Expense positive, income negative; None when neither column holds one."""
        if (expense := to_decimal(cells["expense"])) is not None:
            return expense
        if (income := to_decimal(cells["income"])) is not None:
            return -income
        return None
