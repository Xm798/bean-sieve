"""Bank of China (中国银行) debit card statement provider."""

from __future__ import annotations

import csv
import logging
from datetime import date, time
from decimal import Decimal
from pathlib import Path

from ....core.types import Transaction
from ... import register_provider
from ..._tabular import (
    currency_code,
    find_header_line,
    join_description,
    read_text_lines,
    to_decimal,
)
from ...base import BaseProvider

logger = logging.getLogger(__name__)


@register_provider
class BOCDebitProvider(BaseProvider):
    """
    Provider for Bank of China (中国银行) debit card CSV statements.

    Downloaded from: https://ebsnew.boc.cn/boc15/login.html?locale=zh

    Two layouts share the same fields, so columns are located by header name:
    - Copied table (查询 → 复制表格 → 保存为 CSV): UTF-8, comma-separated,
      columns 交易时间 (date and time), 业务摘要, 对方账户名称, 对方账户账号,
      币种, 钞/汇, 收入金额, 支出金额, 余额, 交易渠道/场所, 附言
    - Downloaded export: UTF-16 LE with a BOM, tab-separated, 交易日期 (date
      only) in place of 交易时间, 交易渠道 in place of 交易渠道/场所, and an
      extra 网点名称 column
    - Amounts use thousand separators (commas)
    - Multi-currency support (人民币元, 港币, etc.)
    """

    provider_id = "boc_debit"
    provider_name = "中国银行借记卡"
    supported_formats = [".csv"]
    filename_keywords = ["中国银行"]
    content_keywords = ["业务摘要"]
    per_card_statement = True  # BOC exports one statement per card
    unkeyed_rows_share_account = True

    COLUMN_NAMES: dict[str, tuple[str, ...]] = {
        "tx_time": ("交易时间", "交易日期"),
        "summary": ("业务摘要",),
        "counterparty": ("对方账户名称",),
        "counterparty_account": ("对方账户账号",),
        "currency": ("币种",),
        "cash_remit": ("钞/汇",),
        "income": ("收入金额",),
        "expense": ("支出金额",),
        "balance": ("余额",),
        "channel": ("交易渠道/场所", "交易渠道"),
        "remarks": ("附言",),
    }
    HEADER_KEYWORDS = ("业务摘要", "收入金额", "支出金额")

    # BOC spells the yuan out in full.
    CURRENCY_ALIASES: dict[str, str] = {"人民币元": "CNY"}

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse BOC debit card CSV statement."""
        lines = read_text_lines(file_path)
        header_idx = find_header_line(lines, self.HEADER_KEYWORDS, 10)
        if header_idx is None:
            logger.warning("Cannot find header row in %s", file_path)
            return []

        delimiter = "\t" if "\t" in lines[header_idx] else ","
        reader = csv.reader(lines[header_idx:], delimiter=delimiter)
        header = [c.strip() for c in next(reader)]
        columns = self._locate_columns(header)
        if columns is None:
            logger.warning("Unrecognised header columns in %s", file_path)
            return []

        transactions: list[Transaction] = []
        for row_num, row in enumerate(reader, header_idx + 2):
            stripped = [c.strip() for c in row]
            if len(stripped) < len(header):
                continue
            fields = {key: stripped[i] for key, i in columns.items()}
            txn = self._parse_row(fields, row_num, file_path)
            if txn:
                transactions.append(txn)

        self.assign_statement_periods(transactions)
        return transactions

    def _locate_columns(self, header: list[str]) -> dict[str, int] | None:
        """Map each field to its column index; None when a column is missing."""
        columns: dict[str, int] = {}
        for key, names in self.COLUMN_NAMES.items():
            index = next((header.index(n) for n in names if n in header), None)
            if index is None:
                return None
            columns[key] = index
        return columns

    def _parse_row(
        self,
        row: dict[str, str],
        row_num: int,
        file_path: Path,
    ) -> Transaction | None:
        """Parse a single data row."""
        tx_date, tx_time = self._parse_datetime(row["tx_time"])
        if tx_date is None:
            return None

        name = row["currency"]
        currency = self.CURRENCY_ALIASES.get(name) or currency_code(name) or name

        amount = self._parse_amount(row)
        if amount is None:
            return None

        summary = row["summary"]
        counterparty = row["counterparty"]
        description = self._build_description(summary, row["remarks"])

        metadata: dict[str, str] = {"summary": summary}
        for key in ("counterparty_account", "balance", "cash_remit", "channel"):
            if row[key]:
                metadata[key] = row[key]

        return Transaction(
            date=tx_date,
            time=tx_time,
            amount=amount,
            currency=currency,
            description=description,
            payee=counterparty or None,
            provider=self.provider_id,
            source_file=file_path,
            source_line=row_num,
            metadata=metadata,
        )

    @staticmethod
    def _parse_datetime(value: str) -> tuple[date | None, time | None]:
        """Parse 'YYYY/MM/DD' with an optional 'HH:MM:SS' time."""
        if not value or not value[0].isdigit():
            return None, None
        try:
            # Normalize separators and non-breaking spaces
            normalized = value.replace("\xa0", " ").replace("/", "-")
            parts = normalized.split(" ")
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

    def _parse_amount(self, row: dict[str, str]) -> Decimal | None:
        """Parse amount. Expense=positive, income=negative."""
        expense = to_decimal(row["expense"])
        income = to_decimal(row["income"])

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
    def _build_description(summary: str, remarks: str) -> str:
        """Build description from summary and remarks."""
        if remarks.startswith("--"):
            remarks = remarks[2:]
        return join_description(summary, remarks)
