"""China Guangfa Bank (广发银行) credit card statement provider."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from ....core.types import Transaction
from ... import register_provider
from ...base import BaseProvider


@register_provider
class CGBCreditProvider(BaseProvider):
    """
    Provider for China Guangfa Bank (广发银行) credit card email statements.

    Parses .eml files containing base64-encoded HTML statements.

    File format:
    - Encoding: GBK (base64 encoded)
    - Statement period: 账单周期:YYYY/MM/DD-YYYY/MM/DD
    - Card sections: 卡号：6200********1234 followed by transaction table
    - Transaction row: 交易日期 入账日期 (类型)摘要 金额 货币 入账金额 入账货币

    Transaction types:
    - (消费): spending, positive amount
    - (还款): payment/refund, negative amount
    - (赠送): bonus/reward, negative amount
    """

    provider_id = "cgb_credit"
    provider_name = "广发银行信用卡"
    supported_formats = [".eml"]
    filename_keywords = ["广发信用卡", "广发银行"]
    content_keywords = ["广发银行信用卡"]
    per_card_statement = (
        True  # CGB sends combined statement but needs per-card tracking
    )

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse CGB credit card email statement."""
        html = self.extract_html_from_eml(file_path)
        soup = self.parse_html(html)
        text = soup.get_text()

        statement_period = self._extract_statement_period(text)
        transactions: list[Transaction] = []

        # Split by card sections
        sections = re.split(r"(卡号：\d{4}\*{8}\d{4})", text)

        current_card: str | None = None
        row_counter = 0

        for section in sections:
            # Check if this section is a card header
            card_match = re.match(r"卡号：\d{4}\*{8}(\d{4})", section)
            if card_match:
                current_card = card_match.group(1)
                continue

            if not current_card:
                continue

            # Parse transactions in this card section
            card_transactions = self._parse_card_section(
                section, current_card, file_path, row_counter, statement_period
            )
            transactions.extend(card_transactions)
            row_counter += len(card_transactions)

        return transactions

    def _extract_statement_period(self, text: str) -> tuple[date, date] | None:
        """Extract statement period (e.g., '账单周期:2025/12/26-2026/01/25')."""
        match = re.search(
            r"账单周期:(\d{4})/(\d{2})/(\d{2})-(\d{4})/(\d{2})/(\d{2})", text
        )
        if match:
            start = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            end = date(int(match.group(4)), int(match.group(5)), int(match.group(6)))
            return (start, end)
        return None

    def _parse_card_section(
        self,
        section: str,
        card_last4: str,
        file_path: Path,
        start_row: int,
        statement_period: tuple[date, date] | None,
    ) -> list[Transaction]:
        """Parse all transactions in a card section."""
        transactions: list[Transaction] = []

        # Pattern: 交易日期 入账日期 (类型)摘要 交易金额 交易货币 入账金额 入账货币
        pattern = (
            r"(\d{4}/\d{2}/\d{2})\s+"  # Transaction date
            r"(\d{4}/\d{2}/\d{2})\s+"  # Posting date
            r"\(([^)]+)\)"  # Transaction type (消费/还款/赠送)
            r"(.+?)\s+"  # Description
            r"([-\d,.]+)\s+"  # Transaction amount
            r"(人民币|美元)\s+"  # Transaction currency
            r"([-\d,.]+)\s+"  # Posting amount
            r"(人民币|美元)"  # Posting currency
        )

        for i, match in enumerate(re.finditer(pattern, section)):
            txn = self._parse_transaction(
                match, card_last4, file_path, start_row + i + 1, statement_period
            )
            if txn:
                transactions.append(txn)

        return transactions

    def _parse_transaction(
        self,
        match: re.Match,
        card_last4: str,
        file_path: Path,
        row_idx: int,
        statement_period: tuple[date, date] | None,
    ) -> Transaction | None:
        """Parse a single transaction from regex match."""
        try:
            trans_date_str = match.group(1)  # YYYY/MM/DD
            post_date_str = match.group(2)  # YYYY/MM/DD
            trans_type = match.group(3)  # 消费/还款/赠送
            description = match.group(4).strip()
            posting_amount_str = match.group(7)  # Use posting amount (入账金额)
            posting_currency_str = match.group(8)

            # Parse dates
            trans_date = self._parse_date(trans_date_str)
            post_date = self._parse_date(post_date_str)

            # Parse amount
            amount = self._parse_amount(posting_amount_str)
            if amount is None:
                return None

            # Map currency
            currency = "CNY" if posting_currency_str == "人民币" else "USD"

            # Build description with type prefix
            full_description = f"({trans_type}){description}"

            return Transaction(
                date=trans_date,
                post_date=post_date,
                amount=amount,
                currency=currency,
                description=full_description,
                card_last4=card_last4,
                provider=self.provider_id,
                source_file=file_path,
                source_line=row_idx,
                statement_period=statement_period,
                metadata={
                    "original_date": trans_date_str,
                    "trans_type": trans_type,
                },
            )
        except (IndexError, ValueError):
            return None

    def _parse_date(self, date_str: str) -> date:
        """Parse date from YYYY/MM/DD format."""
        parts = date_str.split("/")
        return date(int(parts[0]), int(parts[1]), int(parts[2]))

    def _parse_amount(self, amount_str: str) -> Decimal | None:
        """Parse amount string, handling commas and negative values."""
        try:
            cleaned = amount_str.replace(",", "")
            return Decimal(cleaned)
        except Exception:
            return None
