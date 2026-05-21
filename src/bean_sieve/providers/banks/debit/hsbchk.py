"""HSBC Hong Kong debit / savings account statement provider."""

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

DATE_PATTERN = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
# Leading reference codes used by HSBC HK in the description column, e.g.
#   "HC11111111111111   07MAY"      → HC11111111111111
#   "NET- 2222222222222222"         → NET- 2222222222222222
ORDER_ID_PATTERN = re.compile(r"^([A-Z]{2,}-?\s*\d{8,})")


@register_provider
class HSBCHKDebitProvider(BaseProvider):
    """
    Provider for HSBC Hong Kong debit / savings account CSV statements.

    File format:
    - 6 columns: Date, Description, Billing amount, Billing currency,
      Balance, Balance currency
    - Date format: DD/MM/YYYY
    - Amount sign in source: positive = inflow (credit / deposit),
      negative = outflow (debit / withdrawal). Sieve convention is
      expense=positive, income=negative, so amounts are negated.
    """

    provider_id = "hsbchk_debit"
    provider_name = "汇丰香港储蓄账户"
    supported_formats = [".csv"]
    # Distinguishes from hsbchk_credit by the presence of the Balance column.
    content_keywords = ["Date,Description,Billing amount,Billing currency,Balance"]

    REQUIRED_COLUMNS = (
        "Date",
        "Description",
        "Billing amount",
        "Billing currency",
        "Balance",
        "Balance currency",
    )

    def parse(self, file_path: Path) -> list[Transaction]:
        rows = self._read_rows(file_path)
        if not rows:
            return []

        col_map = self._find_columns(rows[0])
        if col_map is None:
            raise ValueError(f"Cannot identify HSBC HK debit header in {file_path}")

        transactions: list[Transaction] = []
        for row_num, row in enumerate(rows[1:], start=2):
            txn = self._parse_row(row, row_num, col_map, file_path)
            if txn:
                transactions.append(txn)
        return transactions

    @staticmethod
    def _read_rows(file_path: Path) -> list[list[str]]:
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

    def _parse_row(
        self,
        row: list[str],
        row_num: int,
        col_map: dict[str, int],
        file_path: Path,
    ) -> Transaction | None:
        def cell(key: str) -> str:
            idx = col_map.get(key)
            if idx is None or idx >= len(row):
                return ""
            return row[idx].strip()

        date_str = cell("Date")
        billing_amount = cell("Billing amount")
        if not date_str or not billing_amount:
            return None

        tx_date = self._parse_date(date_str)
        if tx_date is None:
            return None

        amount = self._parse_amount(billing_amount)
        if amount is None or amount == 0:
            return None
        amount = -amount  # source: +inflow / -outflow → sieve: +expense / -income

        currency = cell("Billing currency") or "HKD"
        description = self.clean_text(cell("Description"))
        order_id = self._extract_order_id(description)

        metadata: dict[str, str] = {}
        balance = cell("Balance")
        if balance:
            metadata["balance"] = balance
        balance_currency = cell("Balance currency")
        if balance_currency and balance_currency != currency:
            metadata["balance_currency"] = balance_currency

        return Transaction(
            date=tx_date,
            amount=amount,
            currency=currency,
            description=description or "Unknown",
            order_id=order_id,
            provider=self.provider_id,
            source_file=file_path,
            source_line=row_num,
            metadata=metadata,
        )

    @staticmethod
    def _parse_date(date_str: str) -> date | None:
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

    @staticmethod
    def _extract_order_id(description: str) -> str | None:
        match = ORDER_ID_PATTERN.match(description)
        return match.group(1).strip() if match else None
