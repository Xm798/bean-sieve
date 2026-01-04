"""Alipay statement provider."""

from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

from ...core.types import Transaction
from .. import register_provider
from ..base import BaseProvider


class AlipayTxType(str, Enum):
    """Alipay transaction type (收/支 column)."""

    EXPENSE = "支出"
    INCOME = "收入"
    NEUTRAL = "不计收支"
    EMPTY = ""


# Number of header lines to skip in Alipay CSV
ALIPAY_HEADER_LINES = 24


@register_provider
class AlipayProvider(BaseProvider):
    """
    Provider for Alipay (支付宝) statement CSV files.

    Alipay exports statements as GBK-encoded CSV files with:
    - 24 header lines (metadata, disclaimers, etc.)
    - Data columns:
      0: 交易时间 (Transaction time)
      1: 交易分类 (Category)
      2: 交易对方 (Counterparty)
      3: 对方账号 (Counterparty account)
      4: 商品说明 (Item description)
      5: 收/支 (Income/Expense)
      6: 金额 (Amount)
      7: 收/付款方式 (Payment method)
      8: 交易状态 (Status)
      9: 交易订单号 (Order ID)
      10: 商家订单号 (Merchant order ID)
      11: 备注 (Remarks)
    """

    provider_id = "alipay"
    provider_name = "支付宝"
    supported_formats = [".csv"]

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse Alipay CSV statement file."""
        transactions = []

        with open(file_path, encoding="gbk", newline="") as f:
            reader = csv.reader(f)

            for line_num, row in enumerate(reader):
                # Skip header lines
                if line_num < ALIPAY_HEADER_LINES:
                    continue

                # Skip empty rows or rows that don't look like data
                if not row or len(row) < 10:
                    continue

                # Skip the title row (contains column headers)
                if row[0] == "交易时间":
                    continue

                try:
                    txn = self._parse_row(row, file_path, line_num + 1)
                    if txn:
                        transactions.append(txn)
                except Exception as e:
                    # Log error but continue parsing other rows
                    import logging

                    logging.warning(
                        f"Failed to parse line {line_num + 1} in {file_path}: {e}"
                    )
                    continue

        return self._post_process(transactions)

    def _parse_row(
        self, row: list[str], file_path: Path, line_num: int
    ) -> Transaction | None:
        """Parse a single CSV row into a Transaction."""
        # Clean whitespace from all fields
        row = [field.strip() for field in row]

        # Extract fields
        tx_time_str = row[0]
        category = row[1]
        peer = row[2]
        peer_account = row[3]
        item_name = row[4]
        tx_type_str = row[5]
        amount_str = row[6]
        method = row[7]
        status = row[8]
        order_id = row[9].strip() if len(row) > 9 else ""
        merchant_id = row[10].strip() if len(row) > 10 else ""
        remarks = row[11].strip() if len(row) > 11 else ""

        # Parse transaction type
        tx_type = self._get_tx_type(tx_type_str)
        if tx_type == AlipayTxType.EMPTY:
            return None

        # Parse datetime
        tx_datetime = datetime.strptime(tx_time_str, "%Y-%m-%d %H:%M:%S")

        # Parse amount
        amount = Decimal(amount_str)

        # Determine sign based on transaction type
        # Convention: expense is positive, income is negative
        if tx_type == AlipayTxType.INCOME:
            amount = -amount

        # Build description
        description = item_name if item_name else category

        return Transaction(
            date=tx_datetime.date(),
            time=tx_datetime.time(),
            amount=amount,
            currency="CNY",
            description=description,
            payee=peer,
            order_id=order_id,
            provider=self.provider_id,
            source_file=file_path,
            source_line=line_num,
            metadata={
                "category": category,
                "peer_account": peer_account,
                "method": method,
                "status": status,
                "merchant_id": merchant_id,
                "tx_type": tx_type_str,
                "remarks": remarks,
            },
        )

    def _get_tx_type(self, tx_type_str: str) -> AlipayTxType:
        """Convert transaction type string to enum."""
        tx_type_str = tx_type_str.strip()
        try:
            return AlipayTxType(tx_type_str)
        except ValueError:
            return AlipayTxType.EMPTY

    def _post_process(self, transactions: list[Transaction]) -> list[Transaction]:
        """
        Post-process transactions to handle refunds and closed transactions.

        - Match refund transactions with original purchases and mark both as useless
        - Mark closed transactions as useless
        """
        result = []

        # Build index by order_id prefix for refund matching
        for i, txn in enumerate(transactions):
            status = txn.metadata.get("status", "")
            tx_type = txn.metadata.get("tx_type", "")
            category = txn.metadata.get("category", "")

            # Skip closed transactions that are marked as neutral
            if status == "交易关闭" and tx_type == "不计收支":
                continue

            # Handle refunds
            if status == "退款成功" and category == "退款":
                # Try to find matching original transaction
                matched = False
                for j, other in enumerate(transactions):
                    if (
                        i != j
                        and txn.order_id
                        and other.order_id
                        and txn.order_id.startswith(other.order_id)
                        and abs(txn.amount) == abs(other.amount)
                    ):
                        # Found matching refund pair, skip both
                        matched = True
                        break

                if matched:
                    continue

            result.append(txn)

        return result
