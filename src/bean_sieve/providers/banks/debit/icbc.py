"""ICBC (工商银行) debit card statement provider."""

from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

from ....core.types import Transaction
from ... import register_provider
from ..._tabular import (
    find_header_line,
    first_masked_card_last4,
    join_description,
    read_text_lines,
    to_decimal,
)
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
    per_card_statement = True  # ICBC exports one statement per card

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
        lines = read_text_lines(file_path)
        card_last4 = first_masked_card_last4(lines[:6])
        header_idx = find_header_line(lines, ("交易日期", "摘要"))
        if header_idx is None:
            raise ValueError(f"Cannot find header row in {file_path}")

        transactions = []
        data_lines = lines[header_idx:]
        reader = csv.reader(data_lines)
        next(reader)

        for row_num, row in enumerate(reader, header_idx + 2):
            stripped = [c.strip() for c in row]
            txn = self._parse_row(stripped, row_num, card_last4, file_path)
            if txn:
                transactions.append(txn)

        # The export lists rows newest first and has no time column, so the
        # reversal is what keeps same-date rows in booking order for the
        # balance directive.
        transactions.reverse()
        self.assign_statement_periods(transactions)
        return transactions

    def _parse_row(
        self,
        row: list[str],
        row_num: int,
        card_last4: str | None,
        file_path: Path,
    ) -> Transaction | None:
        if len(row) < 11:
            return None

        date_str = row[self.COL_DATE]
        if not date_str or "合计" in date_str:
            return None

        try:
            tx_date = date.fromisoformat(date_str)
        except ValueError:
            return None

        amount = self._parse_amount(row)
        if amount is None:
            return None

        summary = row[self.COL_SUMMARY]
        detail = row[self.COL_DETAIL] if len(row) > self.COL_DETAIL else ""
        location = row[self.COL_LOCATION] if len(row) > self.COL_LOCATION else ""
        counterparty = (
            row[self.COL_COUNTERPARTY] if len(row) > self.COL_COUNTERPARTY else ""
        )
        counter_account = (
            row[self.COL_COUNTER_ACCOUNT].strip()
            if len(row) > self.COL_COUNTER_ACCOUNT
            else ""
        )

        balance = (
            row[self.COL_BALANCE].replace(",", "").strip()
            if len(row) > self.COL_BALANCE
            else ""
        )
        description = join_description(summary, detail, location)

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
                **(
                    {"counterparty_account": counter_account} if counter_account else {}
                ),
            },
        )

    def _parse_amount(self, row: list[str]) -> Decimal | None:
        expense_str = row[self.COL_EXPENSE] if len(row) > self.COL_EXPENSE else ""
        income_str = row[self.COL_INCOME] if len(row) > self.COL_INCOME else ""

        expense = to_decimal(expense_str)
        income = to_decimal(income_str)

        if expense is not None:
            return expense
        if income is not None:
            return -income
        return None
