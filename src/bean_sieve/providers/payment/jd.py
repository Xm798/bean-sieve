"""JD.com (京东) payment platform statement provider."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from ...core.types import Transaction
from .. import register_provider
from ..base import BaseProvider


@register_provider
class JDProvider(BaseProvider):
    """
    Provider for JD.com (京东) payment statements.

    File format:
    - Encoding: UTF-8 with BOM
    - 20 lines of export info and tips, a blank line, then the column header on
      line 22
    - 交易时间 and the two order-ID cells end with a TAB before their comma
    - Columns: 交易时间, 商户名称, 交易说明, 金额, 收/付款方式, 交易状态, 收/支, 交易分类, 交易订单号, 商家订单号, 备注
    """

    provider_id = "jd"
    provider_name = "京东支付"
    supported_formats = [".csv"]
    filename_keywords = ["京东交易流水"]
    content_keywords = ["京东账号名", "交易订单号", "商家订单号"]

    def parse(self, file_path: Path) -> list[Transaction]:
        transactions = []

        with open(file_path, encoding="utf-8-sig") as f:
            for _ in range(20):
                next(f, None)

            next(f, None)

            header_line = next(f, None)
            if not header_line:
                return transactions

            columns = [col.strip() for col in header_line.strip().split(",")]

            for line_num, line in enumerate(f, start=23):
                try:
                    # "datetime\t,field,...,field\t,field\t,field,"
                    normalized = line.strip().replace("\t", "")

                    row_data = [field.strip() for field in normalized.split(",")]

                    while row_data and row_data[-1] == "":
                        row_data.pop()

                    # An empty 备注 is dropped with the trailing fields above.
                    if len(row_data) < len(columns) - 1:
                        logging.warning(
                            f"Line {line_num}: too few columns "
                            f"(expected at least {len(columns) - 1}, got {len(row_data)})"
                        )
                        continue

                    while len(row_data) < len(columns):
                        row_data.append("")

                    row = dict(zip(columns, row_data))
                    tx = self._parse_row(row, file_path, line_num)
                    if tx:
                        transactions.append(tx)
                except Exception as e:
                    logging.warning(f"Failed to parse line {line_num}: {e}")
                    continue

        return transactions

    def _parse_row(
        self, row: dict[str, str], file_path: Path, line_num: int
    ) -> Transaction | None:
        if not row.get("交易时间") or not row.get("金额"):
            return None

        tx_type = row.get("收/支", "").strip()
        # 不计收支 rows are refunds and internal transfers.
        if tx_type == "不计收支":
            return None

        # 金额 may carry a refund note; the row books the net amount:
        #   "300.00(已退款120.00)" → 180.00
        #   "100.00(已全额退款)" → 0
        amount_str = row["金额"].strip()
        refund_amount = Decimal(0)
        partial_match = re.search(r"\(已退款([\d,.]+)\)", amount_str)
        full_refund = "(已全额退款)" in amount_str
        if partial_match:
            refund_amount = Decimal(partial_match.group(1).replace(",", ""))
            amount_str = amount_str[: partial_match.start()]
        elif full_refund:
            amount_str = re.sub(r"\s*\(已全额退款\)", "", amount_str)
        amount = Decimal(amount_str.replace(",", ""))
        if full_refund:
            refund_amount = amount
        amount -= refund_amount

        if amount == 0:
            return None

        # 金额 is unsigned; 收入 becomes negative.
        if tx_type == "收入":
            amount = -amount

        dt = datetime.strptime(row["交易时间"].strip(), "%Y-%m-%d %H:%M:%S")

        payment_method = row.get("收/付款方式", "")
        card_last4 = self._extract_card_last4(payment_method)

        description = row.get("交易说明", "").strip()
        payee = row.get("商户名称", "").strip()

        return Transaction(
            date=dt.date(),
            time=dt.time(),
            amount=amount,
            currency="CNY",
            description=description,
            payee=payee if payee else None,
            order_id=row.get("交易订单号", "").strip() or None,
            card_last4=card_last4,
            provider=self.provider_id,
            source_file=file_path,
            source_line=line_num,
            metadata={
                "method": payment_method,
                "transaction_type": tx_type,
                "transaction_status": row.get("交易状态", "").strip(),
                "transaction_category": row.get("交易分类", "").strip(),
                "merchant_order_id": row.get("商家订单号", "").strip(),
                "notes": row.get("备注", "").strip(),
                **({"refund_amount": str(refund_amount)} if refund_amount else {}),
            },
        )

    def _extract_card_last4(self, payment_method: str) -> str | None:
        """Read the card suffix in parentheses.

        Examples:
        - "数字人民币-中国银行钱包(1234)" -> "1234"
        - "中国银行信用卡(5678)" -> "5678"
        - "京东白条" -> None
        """
        if not payment_method:
            return None

        match = re.search(r"\((\d{4})\)", payment_method)
        if match:
            return match.group(1)

        return None
