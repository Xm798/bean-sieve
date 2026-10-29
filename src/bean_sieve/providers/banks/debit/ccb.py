"""China Construction Bank (建设银行) debit card statement provider."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import date, time
from decimal import Decimal
from pathlib import Path

import xlrd

from ....core.types import Transaction
from ... import register_provider
from ..._tabular import (
    join_description,
    masked_card_last4,
    normalize_cell_str,
    sheet_head_contains,
    to_decimal,
)
from ...base import BaseProvider

logger = logging.getLogger(__name__)


@register_provider
class CCBDebitProvider(BaseProvider):
    """
    Provider for China Construction Bank (建设银行) debit card XLS statements.

    File format:
    - Format: XLS (BIFF8)
    - Row 0: "China Construction Bank"
    - Metadata rows (branch, currency, account number), then the column headers
    - Data rows, ending with a disclaimer row
    - Columns come in one of two layouts, located by header text:
      - 记账日, 交易日期, 交易时间, 支出, 收入, 账户余额, 币种, 摘要, 对方账号,
        对方户名, 交易地点
      - 序号, 摘要, 币别, 钞汇, 交易日期, 交易金额 (signed: -expense, +income),
        账户余额, 交易地点/附言, 对方账号与户名
    """

    provider_id = "ccb_debit"
    provider_name = "建设银行借记卡"
    supported_formats = [".xls"]
    filename_pattern = re.compile(r"交易明细_\d{4}_\d{8}_\d{8}")
    per_card_statement = True  # CCB exports one statement per card

    # Field -> header texts naming it, across both layouts.
    COLUMN_HEADERS: dict[str, tuple[str, ...]] = {
        "date": ("交易日期",),
        "time": ("交易时间",),
        "expense": ("支出",),
        "income": ("收入",),
        "signed_amount": ("交易金额",),
        "balance": ("账户余额",),
        "summary": ("摘要",),
        "counterparty_account": ("对方账号",),
        "counterparty_name": ("对方户名",),
        "counterparty": ("对方账号与户名",),
        "location": ("交易地点", "交易地点/附言"),
    }

    HEADER_KEYWORD = "交易日期"
    # The two layouts title the sheet in English and in Chinese respectively.
    TITLE_MARKERS = ("China Construction Bank", "中国建设银行个人活期账户")
    FOOTER_MARKER = "以上数据仅供参考"
    DATE_PATTERN = re.compile(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})(?:\s|$)")
    # The single-amount layout prints the account in full: "卡号/账号:<digits>".
    UNMASKED_ACCOUNT = re.compile(r"账号[:：]\s*(\d{12,})")
    # A combined counterparty cell reads "<account>/<name>", or just "<name>"
    # when the counterparty has no account; accounts may mix letters and "*".
    COMBINED_COUNTERPARTY = re.compile(r"([^/]+?)\s*/\s*(.+)")

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse CCB debit card XLS statement."""
        wb = xlrd.open_workbook(str(file_path))
        sheet = wb.sheet_by_index(0)

        card_last4 = self._extract_card_last4(sheet, file_path)
        header_row = self._find_header_row(sheet, file_path)
        columns = self._map_columns(sheet.row_values(header_row))
        if "date" not in columns or not columns.keys() & {
            "signed_amount",
            "expense",
            "income",
        }:
            raise ValueError(
                f"Unrecognised column layout in {file_path}: {sheet.row_values(header_row)}"
            )

        transactions = []
        for row_idx in range(header_row + 1, sheet.nrows):
            row = [sheet.cell_value(row_idx, col) for col in range(sheet.ncols)]

            if self._is_footer(row):
                break

            cells = {
                field: normalize_cell_str(row[col]) if col < len(row) else ""
                for field, col in columns.items()
            }
            txn = self._parse_row(cells, row_idx + 1, card_last4, file_path)
            if txn:
                transactions.append(txn)

        self.assign_statement_periods(transactions)
        return transactions

    @classmethod
    def _match_content(cls, file_path: Path) -> bool:
        return any(
            sheet_head_contains(file_path, (title, cls.HEADER_KEYWORD), nrows=10)
            for title in cls.TITLE_MARKERS
        )

    def _find_header_row(self, sheet, file_path: Path) -> int:
        for row_idx in range(min(10, sheet.nrows)):
            cells = [str(v).strip() for v in sheet.row_values(row_idx)]
            if self.HEADER_KEYWORD in cells:
                return row_idx
        raise ValueError(f"No column header row found in {file_path}")

    def _map_columns(self, header: Sequence[object]) -> dict[str, int]:
        cells = [str(v).strip() for v in header]
        columns: dict[str, int] = {}
        for field, names in self.COLUMN_HEADERS.items():
            for col, cell in enumerate(cells):
                if cell in names:
                    columns[field] = col
                    break
        return columns

    def _extract_card_last4(self, sheet, file_path: Path) -> str | None:
        """Extract card last 4 digits from account row or filename."""
        for row_idx in range(min(5, sheet.nrows)):
            cell = str(sheet.cell_value(row_idx, 1) if sheet.ncols > 1 else "")
            last4 = masked_card_last4(cell)
            if last4:
                return last4
            match = self.UNMASKED_ACCOUNT.search(cell)
            if match:
                return match[1][-4:]

        # Fallback: extract from filename like "交易明细_6789_..."
        match = re.search(r"交易明细_(\d{4})", file_path.name)
        if match:
            return match.group(1)

        return None

    def _is_footer(self, row: list) -> bool:
        """Check if row is the footer disclaimer."""
        return isinstance(row[0], str) and self.FOOTER_MARKER in row[0]

    def _parse_row(
        self,
        cells: dict[str, str],
        source_line: int,
        card_last4: str | None,
        file_path: Path,
    ) -> Transaction | None:
        """Parse a single data row, given its cells keyed by field."""
        match = self.DATE_PATTERN.match(cells.get("date", ""))
        if not match:
            return None

        tx_date = date(int(match[1]), int(match[2]), int(match[3]))

        tx_time = self._parse_time(cells.get("time", ""))

        amount = self._parse_amount(cells)
        if amount is None:
            return None

        summary = cells.get("summary", "")
        location = cells.get("location", "")
        counterparty_account, counterparty = self._counterparty(cells)
        description = join_description(summary, location)

        metadata: dict[str, str] = {"summary": summary}
        balance = cells.get("balance", "")
        if balance and balance != "0":
            metadata["balance"] = balance
        if counterparty_account:
            metadata["counterparty_account"] = counterparty_account
        if location:
            metadata["location"] = location

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

    def _counterparty(self, cells: dict[str, str]) -> tuple[str, str]:
        """Return (account, name), with "0" placeholders read as empty."""
        account = cells.get("counterparty_account", "")
        name = cells.get("counterparty_name", "")
        combined = cells.get("counterparty", "")
        if combined:
            match = self.COMBINED_COUNTERPARTY.fullmatch(combined)
            account, name = (match[1], match[2]) if match else ("", combined)
        return (
            "" if account == "0" else account,
            "" if name == "0" else name,
        )

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

    def _parse_amount(self, cells: dict[str, str]) -> Decimal | None:
        """Parse amount. Expense=positive, income=negative."""
        if "signed_amount" in cells:
            signed = to_decimal(cells["signed_amount"])
            return -signed if signed is not None else None

        expense = to_decimal(cells.get("expense", ""))
        income = to_decimal(cells.get("income", ""))

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
