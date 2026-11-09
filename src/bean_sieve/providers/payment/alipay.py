"""Alipay statement provider."""

from __future__ import annotations

import csv
import re
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from ...core.metadata_keys import REBATE_TOPUP_MATCH
from ...core.preset_rules import PresetRule, PresetRuleAction, PresetRuleCondition
from ...core.types import ReconcileContext, Transaction
from .. import register_provider
from ..base import BaseProvider
from ._utils import extract_card_last4


class AlipayTxType(StrEnum):
    """Alipay transaction type (收/支 column)."""

    EXPENSE = "支出"
    INCOME = "收入"
    NEUTRAL = "不计收支"
    EMPTY = ""


ALIPAY_HEADER_LINES = 24


def _refund_original_order_id(order_id: str) -> str | None:
    """Extract the original order ID from a refund order ID.

    Known refund order ID formats:
    - old: ``{original}_{suffix}``
    - new (since 2026): ``{original}*REFUND_{suffix}``

    Returns None if order_id carries no refund suffix.
    """
    original = re.split(r"[*_]", order_id, maxsplit=1)[0]
    return original if original and original != order_id else None


# 起始时间：[2025-01-01 00:00:00]    终止时间：[2025-01-31 23:59:59]
STATEMENT_PERIOD_REGEX = re.compile(
    r"起始时间[：:]\s*\[?(\d{4}-\d{2}-\d{2})\s+\d{2}:\d{2}:\d{2}\]?\s+"
    r"终止时间[：:]\s*\[?(\d{4}-\d{2}-\d{2})\s+\d{2}:\d{2}:\d{2}\]?"
)

# Payment-method segments after "&" that Alipay funds itself
REBATE_METHOD_REGEX = re.compile(r"红包|立减|优惠|补贴|满减")


