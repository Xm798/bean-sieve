"""Postal Savings Bank of China credit-card statement provider."""

from __future__ import annotations

import email
import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from ....core.types import Transaction
from ... import register_provider
from ...base import BaseProvider

logger = logging.getLogger(__name__)

EXPECTED_HEADERS = (
    "交易日",
    "记账日",
    "交易摘要",
    "人民币金额",
    "卡号末四位",
    "交易国别",
    "境内外交易标识",
)
_DATE_RE = re.compile(r"^\d{8}$")
_AMOUNT_RE = re.compile(r"^￥[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}$")
_PERIOD_RE = re.compile(
    r"账单周期为【"
    r"(\d{4})/(\d{2})/(\d{2})"
    r"-"
    r"(\d{4})/(\d{2})/(\d{2})"
    r"】"
)


@register_provider
class PSBCCreditProvider(BaseProvider):
    """Parse PSBC credit-card email statements."""

    provider_id = "psbc_credit"
    provider_name = "邮储银行信用卡"
    supported_formats = [".eml"]
    filename_keywords = ["邮储银行信用卡电子账单"]
    content_keywords = [
        "邮储银行信用卡电子账单",
        "中国邮政储蓄银行信用卡对账单",
    ]
    per_card_statement = True  # PSBC sends separate statements per card

    @classmethod
    def _match_content(cls, file_path: Path) -> bool:
        """Detect PSBC markers in a decoded EML subject or HTML body."""
        try:
            with open(file_path, "rb") as file:
                message = email.message_from_binary_file(file)
            provider = cls()
            subject = provider.decode_subject(message)
            html = provider._extract_html_from_message(message)
            title = provider.parse_html(html).find("title")
            return cls.content_keywords[0] in subject or (
                title is not None
                and cls.content_keywords[1] in title.get_text(" ", strip=True)
            )
        except Exception:
            return False

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse a PSBC credit-card email statement."""
        html = self.extract_html_from_eml(file_path)
        soup = self.parse_html(html)
        table_match = self._find_transaction_table(soup)
        if table_match is None:
            return []

        table, column_map, header_row_number = table_match
        transactions: list[Transaction] = []
        for row_number, row in enumerate(table.find_all("tr"), start=1):
            if row_number <= header_row_number:
                continue

            cell_texts = [
                self.clean_text(cell.get_text(" ", strip=True))
                for cell in row.find_all(["th", "td"], recursive=False)
            ]
            if not any(cell_texts):
                continue

            transaction = self._parse_transaction_row(
                cell_texts,
                column_map,
                file_path,
                row_number,
            )
            if transaction is None:
                logger.warning(
                    "Skipping malformed %s row %d",
                    self.provider_id,
                    row_number,
                )
                continue
            transactions.append(transaction)

        if not transactions:
            return []

        statement_period = self._extract_statement_period(
            soup.get_text(" ", strip=True)
        )
        transaction_dates = [transaction.date for transaction in transactions]
        if statement_period is None or not all(
            statement_period[0] <= transaction_date <= statement_period[1]
            for transaction_date in transaction_dates
        ):
            statement_period = (min(transaction_dates), max(transaction_dates))

        for transaction in transactions:
            transaction.statement_period = statement_period
        return transactions

    def _find_transaction_table(
        self,
        soup: BeautifulSoup,
    ) -> tuple[Tag, dict[str, int], int] | None:
        """Find the leaf table containing one complete transaction header."""
        expected = set(EXPECTED_HEADERS)
        for table in soup.find_all("table"):
            if table.find("table") is not None:
                continue

            for row_number, row in enumerate(table.find_all("tr"), start=1):
                headers = [
                    self.clean_text(cell.get_text(" ", strip=True))
                    for cell in row.find_all(["th", "td"], recursive=False)
                ]
                if set(headers) != expected or len(headers) != len(expected):
                    continue
                return (
                    table,
                    {header: index for index, header in enumerate(headers)},
                    row_number,
                )
        return None

    def _parse_transaction_row(
        self,
        cells: list[str],
        column_map: dict[str, int],
        file_path: Path,
        row_number: int,
    ) -> Transaction | None:
        """Parse one transaction row without exposing malformed content."""
        try:
            transaction_date = self._parse_date(cells[column_map["交易日"]])
            post_date = self._parse_date(cells[column_map["记账日"]])
            description = cells[column_map["交易摘要"]].strip()
            amount = self._parse_amount(cells[column_map["人民币金额"]])
            card_last4 = cells[column_map["卡号末四位"]].strip()
            country = cells[column_map["交易国别"]].strip()
            domestic_or_overseas = cells[column_map["境内外交易标识"]].strip()
        except (IndexError, KeyError):
            return None

        if (
            transaction_date is None
            or post_date is None
            or amount is None
            or not description
            or re.fullmatch(r"\d{4}", card_last4) is None
        ):
            return None

        metadata: dict[str, str] = {}
        if country:
            metadata["country"] = country
        if domestic_or_overseas:
            metadata["domestic_or_overseas"] = domestic_or_overseas

        return Transaction(
            date=transaction_date,
            post_date=post_date,
            amount=amount,
            currency="CNY",
            description=description,
            card_last4=card_last4,
            provider=self.provider_id,
            source_file=file_path,
            source_line=row_number,
            metadata=metadata,
        )

    @staticmethod
    def _parse_date(value: str) -> date | None:
        """Parse a strict YYYYMMDD date."""
        if _DATE_RE.fullmatch(value) is None:
            return None
        try:
            return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
        except ValueError:
            return None

    @staticmethod
    def _parse_amount(value: str) -> Decimal | None:
        """Parse a signed CNY amount while preserving the source sign."""
        if _AMOUNT_RE.fullmatch(value) is None:
            return None
        try:
            amount = Decimal(value.removeprefix("￥").replace(",", ""))
        except InvalidOperation:
            return None
        return amount if amount.is_finite() else None

    @staticmethod
    def _extract_statement_period(text: str) -> tuple[date, date] | None:
        """Extract and validate the explicit statement period."""
        match = _PERIOD_RE.search(text)
        if match is None:
            return None
        try:
            start = date(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
            )
            end = date(
                int(match.group(4)),
                int(match.group(5)),
                int(match.group(6)),
            )
        except ValueError:
            return None
        return (start, end) if start <= end else None
