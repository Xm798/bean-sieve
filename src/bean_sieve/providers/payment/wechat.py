"""WeChat Pay statement provider."""

from __future__ import annotations

import csv
import re
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

from openpyxl import load_workbook

from ...core.types import Transaction
from .. import register_provider
from ..base import BaseProvider


class WechatOrderType(str, Enum):
    """WeChat order type (收/支 column)."""

    EXPENSE = "支出"
    INCOME = "收入"
    NEUTRAL = "/"


class WechatTxType(str, Enum):
    """WeChat transaction type (交易类型 column)."""

    CONSUME = "商户消费"
    LUCKY_MONEY = "微信红包"
    TRANSFER = "转账"
    QR_INCOME = "二维码收款"
    QR_SEND = "扫二维码付款"
    GROUP_COLLECT = "群收款"
    REFUND = "退款"
    CASH_TO_CASH = "转入零钱通-来自零钱"
    INTO_CASH = "转入零钱通"
    CASH_IN = "零钱充值"
    CASH_WITHDRAW = "零钱提现"
    CREDIT_CARD_REFUND = "信用卡还款"
    BUY_LICAITONG = "购买理财通"
    CASH_TO_LOOSE_CHANGE = "零钱通转出-到零钱"
    CASH_TO_OTHERS = "零钱通转出"
    FAMILY_CARD = "亲属卡交易"
    SPONSOR_CODE = "赞赏码"
    OTHER = "其他"
    DONATION = "分分捐"


# Number of header lines to skip in WeChat CSV/XLSX
WECHAT_HEADER_LINES = 17

# Regex to extract commission from remarks
COMMISSION_REGEX = re.compile(r"\d+\.\d{2}")


@register_provider
class WechatProvider(BaseProvider):
    """
    Provider for WeChat Pay (微信支付) statement files.

    WeChat exports statements as either CSV or XLSX files with:
    - 17 header lines (metadata, statistics, notes)
    - Data columns:
      0: 交易时间 (Transaction time)
      1: 交易类型 (Transaction type)
      2: 交易对方 (Counterparty)
      3: 商品 (Item)
      4: 收/支 (Income/Expense)
      5: 金额(元) (Amount with ¥ prefix)
      6: 支付方式 (Payment method)
      7: 当前状态 (Status)
      8: 交易单号 (Transaction ID)
      9: 商户单号 (Merchant order ID)
      10: 备注 (Remarks)
    """

    provider_id = "wechat"
    provider_name = "微信支付"
    supported_formats = [".csv", ".xlsx"]

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse WeChat statement file (CSV or XLSX)."""
        suffix = file_path.suffix.lower()

        if suffix == ".xlsx":
            return self._parse_xlsx(file_path)
        else:
            return self._parse_csv(file_path)

    def _parse_xlsx(self, file_path: Path) -> list[Transaction]:
        """Parse WeChat XLSX statement file."""
        transactions = []

        wb = load_workbook(file_path, read_only=True, data_only=True)
        ws = wb.active

        for line_num, row in enumerate(ws.iter_rows(values_only=True)):
            # Skip header lines
            if line_num < WECHAT_HEADER_LINES:
                continue

            # Convert tuple to list and filter None values
            row_data = [str(cell) if cell is not None else "" for cell in row]

            # Skip empty rows
            if not row_data or all(not cell for cell in row_data):
                continue

            try:
                txn = self._parse_row(row_data, file_path, line_num + 1)
                if txn:
                    transactions.append(txn)
            except Exception as e:
                import logging

                logging.warning(
                    f"Failed to parse line {line_num + 1} in {file_path}: {e}"
                )
                continue

        wb.close()
        return transactions

    def _parse_csv(self, file_path: Path) -> list[Transaction]:
        """Parse WeChat CSV statement file."""
        transactions = []

        # Try UTF-8 first, then GBK
        for encoding in ["utf-8", "gbk"]:
            try:
                with open(file_path, encoding=encoding, newline="") as f:
                    content = f.read()
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError(f"Cannot decode file {file_path}")

        # Remove tabs that WeChat adds to CSV
        content = content.replace("\t", "")

        reader = csv.reader(content.splitlines())

        for line_num, row in enumerate(reader):
            # Skip header lines
            if line_num < WECHAT_HEADER_LINES:
                continue

            # Skip empty rows
            if not row or len(row) < 10:
                continue

            try:
                txn = self._parse_row(row, file_path, line_num + 1)
                if txn:
                    transactions.append(txn)
            except Exception as e:
                import logging

                logging.warning(
                    f"Failed to parse line {line_num + 1} in {file_path}: {e}"
                )
                continue

        return transactions

    def _parse_row(
        self, row: list[str], file_path: Path, line_num: int
    ) -> Transaction | None:
        """Parse a single row into a Transaction."""
        # Clean whitespace
        row = [field.strip() for field in row]

        # Extract fields
        tx_time_str = row[0]
        tx_type_str = row[1]
        peer = row[2]
        item = row[3]
        order_type_str = row[4]
        amount_str = row[5]
        method = row[6]
        status = row[7]
        order_id = row[8] if len(row) > 8 else ""
        merchant_id = row[9] if len(row) > 9 else ""
        remarks = row[10] if len(row) > 10 else ""

        # Parse order type
        order_type = self._get_order_type(order_type_str)
        if order_type == WechatOrderType.NEUTRAL:
            # For cash withdraw, treat as income (money coming to your wallet)
            if tx_type_str == WechatTxType.CASH_WITHDRAW.value:
                order_type = WechatOrderType.INCOME
            else:
                return None

        # Parse datetime
        tx_datetime = datetime.strptime(tx_time_str, "%Y-%m-%d %H:%M:%S")

        # Parse amount (remove ¥ prefix)
        amount_str = amount_str.lstrip("¥").strip()
        amount = Decimal(amount_str)

        # Handle commission in remarks
        commission = Decimal("0")
        if "服务费" in remarks:
            match = COMMISSION_REGEX.search(remarks)
            if match:
                commission = Decimal(match.group())
                amount = amount - commission

        # Determine sign based on order type
        # Convention: expense is positive, income is negative
        if order_type == WechatOrderType.INCOME:
            amount = -amount

        # Build description
        description = item if item and item != "/" else tx_type_str

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
                "tx_type": tx_type_str,
                "method": method,
                "status": status,
                "merchant_id": merchant_id,
                "remarks": remarks,
                "order_type": order_type_str,
                "commission": str(commission) if commission else None,
            },
        )

    def _get_order_type(self, order_type_str: str) -> WechatOrderType:
        """Convert order type string to enum."""
        order_type_str = order_type_str.strip()
        try:
            return WechatOrderType(order_type_str)
        except ValueError:
            return WechatOrderType.NEUTRAL
