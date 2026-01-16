"""Huaxia Bank (华夏银行) credit card statement provider."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from ....core.types import ReconcileContext, Transaction
from ... import register_provider
from ...base import BaseProvider


@register_provider
class HXBCreditProvider(BaseProvider):
    """
    Provider for Huaxia Bank (华夏银行) credit card email statements.

    Parses .eml files containing base64-encoded HTML statements.
    Lifecycle hooks:
    - pre_reconcile: Store card_last4 in metadata for output
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
            card_last4=card,
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
        context: ReconcileContext,  # noqa: ARG002
    ) -> list[Transaction]:
        """Store card_last4 in metadata for output."""
        for txn in transactions:
            if txn.card_last4:
                txn.metadata["card_last4"] = txn.card_last4

        return transactions
