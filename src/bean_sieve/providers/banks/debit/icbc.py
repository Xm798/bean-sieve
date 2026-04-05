"""ICBC (工商银行) debit card statement provider."""

from __future__ import annotations

import csv
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ....core.types import Transaction
from ... import register_provider
from ...base import BaseProvider


@register_provider
class ICBCDebitProvider(BaseProvider):
    """
    Provider for ICBC (工商银行) debit card CSV statements.

    File format:
    - Encoding: UTF-8 with BOM
    - Header: 6 metadata lines, then column header at line 7
    - Columns (15): 交易日期, 摘要, 交易详情, 交易场所, 交易国家或地区简称,
      钞/汇, 交易金额(收入), 交易金额(支出), 交易币种,
      记账金额(收入), 记账金额(支出), 记账币种, 余额, 对方户名, 对方账户
    - Footer: summary row starting with "人民币合计"
    - Amounts use thousand separators (commas)
    """

    provider_id = "icbc_debit"
    provider_name = "工商银行借记卡"
    supported_formats = [".csv"]
    filename_keywords = ["hisdetail"]
    content_keywords: list[str] = []

    # Column indices (0-based, after header)
    COL_DATE = 0  # 交易日期
    COL_SUMMARY = 1  # 摘要
    COL_DETAIL = 2  # 交易详情
    COL_LOCATION = 3  # 交易场所
    COL_INCOME = 9  # 记账金额(收入)
    COL_EXPENSE = 10  # 记账金额(支出)
    COL_BALANCE = 12  # 余额
    COL_COUNTERPARTY = 13  # 对方户名
    COL_COUNTER_ACCOUNT = 14  # 对方账户

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse ICBC debit card CSV statement."""
        lines = self._read_lines(file_path)
        card_last4 = self._extract_card_last4(lines)
        header_idx = self._find_header(lines)
        if header_idx is None:
            raise ValueError(f"Cannot find header row in {file_path}")

        transactions = []
        data_lines = lines[header_idx:]
        reader = csv.reader(data_lines)
        next(reader)  # skip header row

        for row_num, row in enumerate(reader, header_idx + 2):
            stripped = [c.strip() for c in row]
            txn = self._parse_row(stripped, row_num, card_last4, file_path)
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

    def _extract_card_last4(self, lines: list[str]) -> str | None:
        """Extract card last 4 digits from metadata lines."""
        for line in lines[:6]:
            match = re.search(r"\d{4}\*+(\d{4})", line)
            if match:
                return match.group(1)
        return None

    def _find_header(self, lines: list[str]) -> int | None:
        """Find the header row index."""
        for i, line in enumerate(lines):
            if "交易日期" in line and "摘要" in line:
                return i
        return None

    def _parse_row(
        self,
        row: list[str],
        row_num: int,
        card_last4: str | None,
        file_path: Path,
    ) -> Transaction | None:
        """Parse a single data row."""
        if len(row) < 11:
            return None

        date_str = row[self.COL_DATE]
        if not date_str or "合计" in date_str:
            return None

        # Parse date (format: 2026-03-02)
        try:
            tx_date = date.fromisoformat(date_str)
        except ValueError:
            return None

        # Parse amount: income (negative) or expense (positive)
        amount = self._parse_amount(row)
        if amount is None:
            return None

        summary = row[self.COL_SUMMARY]
        detail = row[self.COL_DETAIL] if len(row) > self.COL_DETAIL else ""
        location = row[self.COL_LOCATION] if len(row) > self.COL_LOCATION else ""
        counterparty = (
            row[self.COL_COUNTERPARTY] if len(row) > self.COL_COUNTERPARTY else ""
        )

        balance = (
            row[self.COL_BALANCE].replace(",", "").strip()
            if len(row) > self.COL_BALANCE
            else ""
        )
        description = self._build_description(summary, detail, location)

        return Transaction(
            date=tx_date,
            amount=amount,
            currency="CNY",
            description=description,
            payee=counterparty or None,
            card_last4=card_last4,
            provider=self.provider_id,
            source_file=file_path,
            source_line=row_num,
            metadata={
                "summary": summary,
                **({"detail": detail} if detail else {}),
                **({"location": location} if location else {}),
                **({"balance": balance} if balance else {}),
            },
        )

    def _parse_amount(self, row: list[str]) -> Decimal | None:
        """Parse amount from income/expense columns. Expense=positive, income=negative."""
        expense_str = row[self.COL_EXPENSE] if len(row) > self.COL_EXPENSE else ""
        income_str = row[self.COL_INCOME] if len(row) > self.COL_INCOME else ""

        expense = self._to_decimal(expense_str)
        income = self._to_decimal(income_str)

        if expense is not None:
            return expense
        if income is not None:
            return -income
        return None

    def _to_decimal(self, value: str) -> Decimal | None:
        """Convert string with thousand separators to Decimal."""
        cleaned = value.replace(",", "").strip()
        if not cleaned or cleaned == "-":
            return None
        try:
            d = Decimal(cleaned)
            return d if d != 0 else None
        except InvalidOperation:
            return None

    def _build_description(self, summary: str, detail: str, location: str) -> str:
        """Build description from summary, detail, and location."""
        parts = [p for p in [summary, detail, location] if p]
        return " | ".join(parts) if parts else "Unknown"
