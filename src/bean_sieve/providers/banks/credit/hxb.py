"""Huaxia Bank (华夏银行) credit card statement provider."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from ....core.types import ReconcileContext, ReconcileResult, Transaction
from ... import register_provider
from ...base import BaseProvider

DEFAULT_BILL_ACCOUNT = "Liabilities:Credit:HXB:Bill"


@register_provider
class HXBCreditProvider(BaseProvider):
    """
    Provider for Huaxia Bank (华夏银行) credit card email statements.

    Parses .eml files containing base64-encoded HTML statements.
    Lifecycle hooks:
    - pre_reconcile: Set account based on card_suffix from config
    - post_output: Generate settlement entries
    """

    provider_id = "hxb_credit"
    provider_name = "华夏银行信用卡"
    supported_formats = [".eml"]
    filename_keywords = ["华夏信用卡"]
    content_keywords = ["华夏信用卡对账单"]

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse HXB credit card email statement."""
        html = self.extract_html_from_eml(file_path)
        text = self._html_to_text(html)
        year = self._extract_year_from_path(file_path)

        return self._parse_transactions(text, year, file_path)

    def _html_to_text(self, html: str) -> str:
        """Strip HTML tags and return plain text."""
        return re.sub(r"<[^>]+>", "\n", html)

    def _extract_year_from_path(self, file_path: Path) -> str:
        """Extract year from filename (e.g., '华夏信用卡-电子账单2025年11月.eml')."""
        match = re.search(r"(\d{4})年", file_path.name)
        if match:
            return match.group(1)
        return str(date.today().year)

    def _parse_transactions(
        self, text: str, year: str, file_path: Path
    ) -> list[Transaction]:
        """Parse transactions from statement text."""
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        transactions = []

        i = 0
        in_trans = False

        while i < len(lines):
            if lines[i] == "交易日":
                in_trans = True
                i += 1
                continue
            if lines[i] == "美元账务信息":
                break

            if in_trans and re.match(r"^\d{2}/\d{2}$", lines[i]):
                txn = self._parse_single_transaction(lines, i, year, file_path)
                if txn:
                    transactions.append(txn[0])
                    i = txn[1]
                else:
                    i += 1
            else:
                i += 1

        return transactions

    def _parse_single_transaction(
        self, lines: list[str], start_idx: int, year: str, file_path: Path
    ) -> tuple[Transaction, int] | None:
        """Parse a single transaction starting at start_idx."""
        i = start_idx
        date1 = lines[i]
        i += 1

        # Skip posting date if present
        if i < len(lines) and re.match(r"^\d{2}/\d{2}$", lines[i]):
            i += 1

        # Capture description
        desc_parts = []
        while i < len(lines) and not re.match(r"^\d{4}$", lines[i]):
            desc_parts.append(lines[i])
            i += 1

        description = " ".join(desc_parts)

        # Get card number (4 digits)
        if i >= len(lines) or not re.match(r"^\d{4}$", lines[i]):
            return None

        card = lines[i]
        i += 1

        # Get amount
        if i >= len(lines) or not re.match(r"^[-￥＄]", lines[i]):
            return None

        amt_str = lines[i].replace("￥", "").replace("＄", "").replace(",", "")
        i += 1

        try:
            amount = Decimal(amt_str)
        except Exception:
            return None

        # Parse date
        month, day = date1.split("/")
        iso_date = f"{year}-{month}-{day}"

        # Determine currency (CNY by default, USD if $ symbol)
        currency = "CNY"
        if "＄" in lines[i - 1]:
            currency = "USD"

        txn = Transaction(
            date=date.fromisoformat(iso_date),
            amount=amount,
            currency=currency,
            description=description,
            card_suffix=card,
            provider=self.provider_id,
            source_file=file_path,
            source_line=start_idx + 1,
            metadata={
                "original_date": date1,
            },
        )

        return (txn, i)

    def pre_reconcile(
        self,
        transactions: list[Transaction],
        context: ReconcileContext,
    ) -> list[Transaction]:
        """
        Set account based on card_suffix from config.providers.hxb_credit.accounts.
        """
        provider_config = self._get_provider_config(context)
        accounts = provider_config.accounts

        for txn in transactions:
            if txn.card_suffix and txn.card_suffix in accounts:
                txn.account = accounts[txn.card_suffix]

        return transactions

    def _get_provider_config(self, context: ReconcileContext):
        """Get provider configuration from context."""
        from ....config.schema import ProviderConfig

        if context.config:
            return context.config.get_provider_config(self.provider_id)
        return ProviderConfig()

    def post_output(
        self,
        content: str,
        result: ReconcileResult,
        context: ReconcileContext,
    ) -> str:
        """
        Append settlement entry to output.

        Generates a transaction that transfers card balances to Bill account
        for the statement period.
        """
        if not context.date_range:
            return content

        settlement = self._generate_settlement(result, context)
        if settlement:
            return content + "\n\n" + settlement
        return content

    def _generate_settlement(
        self,
        result: ReconcileResult,
        context: ReconcileContext,
    ) -> str:
        """Generate beancount settlement entry."""
        if not context.date_range:
            return ""

        provider_config = self._get_provider_config(context)
        accounts = provider_config.accounts
        bill_account = provider_config.bill_account or DEFAULT_BILL_ACCOUNT
        end_date = context.date_range[1]

        # Calculate per-card totals from processed transactions
        card_totals: dict[str, Decimal] = defaultdict(Decimal)
        for txn in result.processed:
            if txn.account and txn.account in accounts.values():
                # Extract card suffix from account name
                card = self._extract_card_from_account(txn.account)
                if card:
                    card_totals[card] += txn.amount

        if not card_totals:
            return ""

        # Generate settlement entry
        month = end_date.month
        lines = [f'{end_date.isoformat()} * "华夏银行" "{month}月账单结算"']

        total = Decimal("0")
        for card in sorted(card_totals.keys()):
            account = accounts.get(card, f"Liabilities:Credit:HXB:UNKNOWN-{card}")
            amount = card_totals[card]
            total += amount
            lines.append(f"  {account:<50} {amount:>10.2f} CNY")

        lines.append(f"  {bill_account:<50} {-total:>10.2f} CNY")

        return "\n".join(lines)

    def _extract_card_from_account(self, account: str) -> str | None:
        """Extract card suffix (last 4 digits) from account name."""
        # Handle format like "Liabilities:Credit:HXB:U-王者KPL9270"
        match = re.search(r"(\d{4})$", account)
        if match:
            return match.group(1)
        return None

    def get_statement_card_totals(self, file_path: Path) -> dict[str, Decimal]:
        """
        Parse statement and return per-card net totals (excluding repayments).

        Useful for comparing with ledger totals during settlement.
        """
        html = self.extract_html_from_eml(file_path)
        text = self._html_to_text(html)
        lines = [line.strip() for line in text.split("\n") if line.strip()]

        card_totals: dict[str, Decimal] = defaultdict(Decimal)
        i = 0
        in_trans = False

        while i < len(lines):
            if lines[i] == "交易日":
                in_trans = True
                i += 1
                continue
            if lines[i] == "美元账务信息":
                break

            if in_trans and re.match(r"^\d{2}/\d{2}$", lines[i]):
                i += 1
                # Skip posting date if present
                if i < len(lines) and re.match(r"^\d{2}/\d{2}$", lines[i]):
                    i += 1

                # Capture description
                desc_parts = []
                while i < len(lines) and not re.match(r"^\d{4}$", lines[i]):
                    desc_parts.append(lines[i])
                    i += 1
                desc = " ".join(desc_parts)

                if i < len(lines) and re.match(r"^\d{4}$", lines[i]):
                    card = lines[i]
                    i += 1
                    if i < len(lines) and re.match(r"^[-￥＄]", lines[i]):
                        amt_str = (
                            lines[i]
                            .replace("￥", "")
                            .replace("＄", "")
                            .replace(",", "")
                        )
                        try:
                            amt = Decimal(amt_str)
                            # Skip repayments (还款)
                            if "还款" not in desc:
                                card_totals[card] += amt
                        except Exception:
                            pass
                        i += 1
            else:
                i += 1

        return dict(card_totals)
