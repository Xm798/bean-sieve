"""Huaxia Bank (华夏银行) credit card statement provider."""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from ....core.types import Transaction
from ... import register_provider
from ...base import BaseProvider


@register_provider
class HXBCreditProvider(BaseProvider):
    """
    Provider for Huaxia Bank (华夏银行) credit card email statements.

    Parses .eml files containing base64-encoded HTML statements.
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

        # Extract statement period from HTML content or filename
        statement_period = self._extract_statement_period(html, file_path)
        year = self._extract_year_from_path(file_path)

        return self._parse_transactions(text, year, file_path, statement_period)

    def _html_to_text(self, html: str) -> str:
        """Strip HTML tags and return plain text."""
        return re.sub(r"<[^>]+>", "\n", html)

    def _extract_year_from_path(self, file_path: Path) -> str:
        """Extract year from filename (e.g., '华夏信用卡-电子账单2025年11月.eml')."""
        match = re.search(r"(\d{4})年", file_path.name)
        if match:
            return match.group(1)
        return str(date.today().year)

    def _extract_statement_period(
        self, html: str, file_path: Path
    ) -> tuple[date, date] | None:
        """Extract statement period from HTML content or filename.

        Tries multiple patterns:
        1. HTML content: 2025/11/01-2025/11/30, 2025年11月01日-2025年11月30日
        2. Filename: 2025年11月 -> assumes full month coverage
        """
        # Try to find period in HTML content
        # Pattern: YYYY/MM/DD-YYYY/MM/DD
        match = re.search(r"(\d{4})/(\d{2})/(\d{2})-(\d{4})/(\d{2})/(\d{2})", html)
        if match:
            start = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            end = date(int(match.group(4)), int(match.group(5)), int(match.group(6)))
            return (start, end)

        # Pattern: YYYY年MM月DD日-YYYY年MM月DD日
        match = re.search(
            r"(\d{4})年(\d{1,2})月(\d{1,2})日.*?(\d{4})年(\d{1,2})月(\d{1,2})日", html
        )
        if match:
            start = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            end = date(int(match.group(4)), int(match.group(5)), int(match.group(6)))
            return (start, end)

        # Fallback: extract year and month from filename and assume full month
        match = re.search(r"(\d{4})年(\d{1,2})月", file_path.name)
        if match:
            year = int(match.group(1))
            month = int(match.group(2))
            # Assume statement covers the full month
            start = date(year, month, 1)
            # Last day of month
            if month == 12:
                end = date(year + 1, 1, 1) - timedelta(days=1)
            else:
                end = date(year, month + 1, 1) - timedelta(days=1)
            return (start, end)

        return None

    def _parse_transactions(
        self,
        text: str,
        year: str,
        file_path: Path,
        statement_period: tuple[date, date] | None = None,
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
                txn = self._parse_single_transaction(
                    lines, i, year, file_path, statement_period
                )
                if txn:
                    transactions.append(txn[0])
                    i = txn[1]
                else:
                    i += 1
            else:
                i += 1

        return transactions

    def _parse_single_transaction(
        self,
        lines: list[str],
        start_idx: int,
        year: str,
        file_path: Path,
        statement_period: tuple[date, date] | None = None,
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
            statement_period=statement_period,
            metadata={
                "original_date": date1,
            },
        )

        return (txn, i)
