"""Bank of China (中国银行) debit card statement provider."""

from __future__ import annotations

import csv
import logging
from datetime import date, time
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ....core.types import ReconcileContext, Transaction
from ... import register_provider
from ...base import BaseProvider

logger = logging.getLogger(__name__)


@register_provider
class BOCDebitProvider(BaseProvider):
    """
    Provider for Bank of China (中国银行) debit card CSV statements.

    Downloaded from: https://ebsnew.boc.cn/boc15/login.html?locale=zh
    Path: 使用卡号登录 → 查询 → 复制表格 → 保存为 CSV

    File format:
    - Encoding: UTF-8
    - Row 0: column headers
    - Row 1+: data rows
    - Columns: 交易时间, 业务摘要, 对方账户名称, 对方账户账号, 币种, 钞/汇,
               收入金额, 支出金额, 余额, 交易渠道/场所, 附言
    - Amounts use thousand separators (commas)
    - Multi-currency support (人民币元, 港币, etc.)
    """

    provider_id = "boc_debit"
    provider_name = "中国银行借记卡"
    supported_formats = [".csv"]
    filename_keywords = ["中国银行"]
    content_keywords = ["业务摘要"]

    COL_TX_TIME = 0  # 交易时间
    COL_SUMMARY = 1  # 业务摘要
    COL_COUNTERPARTY = 2  # 对方账户名称
    COL_COUNTERPARTY_ACCOUNT = 3  # 对方账户账号
    COL_CURRENCY = 4  # 币种
    COL_INCOME = 6  # 收入金额
    COL_EXPENSE = 7  # 支出金额
    COL_BALANCE = 8  # 余额
    COL_CHANNEL = 9  # 交易渠道/场所
    COL_REMARKS = 10  # 附言

    EXPECTED_COLS = 11
    HEADER_KEYWORDS = ("交易时间", "业务摘要")

    CURRENCY_MAP: dict[str, str] = {
        "人民币元": "CNY",
        "港币": "HKD",
        "美元": "USD",
        "欧元": "EUR",
        "英镑": "GBP",
        "日元": "JPY",
        "澳元": "AUD",
        "加拿大元": "CAD",
        "瑞士法郎": "CHF",
        "新西兰元": "NZD",
        "新加坡元": "SGD",
    }

    def pre_reconcile(
        self,
        transactions: list[Transaction],
        context: ReconcileContext,
    ) -> list[Transaction]:
        """Set card_last4 from config (CSV has no card number field)."""
        if not context.config:
            return transactions
        provider_config = context.config.get_provider_config(self.provider_id)
        accounts = provider_config.accounts
        if len(accounts) == 1:
            card_key = next(iter(accounts))
            return [t.model_copy(update={"card_last4": card_key}) for t in transactions]
        if len(accounts) > 1:
            logger.warning(
                "boc_debit has %d accounts configured but CSV has no card number. "
                "Please use a single account entry or set card_last4 via rules.",
                len(accounts),
            )
        return transactions

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse BOC debit card CSV statement."""
        lines = self._read_lines(file_path)
        header_idx = self._find_header(lines)
        if header_idx is None:
            logger.warning("Cannot find header row in %s", file_path)
            return []

        transactions: list[Transaction] = []
        data_lines = lines[header_idx:]
        reader = csv.reader(data_lines)
        next(reader)  # skip header row

        for row_num, row in enumerate(reader, header_idx + 2):
            stripped = [c.strip() for c in row]
            txn = self._parse_row(stripped, row_num, file_path)
            if txn:
                transactions.append(txn)

        return transactions

    def _read_lines(self, file_path: Path) -> list[str]:
        """Read file with encoding detection."""
        for encoding in ["utf-8-sig", "utf-8", "gbk"]:
            try:
                with open(file_path, encoding=encoding) as f:
                    return f.readlines()
            except (UnicodeDecodeError, UnicodeError):
                continue
        raise ValueError(f"Cannot decode {file_path}")

    def _find_header(self, lines: list[str]) -> int | None:
        """Find the header row index."""
        for i, line in enumerate(lines[:10]):
            if all(kw in line for kw in self.HEADER_KEYWORDS):
                return i
        return None

    def _parse_row(
        self,
        row: list[str],
        row_num: int,
        file_path: Path,
    ) -> Transaction | None:
        """Parse a single data row."""
        if len(row) < self.EXPECTED_COLS:
            return None

        # Parse datetime
        tx_date, tx_time = self._parse_datetime(row[self.COL_TX_TIME])
        if tx_date is None:
            return None

        # Parse currency
        currency = self.CURRENCY_MAP.get(row[self.COL_CURRENCY], row[self.COL_CURRENCY])

        # Parse amount
        amount = self._parse_amount(row)
        if amount is None:
            return None

        summary = row[self.COL_SUMMARY]
        counterparty = row[self.COL_COUNTERPARTY]
        remarks = row[self.COL_REMARKS] if len(row) > self.COL_REMARKS else ""
        description = self._build_description(summary, remarks)

        metadata: dict[str, str] = {"summary": summary}
        counterparty_account = row[self.COL_COUNTERPARTY_ACCOUNT]
        if counterparty_account:
            metadata["counterparty_account"] = counterparty_account
        balance = row[self.COL_BALANCE]
        if balance:
            metadata["balance"] = balance
        channel = row[self.COL_CHANNEL] if len(row) > self.COL_CHANNEL else ""
        if channel:
            metadata["channel"] = channel

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
        """Parse datetime from 'YYYY/MM/DD HH:MM:SS' string."""
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

    def _parse_amount(self, row: list[str]) -> Decimal | None:
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
    def _to_decimal(value: str) -> Decimal | None:
        """Convert string with thousand separators to Decimal."""
        cleaned = value.replace(",", "").strip()
        if not cleaned or cleaned == "-":
            return None
        try:
            d = Decimal(cleaned)
            return d if d != 0 else None
        except InvalidOperation:
            return None

    @staticmethod
    def _build_description(summary: str, remarks: str) -> str:
        """Build description from summary and remarks."""
        if remarks.startswith("--"):
            remarks = remarks[2:]
        parts = [p for p in [summary, remarks] if p]
        return " | ".join(parts) if parts else "Unknown"
