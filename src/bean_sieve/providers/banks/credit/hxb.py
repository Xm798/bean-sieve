"""Huaxia Bank (华夏银行) credit card statement provider."""

from __future__ import annotations

import calendar
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
        statement_period = self._extract_statement_period(html, text, file_path)
        fallback_year = self._extract_year_from_path(file_path)

        transactions = self._parse_transactions(
            text, statement_period, fallback_year, file_path
        )
        self.assign_statement_periods(transactions, statement_period)
        return transactions

    def _html_to_text(self, html: str) -> str:
        """Strip HTML tags and return plain text."""
        return re.sub(r"<[^>]+>", "\n", html)

    def _extract_year_from_path(self, file_path: Path) -> int:
        """Extract year from filename (e.g., '华夏信用卡-电子账单2025年11月.eml')."""
        match = re.search(r"(\d{4})年", file_path.name)
        if match:
            return int(match.group(1))
        return date.today().year

    def _extract_statement_period(
        self, html: str, text: str, file_path: Path
    ) -> tuple[date, date] | None:
        """Extract statement period from HTML content or filename.

        Tries multiple patterns:
        1. HTML content: 2025/11/01-2025/11/30, 2025年11月01日-2025年11月30日
        2. HTML billing date (账单日 每月XX日) + statement month
        3. Statement month alone -> assumes full month coverage
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

        statement_month = self._extract_statement_month(text, file_path)
        if statement_month is None:
            return None
        year, month = statement_month

        # e.g., "账单日 每月26日" on the 2026/02 statement → Jan 27 - Feb 26
        period = self._derive_period_from_billing_date(text, year, month)
        if period:
            return period

        return (
            date(year, month, 1),
            date(year, month, calendar.monthrange(year, month)[1]),
        )

    def _extract_statement_month(
        self, text: str, file_path: Path
    ) -> tuple[int, int] | None:
        """Read the statement month from the 对账单(YYYY/MM) title or the filename."""
        match = re.search(r"对账单\s*[(（]\s*(\d{4})/(\d{1,2})\s*[)）]", text)
        if match is None:
            match = re.search(r"(\d{4})年(\d{1,2})月", file_path.name)
        if match is None:
            return None
        return int(match.group(1)), int(match.group(2))

    def _derive_period_from_billing_date(
        self, text: str, year: int, month: int
    ) -> tuple[date, date] | None:
        """Derive billing period from billing date (账单日) and statement month.

        Credit card billing period runs from previous billing date + 1
        to current billing date. e.g., billing date 26th for Feb statement
        means the period is Jan 27 - Feb 26.
        """
        match = re.search(r"账单日\s*每月\s*(\d{1,2})\s*日", text)
        if not match:
            return None

        billing_day = int(match.group(1))

        # End date: billing day of statement month (clamp to month's last day)
        max_day = calendar.monthrange(year, month)[1]
        end = date(year, month, min(billing_day, max_day))

        # Start date: previous billing day + 1
        if month == 1:
            prev_year, prev_month = year - 1, 12
        else:
            prev_year, prev_month = year, month - 1
        prev_max_day = calendar.monthrange(prev_year, prev_month)[1]
        prev_billing = date(prev_year, prev_month, min(billing_day, prev_max_day))
        start = prev_billing + timedelta(days=1)

        return (start, end)

    def _parse_date_with_period(
        self,
        date_str: str,
        statement_period: tuple[date, date] | None,
        fallback_year: int,
    ) -> date:
        """Resolve a MM/DD row to a full date using the statement period.

        Rows carry no year, and the statement closes on its period end, so a row
        is the latest MM/DD falling on or before that day: a December row on a
        January statement is the previous December, and a November row on that
        same statement a settlement delayed from the November before it.
        """
        month, day = map(int, date_str.split("/"))

        if not statement_period:
            return date(fallback_year, month, day)

        end = statement_period[1]
        year = end.year if (month, day) <= (end.month, end.day) else end.year - 1
        return date(year, month, day)

    def _parse_transactions(
        self,
        text: str,
        statement_period: tuple[date, date] | None,
        fallback_year: int,
        file_path: Path,
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
            # The USD section repeats the CNY layout: a balance summary, then
            # its own 交易日 header and rows with ＄ amounts.
            if lines[i] == "美元账务信息":
                in_trans = False
                i += 1
                continue

            if in_trans and re.match(r"^\d{2}/\d{2}$", lines[i]):
                txn = self._parse_single_transaction(
                    lines, i, statement_period, fallback_year, file_path
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
        statement_period: tuple[date, date] | None,
        fallback_year: int,
        file_path: Path,
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

        trans_date = self._parse_date_with_period(
            date1, statement_period, fallback_year
        )

        # Determine currency (CNY by default, USD if $ symbol)
        currency = "CNY"
        if "＄" in lines[i - 1]:
            currency = "USD"

        txn = Transaction(
            date=trans_date,
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
