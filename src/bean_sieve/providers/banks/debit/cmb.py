"""China Merchants Bank (招商银行) debit card statement provider."""

from __future__ import annotations

import csv
import re
from datetime import date, time
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ....core.types import Transaction
from ... import register_provider
from ...base import BaseProvider


@register_provider
class CMBDebitProvider(BaseProvider):
    """
    Provider for China Merchants Bank (招商银行) debit card CSV statements.

    File format:
    - Encoding: UTF-8 with BOM
    - Header: 7 lines (title, export time, account, currency, date range, filter, blank)
    - Column header at line 8: 交易日期, 交易时间, 收入, 支出, 余额, 交易类型, 交易备注
    - Values have leading tabs within quoted fields
    - Footer: blank line + 2 summary lines starting with "# 收入合计" / "# 支出合计"
    """

    provider_id = "cmb_debit"
    provider_name = "招商银行借记卡"
    supported_formats = [".csv"]
    filename_keywords = ["CMB_"]
    content_keywords = ["招商银行交易记录"]

    COL_DATE = 0  # 交易日期
    COL_TIME = 1  # 交易时间
    COL_INCOME = 2  # 收入
    COL_EXPENSE = 3  # 支出
    COL_BALANCE = 4  # 余额
    COL_TYPE = 5  # 交易类型
    COL_REMARK = 6  # 交易备注

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse CMB debit card CSV statement."""
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
            if "交易日期" in line and "交易时间" in line:
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
        if len(row) < 7:
            return None

        date_str = row[self.COL_DATE]
        if not date_str or date_str.startswith("#"):
            return None

        # Parse date (format: YYYYMMDD)
        try:
            tx_date = date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
        except (ValueError, IndexError):
            return None

        # Parse time (format: HH:MM:SS)
        tx_time = None
        time_str = row[self.COL_TIME]
        if time_str:
            try:
                parts = time_str.split(":")
                tx_time = time(int(parts[0]), int(parts[1]), int(parts[2]))
            except (ValueError, IndexError):
                pass

        # Parse amount: expense=positive, income=negative
        amount = self._parse_amount(row)
        if amount is None:
            return None

        tx_type = row[self.COL_TYPE]
        remark = row[self.COL_REMARK]
        description = self._build_description(tx_type, remark)

        return Transaction(
            date=tx_date,
            time=tx_time,
            amount=amount,
            currency="CNY",
            description=description,
            card_last4=card_last4,
            provider=self.provider_id,
            source_file=file_path,
            source_line=row_num,
            metadata={"type": tx_type}
            | ({"balance": row[self.COL_BALANCE]} if row[self.COL_BALANCE] else {})
            | ({"remark": remark} if remark else {}),
        )

    def _parse_amount(self, row: list[str]) -> Decimal | None:
        """Parse amount from income/expense columns. Expense=positive, income=negative."""
        expense = self._to_decimal(row[self.COL_EXPENSE])
        income = self._to_decimal(row[self.COL_INCOME])

        if expense is not None:
            return expense
        if income is not None:
            return -income
        return None

    def _to_decimal(self, value: str) -> Decimal | None:
        """Convert string to Decimal."""
        cleaned = value.replace(",", "").strip()
        if not cleaned or cleaned == "-":
            return None
        try:
            d = Decimal(cleaned)
            return d if d != 0 else None
        except InvalidOperation:
            return None

    def _build_description(self, tx_type: str, remark: str) -> str:
        """Build description from transaction type and remark."""
        parts = [p for p in [tx_type, remark] if p]
        return " | ".join(parts) if parts else "Unknown"