@register_provider
class AlipayProvider(BaseProvider):
    """
    Provider for Alipay (支付宝) statement CSV files.

    Alipay exports statements as GBK-encoded CSV files with:
    - 24 header lines before the column header row
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
        transactions = []

        with open(file_path, encoding="gbk", newline="") as f:
            content = f.read()

        lines = content.splitlines()

        header_text = "\n".join(lines[:ALIPAY_HEADER_LINES])
        statement_period = self._extract_statement_period(header_text)

        reader = csv.reader(lines)

        for line_num, row in enumerate(reader):
            if line_num < ALIPAY_HEADER_LINES:
                continue

            if not row or len(row) < 10:
                continue

            if row[0] == "交易时间":
                continue

            try:
                txn = self._parse_row(row, file_path, line_num + 1, statement_period)
                if txn:
                    transactions.append(txn)
            except Exception as e:
                import logging

                logging.warning(
                    f"Failed to parse line {line_num + 1} in {file_path}: {e}"
                )
                continue

        return self._post_process(transactions)

    def _parse_row(
        self,
        row: list[str],
        file_path: Path,
        line_num: int,
        statement_period: tuple[date, date] | None = None,
    ) -> Transaction | None:
        row = [field.strip() for field in row]

        tx_time_str = row[0]
        category = row[1]
        peer = row[2]
        peer_account = row[3]
        item_name = row[4]
        tx_type_str = row[5]
        amount_str = row[6]
        method, *method_extras = (part.strip() for part in row[7].split("&"))
        status = row[8]
        order_id = row[9].strip() if len(row) > 9 else ""
        merchant_id = row[10].strip() if len(row) > 10 else ""
        remarks = row[11].strip() if len(row) > 11 else ""

        tx_type = self._get_tx_type(tx_type_str)
        if tx_type == AlipayTxType.EMPTY:
            return None

        tx_datetime = datetime.strptime(tx_time_str, "%Y-%m-%d %H:%M:%S")

        amount = Decimal(amount_str)

        # Expenses are positive, income negative.
        if tx_type == AlipayTxType.INCOME and amount > 0:
            amount = -amount

        description = item_name if item_name else category

        metadata: dict[str, object] = {
            "category": category,
            "peer_account": peer_account,
            "method": method,
            "status": status,
            "merchant_id": merchant_id,
            "tx_type": tx_type_str,
            "remarks": remarks,
        }
        # A purchase paid with "card&红包" reports the net card charge, but a
        # credit repayment reports the gross amount the card was credited with,
        # which the ledger splits into the fund leg and an Income:Rebate leg.
        if category == "信用借还" and any(
            REBATE_METHOD_REGEX.search(extra) for extra in method_extras
        ):
            metadata[REBATE_TOPUP_MATCH] = True

        return Transaction(
            date=tx_datetime.date(),
            time=tx_datetime.time(),
            amount=amount,
            currency="CNY",
            description=description,
            payee=peer,
            order_id=order_id,
            card_last4=self._extract_card_last4(method),
            provider=self.provider_id,
            source_file=file_path,
            source_line=line_num,
            statement_period=statement_period,
            metadata=metadata,
        )

    _extract_card_last4 = staticmethod(extract_card_last4)

    def _get_tx_type(self, tx_type_str: str) -> AlipayTxType:
        tx_type_str = tx_type_str.strip()
        try:
            return AlipayTxType(tx_type_str)
        except ValueError:
            return AlipayTxType.EMPTY

    def _extract_statement_period(self, header_text: str) -> tuple[date, date] | None:
        match = STATEMENT_PERIOD_REGEX.search(header_text)
        if match:
            start_date = date.fromisoformat(match.group(1))
            end_date = date.fromisoformat(match.group(2))
            return (start_date, end_date)
        return None

    def _post_process(self, transactions: list[Transaction]) -> list[Transaction]:
        """
        Drop zero-amount rows (e.g. 碰一下立减) and closed orders without a refund,
        and link each refund and its original order with ^<original order ID>.
        """
        filtered = [txn for txn in transactions if txn.amount != 0]

        refunded_order_ids: set[str] = set()
        for txn in filtered:
            status = txn.metadata.get("status", "")
            if status == "退款成功" and txn.order_id:
                original_id = _refund_original_order_id(txn.order_id)
                if original_id:
                    refunded_order_ids.add(original_id)

        result = []
        for txn in filtered:
            status = txn.metadata.get("status", "")

            # A closed order without a refund row never settled, whatever its
            # 收/支 (e.g. a 闲鱼 收入 order cancelled before completion); one with a
            # refund moved money and is kept so the linked pair nets to zero.
            if (
                status in ("交易关闭", "已关闭")
                and txn.order_id not in refunded_order_ids
            ):
                continue

            if txn.order_id:
                if status == "退款成功" and (
                    original_id := _refund_original_order_id(txn.order_id)
                ):
                    txn = txn.model_copy(update={"links": [original_id]})
                elif txn.order_id in refunded_order_ids:
                    txn = txn.model_copy(update={"links": [txn.order_id]})

            result.append(txn)

        return result

    @classmethod
    def get_preset_rules(cls) -> list[PresetRule]:
        return [
            # 余额宝转入、收益：资金流入余额宝，金额取反。
            PresetRule(
                rule_id="alipay_yuebao_in",
                name="余额宝转入",
                provider="alipay",
                condition=PresetRuleCondition(description=r"余额宝.*转入"),
                action=PresetRuleAction(account_keyword="余额宝", negate=True),
                priority=110,
            ),
            PresetRule(
                rule_id="alipay_yuebao_income",
                name="余额宝收益",
                provider="alipay",
                condition=PresetRuleCondition(description=r"余额宝.*收益"),
                action=PresetRuleAction(account_keyword="余额宝", negate=True),
                priority=110,
            ),
            PresetRule(
                rule_id="alipay_yuebao_out",
                name="余额宝转出",
                provider="alipay",
                condition=PresetRuleCondition(description=r"余额宝.*转出"),
                action=PresetRuleAction(account_keyword="余额宝"),
                priority=110,
            ),
            # 基金赎回、卖出的资金流入余额宝，金额取反。
            PresetRule(
                rule_id="alipay_fund_redeem_yuebao",
                name="基金赎回到余额宝",
                provider="alipay",
                condition=PresetRuleCondition(
                    description=r"赎回",
                    metadata={"method": r"余额宝"},
                ),
                action=PresetRuleAction(account_keyword="余额宝", negate=True),
                priority=110,
            ),
            PresetRule(
                rule_id="alipay_fund_sell_yuebao",
                name="基金卖出到余额宝",
                provider="alipay",
                condition=PresetRuleCondition(description=r"卖出至余额宝"),
                action=PresetRuleAction(account_keyword="余额宝", negate=True),
                priority=110,
            ),
            # 仅收入/不计收支的退款取反：用户退款给他人时 tx_type 为支出，
            # 金额方向已正确。
            PresetRule(
                rule_id="alipay_refund",
                name="退款",
                provider="alipay",
                condition=PresetRuleCondition(
                    description=r"^退款",
                    metadata={"tx_type": r"^(收入|不计收支)$"},
                ),
                action=PresetRuleAction(negate=True),
                priority=100,
            ),
            # 收益发放是利息收入，取反为负数。
            PresetRule(
                rule_id="alipay_interest",
                name="收益发放",
                provider="alipay",
                condition=PresetRuleCondition(description=r"收益发放"),
                action=PresetRuleAction(negate=True),
                priority=100,
            ),
            # 花呗还款在账单里是一行，在账本里是银行卡和花呗两条余额表分录。
            # 这一行匹配花呗分录（流入花呗、冲减负债，故取反），银行卡分录由
            # 对应银行流水匹配；否则花呗分录无人匹配，报为 Extra。
            PresetRule(
                rule_id="alipay_huabei_repay",
                name="花呗还款",
                provider="alipay",
                condition=PresetRuleCondition(
                    description=r"花呗.*还款",
                    metadata={"category": r"信用借还"},
                ),
                action=PresetRuleAction(account_keyword="花呗", negate=True),
                priority=95,
            ),
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
        Merge rows sharing a timestamp: Taobao records a multi-item order as one
        row per item, while the ledger books it as one transaction.
        """
        from collections import defaultdict

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
        first = txns[0]

        total_amount = sum((t.amount for t in txns), Decimal(0))

        payees = []
        seen_payees: set[str] = set()
        for t in txns:
            if t.payee and t.payee not in seen_payees:
                payees.append(t.payee)
                seen_payees.add(t.payee)
        merged_payee = " ".join(payees) if payees else first.payee

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
            order_id=None,  # The merged row stands for several orders.
            card_last4=first.card_last4,
            provider=first.provider,
            source_file=first.source_file,
            source_line=first.source_line,
            metadata=first.metadata,
        )
