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


# Number of header lines to skip in Meituan CSV (metadata + tips + section title
# + column header row at index 19; data rows start at index 20).
MEITUAN_HEADER_LINES = 20

# Regex to extract statement period from header.
# Meituan uses date-only (no time): 起始时间：[2025-01-01] 终止时间：[2025-01-31]
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
    - Meituan has no dedicated counterparty column; the merchant is embedded in
      the order title (``商户名-商品/订单号``). The prefix before the first ``-``
      becomes the payee and the remainder becomes the description (so the
      merchant is not repeated); titles without ``-`` leave payee empty and
      keep the whole title as description.
    - Refund (退款) rows are emitted as standalone income transactions tagged
      ``#refund``. Meituan refund order IDs share no prefix with the original
      payment, so they are only tagged, not linked back to the original order.
    """

    provider_id = "meituan"
    provider_name = "美团"
    supported_formats = [".csv"]
    filename_keywords = ["美团"]
    content_keywords = ["美团交易账单明细"]

    _extract_card_last4 = staticmethod(extract_card_last4)

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse Meituan CSV statement file."""
        transactions: list[Transaction] = []

        with open(file_path, encoding="utf-8-sig", newline="") as f:
            content = f.read()

        lines = content.splitlines()

        # Extract statement period from header
        header_text = "\n".join(lines[:MEITUAN_HEADER_LINES])
        statement_period = self._extract_statement_period(header_text)

        reader = csv.reader(lines)

        for line_num, row in enumerate(reader):
            # Skip header lines
            if line_num < MEITUAN_HEADER_LINES:
                continue

            # Skip empty rows or rows that don't look like data
            if not row or len(row) < 8:
                continue

            # Skip the column header row (in case header count drifts)
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
        """Parse a single CSV row into a Transaction."""
        # Clean whitespace (Meituan appends a tab to order/merchant IDs)
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

        # Parse direction (收/支)
        direction = self._get_direction(direction_str)
        if direction in (MeituanTxType.EMPTY, MeituanTxType.NEUTRAL):
            return None

        # Prefer success time; fall back to creation time if absent
        time_str = success_time_str or create_time_str
        tx_datetime = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")

        # Amount: use actual paid amount (what really left the account)
        amount = self._parse_amount(paid_amount_str)
        if amount is None:
            return None

        # Convention: expense is positive, income is negative
        if direction == MeituanTxType.INCOME and amount > 0:
            amount = -amount

        # Meituan titles look like "商户名-商品/订单号". Split on the first "-":
        # the prefix is the merchant (payee), the remainder is the description.
        # Titles without "-" (e.g. "江苏联通200元") leave payee empty and keep
        # the whole title as the description.
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
        # Keep the nominal order amount when it differs from paid (discount/coupon)
        order_amount = self._parse_amount(order_amount_str)
        if order_amount is not None and order_amount != abs(amount):
            metadata["order_amount"] = str(order_amount)

        # Tag refunds (#refund) — Meituan provides no linkable original order ID,
        # so we only mark them rather than linking to the original payment.
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
        """Parse an amount string like '¥42.50' or '42.50' into Decimal."""
        cleaned = value.lstrip("¥￥").replace(",", "").strip()
        if not cleaned:
            return None
        try:
            return Decimal(cleaned)
        except (InvalidOperation, ValueError):
            return None

    def _get_direction(self, direction_str: str) -> MeituanTxType:
        """Convert 收/支 string to enum."""
        direction_str = direction_str.strip()
        try:
            return MeituanTxType(direction_str)
        except ValueError:
            return MeituanTxType.EMPTY

    def _extract_statement_period(self, header_text: str) -> tuple[date, date] | None:
        """Extract statement period from header text.

        Meituan format: 起始时间：[2025-01-01] 终止时间：[2025-01-31]
        """
        match = STATEMENT_PERIOD_REGEX.search(header_text)
        if match:
            start_date = date.fromisoformat(match.group(1))
            end_date = date.fromisoformat(match.group(2))
            return (start_date, end_date)
        return None
