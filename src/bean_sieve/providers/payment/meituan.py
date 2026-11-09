"""Meituan (美团) payment platform statement provider."""

from __future__ import annotations

import csv
import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path

from ...core.types import Transaction
from .. import register_provider
from ..base import BaseProvider
from ._utils import extract_card_last4


class MeituanTxType(StrEnum):
    """Meituan order direction (收/支 column)."""

    EXPENSE = "支出"
    INCOME = "收入"
    NEUTRAL = "不计收支"
    EMPTY = ""


# The column header row is at index 19; data rows start at index 20.
MEITUAN_HEADER_LINES = 20

# Date only, unlike Alipay and WeChat: 起始时间：[2025-01-01] 终止时间：[2025-01-31]
STATEMENT_PERIOD_REGEX = re.compile(
    r"起始时间[：:]\s*\[?(\d{4}-\d{2}-\d{2})\]?\s+"
    r"终止时间[：:]\s*\[?(\d{4}-\d{2}-\d{2})\]?"
)


@register_provider
class MeituanProvider(BaseProvider):
    """
    Provider for Meituan (美团) statement CSV files.

    Meituan exports statements as UTF-8 (with BOM) CSV files with:
    - 20 header lines (metadata, statistics, tips, section title, column header)
    - Data columns:
      0: 交易创建时间 (Creation time)
      1: 交易成功时间 (Success time)
      2: 交易类型 (Transaction type, e.g. 支付/退款)
      3: 订单标题 (Order title)
      4: 收/支 (Income/Expense)
      5: 支付方式 (Payment method)
      6: 订单金额 (Order amount with ¥ prefix)
      7: 实付金额 (Actual paid amount with ¥ prefix)
      8: 交易单号 (Order ID)
      9: 商家单号 (Merchant order ID)
      10: 备注 (Remarks)

    Notes:
    - There is no counterparty column; the order title reads
      ``商户名-商品/订单号``, and the part before the first ``-`` is the payee.
    - Refund (退款) rows are standalone income tagged ``#refund``: a refund's
      order ID shares no prefix with the original payment, so it cannot be
      linked to it.
    """

    provider_id = "meituan"
    provider_name = "美团"
    supported_formats = [".csv"]
    filename_keywords = ["美团"]
    content_keywords = ["美团交易账单明细"]

    _extract_card_last4 = staticmethod(extract_card_last4)

    def parse(self, file_path: Path) -> list[Transaction]:
        transactions: list[Transaction] = []

        with open(file_path, encoding="utf-8-sig", newline="") as f:
            content = f.read()

        lines = content.splitlines()

        header_text = "\n".join(lines[:MEITUAN_HEADER_LINES])
        statement_period = self._extract_statement_period(header_text)

        reader = csv.reader(lines)

        for line_num, row in enumerate(reader):
            if line_num < MEITUAN_HEADER_LINES:
                continue

            if not row or len(row) < 8:
                continue

            # Guards against a header block longer than MEITUAN_HEADER_LINES.
            if row[0].strip() == "交易创建时间":
                continue

            try:
                txn = self._parse_row(row, file_path, line_num + 1, statement_period)
                if txn:
                    transactions.append(txn)
            except Exception as e:
                logging.warning(
                    f"Failed to parse line {line_num + 1} in {file_path}: {e}"
                )
                continue

        return transactions

    def _parse_row(
        self,
        row: list[str],
        file_path: Path,
        line_num: int,
        statement_period: tuple[date, date] | None = None,
    ) -> Transaction | None:
        # Meituan appends a tab to order and merchant IDs.
        row = [field.strip() for field in row]

        create_time_str = row[0]
        success_time_str = row[1]
        tx_type_str = row[2]
        title = row[3]
        direction_str = row[4]
        method = row[5]
        order_amount_str = row[6]
        paid_amount_str = row[7]
        order_id = row[8] if len(row) > 8 else ""
        merchant_id = row[9] if len(row) > 9 else ""
        remarks = row[10] if len(row) > 10 else ""

        direction = self._get_direction(direction_str)
        if direction in (MeituanTxType.EMPTY, MeituanTxType.NEUTRAL):
            return None

        time_str = success_time_str or create_time_str
        tx_datetime = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")

        # 实付金额 is what left the account; 订单金额 is before discounts.
        amount = self._parse_amount(paid_amount_str)
        if amount is None:
            return None

        # Expenses are positive, income negative.
        if direction == MeituanTxType.INCOME and amount > 0:
            amount = -amount

        # A title without "-" (e.g. "话费充值100元") has no payee.
        payee: str | None = None
        description = title
        if "-" in title:
            prefix, remainder = title.split("-", 1)
            payee = prefix.strip() or None
            description = remainder.strip() or title

        metadata: dict[str, str] = {
            "tx_type": tx_type_str,
            "method": method,
        }
        if merchant_id:
            metadata["merchant_id"] = merchant_id
        if remarks and remarks != "/":
            metadata["remarks"] = remarks
        order_amount = self._parse_amount(order_amount_str)
        if order_amount is not None and order_amount != abs(amount):
            metadata["order_amount"] = str(order_amount)

        tags = ["refund"] if tx_type_str == "退款" else []

        return Transaction(
            date=tx_datetime.date(),
            time=tx_datetime.time(),
            amount=amount,
            currency="CNY",
            description=description,
            payee=payee,
            order_id=order_id or None,
            card_last4=self._extract_card_last4(method),
            provider=self.provider_id,
            source_file=file_path,
            source_line=line_num,
            statement_period=statement_period,
            tags=tags,
            metadata=metadata,
        )

    @staticmethod
    def _parse_amount(value: str) -> Decimal | None:
        cleaned = value.lstrip("¥￥").replace(",", "").strip()
        if not cleaned:
            return None
        try:
            return Decimal(cleaned)
        except (InvalidOperation, ValueError):
            return None

    def _get_direction(self, direction_str: str) -> MeituanTxType:
        direction_str = direction_str.strip()
        try:
            return MeituanTxType(direction_str)
        except ValueError:
            return MeituanTxType.EMPTY

    def _extract_statement_period(self, header_text: str) -> tuple[date, date] | None:
        match = STATEMENT_PERIOD_REGEX.search(header_text)
        if match:
            start_date = date.fromisoformat(match.group(1))
            end_date = date.fromisoformat(match.group(2))
            return (start_date, end_date)
        return None
