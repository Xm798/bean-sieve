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
    - Header rows: 21 (9 metadata + 10 tips + 1 blank + 1 column header)
    - Non-standard delimiter: first column (交易时间) separated by TAB, rest by comma
    - Columns: 交易时间, 商户名称, 交易说明, 金额, 收/付款方式, 交易状态, 收/支, 交易分类, 交易订单号, 商家订单号, 备注
    """

    provider_id = "jd"
    provider_name = "京东支付"
    supported_formats = [".csv"]
    filename_keywords = ["京东交易流水"]
    content_keywords = ["京东账号名", "交易订单号", "商家订单号"]

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse JD statement file.

        Note: JD CSV uses a non-standard format:
        - First column (交易时间) is separated by TAB
        - Remaining columns are separated by comma
        """
        transactions = []

        with open(file_path, encoding="utf-8-sig") as f:
            # Skip header rows (20 lines of metadata + tips)
            for _ in range(20):
                next(f, None)

            # Skip blank line
            next(f, None)

            # Read header line to get column names
            header_line = next(f, None)
            if not header_line:
                return transactions

            # Parse header (comma-separated)
            columns = [col.strip() for col in header_line.strip().split(",")]

            # Read data rows
            for line_num, line in enumerate(f, start=23):
                try:
                    # Replace all TABs with empty string to normalize format
                    # Format: "datetime\t,field,field,...,field\t,field\t,field,"
                    # After replacing TAB: "datetime,field,field,...,field,field,field,"
                    normalized = line.strip().replace("\t", "")

                    # Split by comma
                    row_data = [field.strip() for field in normalized.split(",")]

                    # Remove empty trailing fields
                    while row_data and row_data[-1] == "":
                        row_data.pop()

                    # Create dict mapping
                    # Note: "备注" column may be missing (empty)
                    if len(row_data) < len(columns) - 1:
                        logging.warning(
                            f"Line {line_num}: too few columns "
                            f"(expected at least {len(columns) - 1}, got {len(row_data)})"
                        )
                        continue

                    # Pad with empty strings if needed
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
        """Parse a single transaction row."""
        # Skip rows with missing critical fields
        if not row.get("交易时间") or not row.get("金额"):
            return None

        # Parse transaction type
        tx_type = row.get("收/支", "").strip()
        # Skip "不计收支" transactions (refunds, internal transfers)
        if tx_type == "不计收支":
            return None

        # Parse amount — handles partial and full refund annotations:
        #   "1174.41(已退款903.43)" → net 270.98
        #   "100.00(已全额退款)" → net 0
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

        # Skip fully refunded transactions (net amount is 0)
        if amount == 0:
            return None

        # JD uses: 支出=positive, 收入=negative (already matches bean-sieve convention)
        if tx_type == "收入":
            amount = -amount

        # Parse datetime
        dt = datetime.strptime(row["交易时间"].strip(), "%Y-%m-%d %H:%M:%S")

        # Extract card last 4 digits from payment method
        payment_method = row.get("收/付款方式", "")
        card_last4 = self._extract_card_last4(payment_method)

        # Build description
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
        """Extract card last 4 digits from payment method string.

        Examples:
        - "数字人民币-中国银行钱包(0637)" -> "0637"
        - "中国银行信用卡(0731)" -> "0731"
        - "京东白条" -> None
        """
        if not payment_method:
            return None

        # Match pattern like "(0637)" or "(1234)"
        match = re.search(r"\((\d{4})\)", payment_method)
        if match:
            return match.group(1)

        return None
