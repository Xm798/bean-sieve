"""HSBC Hong Kong credit card statement provider."""

from __future__ import annotations

import csv
import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ....core.types import Transaction
from ... import register_provider
from ...base import BaseProvider

logger = logging.getLogger(__name__)

# HSBC HK exports the credit-card history with a fixed default filename
# (TransactionHistory.csv). The CSV itself does not contain the card number,
# so we extract it from the filename when the user has appended a "_<last4>"
# suffix, e.g. "TransactionHistory_8888.csv" or "TransactionHistory_8888 (1).csv".
CARD_LAST4_PATTERN = re.compile(r"_(\d{4})(?:\s*\(\d+\))?\.csv$", re.IGNORECASE)
DATE_PATTERN = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


@register_provider
class HSBCHKCreditProvider(BaseProvider):
    """
    Provider for HSBC Hong Kong credit card CSV statements.

    File format:
    - 10 columns: Transaction date, Post date, Description, Billing amount,
      Billing currency, Transaction status, Merchant name, Country / region,
      Area / district, Credit / Debit
    - Date format: DD/MM/YYYY
    - Amount sign in source: DEBIT rows are negative (expense), CREDIT rows
      are positive (refund / payment). Sieve convention is the opposite
      (expense=positive, income=negative), so amounts are negated.
    - Card number is not present in the CSV; rename downloaded files to
      TransactionHistory_<last4>.csv to disambiguate when multiple cards exist.
    """

    provider_id = "hsbchk_credit"
    provider_name = "汇丰香港信用卡"
    supported_formats = [".csv"]
    # Use content_keywords (not filename_pattern) — credit and debit exports
    # share the same default filename, so we differentiate by header columns.
    content_keywords = [
        "Transaction date,Post date,Description,Billing amount",
    ]
    # One CSV download covers exactly one card.
    per_card_statement = True

    REQUIRED_COLUMNS = (
        "Transaction date",
        "Post date",
        "Description",
        "Billing amount",
        "Billing currency",
        "Merchant name",
        "Credit / Debit",
    )

    def parse(self, file_path: Path) -> list[Transaction]:
        rows = self._read_rows(file_path)
        if not rows:
            return []

        col_map = self._find_columns(rows[0])
        if col_map is None:
            raise ValueError(f"Cannot identify HSBC HK credit header in {file_path}")

        card_last4 = self._extract_card_last4(file_path)

        transactions: list[Transaction] = []
        for row_num, row in enumerate(rows[1:], start=2):
            txn = self._parse_row(row, row_num, col_map, card_last4, file_path)
            if txn:
                transactions.append(txn)

        if transactions:
            dates = [t.date for t in transactions]
            statement_period = (min(dates), max(dates))
            for t in transactions:
                t.statement_period = statement_period

        return transactions

    @staticmethod
    def _read_rows(file_path: Path) -> list[list[str]]:
        """Read CSV rows, stripping per-cell whitespace (handles trailing tabs)."""
        for encoding in ["utf-8-sig", "utf-8", "gbk", "big5"]:
            try:
                with open(file_path, encoding=encoding, newline="") as f:
                    return [
                        [c.strip() for c in row]
                        for row in csv.reader(f)
                        if any(c.strip() for c in row)
                    ]
            except (UnicodeDecodeError, UnicodeError):
                continue
        raise ValueError(f"Cannot decode {file_path}")

    @classmethod
    def _find_columns(cls, header_row: list[str]) -> dict[str, int] | None:
        """Map every header cell to its index, after verifying required columns exist."""
        normalized = [c.strip().lower() for c in header_row]
        if not all(col.lower() in normalized for col in cls.REQUIRED_COLUMNS):
            return None
        return {original.strip(): idx for idx, original in enumerate(header_row)}

    @staticmethod
    def _extract_card_last4(file_path: Path) -> str | None:
        match = CARD_LAST4_PATTERN.search(file_path.name)
        return match.group(1) if match else None

    def _parse_row(
        self,
        row: list[str],
        row_num: int,
        col_map: dict[str, int],
        card_last4: str | None,
        file_path: Path,
    ) -> Transaction | None:
        def cell(key: str) -> str:
            idx = col_map.get(key)
            if idx is None or idx >= len(row):
                return ""
            return row[idx].strip()

        trans_date_str = cell("Transaction date")
        billing_amount = cell("Billing amount")
        if not trans_date_str or not billing_amount:
            return None

        trans_date = self._parse_date(trans_date_str)
        if trans_date is None:
            return None
        post_date = self._parse_date(cell("Post date"))

        amount = self._parse_amount(billing_amount)
        if amount is None or amount == 0:
            return None
        amount = -amount  # source: -expense / +credit → sieve: +expense / -income

        currency = cell("Billing currency") or "HKD"
        description = self.clean_text(cell("Description"))
        merchant = cell("Merchant name") or None

        metadata: dict[str, str] = {}
        status = cell("Transaction status")
        if status:
            metadata["transaction_status"] = status
        country = cell("Country / region")
        if country:
            metadata["country"] = country
        district = cell("Area / district")
        if district:
            metadata["district"] = district
        direction = cell("Credit / Debit")
        if direction:
            metadata["direction"] = direction

        return Transaction(
            date=trans_date,
            post_date=post_date,
            amount=amount,
            currency=currency,
            description=description or "Unknown",
            payee=merchant,
            card_last4=card_last4,
            provider=self.provider_id,
            source_file=file_path,
            source_line=row_num,
            metadata=metadata,
        )

    @staticmethod
    def _parse_date(date_str: str) -> date | None:
        if not date_str:
            return None
        match = DATE_PATTERN.match(date_str.strip())
        if not match:
            return None
        try:
            return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        except ValueError:
            return None

    @staticmethod
    def _parse_amount(value: str) -> Decimal | None:
        cleaned = value.replace(",", "").replace("\t", "").strip()
        if not cleaned:
            return None
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return None
