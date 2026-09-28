"""CGB credit card statement provider."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from ....core.metadata_keys import ORIGINAL_AMOUNT, ORIGINAL_CURRENCY
from ....core.types import Transaction
from ... import register_provider
from ...base import BaseProvider

# Issuer name written as escapes
_ISSUER_SHORT = "\u5e7f\u53d1"
_ISSUER = f"{_ISSUER_SHORT}\u94f6\u884c"

_CURRENCY_CODES = {
    "人民币": "CNY",
    "美元": "USD",
    "港币": "HKD",
    "澳门元": "MOP",
    "新台币": "TWD",
    "欧元": "EUR",
    "英镑": "GBP",
    "日元": "JPY",
    "韩元": "KRW",
    "新加坡元": "SGD",
    "澳大利亚元": "AUD",
    "加拿大元": "CAD",
    "瑞士法郎": "CHF",
    "泰铢": "THB",
}


@register_provider
class CGBCreditProvider(BaseProvider):
    """
    Provider for CGB credit card email statements.

    Parses .eml files containing base64-encoded HTML statements.

    File format:
    - Encoding: GBK (base64 encoded)
    - Statement period: 账单周期:YYYY/MM/DD-YYYY/MM/DD
    - Card sections: 卡号：6200********1234 followed by transaction table
    - Transaction row: 交易日期 入账日期 (类型)摘要 金额 货币 入账金额 入账货币
    - A foreign-currency card bills in its own currency (e.g. 美元) and lists
      spending in a third currency (e.g. 港币) with the original amount

    Transaction types:
    - (消费): spending, positive amount
    - (还款): payment/refund, negative amount
    - (赠送): bonus/reward, negative amount
    """

    provider_id = "cgb_credit"
    provider_name = f"{_ISSUER}信用卡"
    supported_formats = [".eml"]
    filename_keywords = [f"{_ISSUER_SHORT}信用卡", _ISSUER]
    content_keywords = [f"{_ISSUER}信用卡"]
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
                section, current_card, file_path, row_counter
            )
            transactions.extend(card_transactions)
            row_counter += len(card_transactions)

        self.assign_statement_periods(transactions, statement_period)
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
            r"([\u4e00-\u9fff]+)\s+"  # Transaction currency
            r"([-\d,.]+)\s+"  # Posting amount
            r"([\u4e00-\u9fff]+)"  # Posting currency
        )

        for i, match in enumerate(re.finditer(pattern, section)):
            txn = self._parse_transaction(
                match, card_last4, file_path, start_row + i + 1
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
    ) -> Transaction | None:
        """Parse a single transaction from regex match."""
        try:
            trans_date_str = match.group(1)  # YYYY/MM/DD
            post_date_str = match.group(2)  # YYYY/MM/DD
            trans_type = match.group(3)  # 消费/还款/赠送
            description = match.group(4).strip()
            trans_amount_str = match.group(5)
            trans_currency_str = match.group(6)
            posting_amount_str = match.group(7)  # Use posting amount (入账金额)
            posting_currency_str = match.group(8)

            # Parse dates
            trans_date = self._parse_date(trans_date_str)
            post_date = self._parse_date(post_date_str)

            # Parse amount
            amount = self._parse_amount(posting_amount_str)
            if amount is None:
                return None

            currency = self._map_currency(posting_currency_str)
            original_currency = self._map_currency(trans_currency_str)

            # Build description with type prefix
            full_description = f"({trans_type}){description}"

            metadata: dict = {
                "original_date": trans_date_str,
                "trans_type": trans_type,
            }
            if original_currency != currency:
                original_amount = self._parse_amount(trans_amount_str)
                if original_amount is not None:
                    metadata[ORIGINAL_AMOUNT] = original_amount
                    metadata[ORIGINAL_CURRENCY] = original_currency

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
                metadata=metadata,
            )
        except (IndexError, ValueError):
            return None

    @staticmethod
    def _map_currency(name: str) -> str:
        return _CURRENCY_CODES.get(name, name)

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
