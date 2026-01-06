"""Alipay statement provider."""

from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

from ...core.preset_rules import PresetRule, PresetRuleAction, PresetRuleCondition
from ...core.types import ReconcileContext, Transaction
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
    filename_keywords = ["支付宝", "alipay"]
    content_keywords = ["支付宝交易记录明细", "支付宝（中国）"]

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
        method = row[7].split("&")[0].strip()
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
        if tx_type == AlipayTxType.INCOME and amount > 0:
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
        Post-process transactions to handle special cases.

        - Filter all zero-amount transactions (e.g., discounts like "碰一下立减")
        - Mark closed transactions as useless
        """
        # First pass: filter zero-amount transactions
        filtered = [txn for txn in transactions if txn.amount != 0]

        result = []
        for txn in filtered:
            status = txn.metadata.get("status", "")
            tx_type = txn.metadata.get("tx_type", "")

            # Skip closed transactions that are marked as neutral
            if status == "交易关闭" and tx_type == "不计收支":
                continue

            result.append(txn)

        return result

    @classmethod
    def get_preset_rules(cls) -> list[PresetRule]:
        """Return preset rules for Alipay transactions."""
        return [
            # 余额宝转入（资金流入余额宝，需要翻转金额符号）
            PresetRule(
                rule_id="alipay_yuebao_in",
                name="余额宝转入",
                provider="alipay",
                condition=PresetRuleCondition(description=r"余额宝.*转入"),
                action=PresetRuleAction(account_keyword="余额宝", negate=True),
                priority=110,
            ),
            # 余额宝收益（资金流入余额宝，需要翻转金额符号）
            PresetRule(
                rule_id="alipay_yuebao_income",
                name="余额宝收益",
                provider="alipay",
                condition=PresetRuleCondition(description=r"余额宝.*收益"),
                action=PresetRuleAction(account_keyword="余额宝", negate=True),
                priority=110,
            ),
            # 余额宝转出（资金流出余额宝）
            PresetRule(
                rule_id="alipay_yuebao_out",
                name="余额宝转出",
                provider="alipay",
                condition=PresetRuleCondition(description=r"余额宝.*转出"),
                action=PresetRuleAction(account_keyword="余额宝"),
                priority=110,
            ),
            # 退款（只对正数金额翻转，避免双重翻转）
            PresetRule(
                rule_id="alipay_refund",
                name="退款",
                provider="alipay",
                condition=PresetRuleCondition(description=r"^退款"),
                action=PresetRuleAction(negate=True),
                priority=100,
            ),
            # 收益发放（利息收入，需要翻转为负数表示收入）
            PresetRule(
                rule_id="alipay_interest",
                name="收益发放",
                provider="alipay",
                condition=PresetRuleCondition(description=r"收益发放"),
                action=PresetRuleAction(negate=True),
                priority=100,
            ),
            # 花呗消费
            PresetRule(
                rule_id="alipay_huabei",
                name="花呗消费",
                provider="alipay",
                condition=PresetRuleCondition(
                    metadata={"method": r"花呗"},
                ),
                action=PresetRuleAction(account_keyword="花呗"),
                priority=90,
            ),
            # 余额支付
            PresetRule(
                rule_id="alipay_balance",
                name="余额支付",
                provider="alipay",
                condition=PresetRuleCondition(
                    metadata={"method": r"^余额$"},
                ),
                action=PresetRuleAction(account_keyword="余额"),
                priority=80,
            ),
        ]

    def pre_reconcile(
        self,
        transactions: list[Transaction],
        context: ReconcileContext,  # noqa: ARG002
    ) -> list[Transaction]:
        """
        Merge transactions with identical timestamps.

        Taobao orders with multiple items are often recorded as separate
        transactions with the same timestamp. This merges them for matching.
        """
        from collections import defaultdict

        # Group by (date, time)
        groups: dict[tuple, list[Transaction]] = defaultdict(list)
        for txn in transactions:
            key = (txn.date, txn.time)
            groups[key].append(txn)

        result = []
        for group in groups.values():
            if len(group) == 1:
                result.append(group[0])
            else:
                result.append(self._merge_transactions(group))

        return result

    def _merge_transactions(self, txns: list[Transaction]) -> Transaction:
        """Merge multiple transactions into one."""
        first = txns[0]

        # Sum amounts
        total_amount = sum(t.amount for t in txns)

        # Merge payees (unique, preserve order)
        payees = []
        seen_payees: set[str] = set()
        for t in txns:
            if t.payee and t.payee not in seen_payees:
                payees.append(t.payee)
                seen_payees.add(t.payee)
        merged_payee = " ".join(payees) if payees else first.payee

        # Merge descriptions (truncate to 8 chars each if too long)
        descriptions = []
        seen_desc: set[str] = set()
        for t in txns:
            if t.description and t.description not in seen_desc:
                desc = t.description[:8] if len(t.description) > 8 else t.description
                descriptions.append(desc)
                seen_desc.add(t.description)
        merged_desc = "/".join(descriptions)

        return Transaction(
            date=first.date,
            time=first.time,
            amount=total_amount,
            currency=first.currency,
            description=merged_desc,
            payee=merged_payee,
            order_id=None,  # Clear order_id for merged transactions
            provider=first.provider,
            source_file=first.source_file,
            source_line=first.source_line,
            metadata=first.metadata,
        )
