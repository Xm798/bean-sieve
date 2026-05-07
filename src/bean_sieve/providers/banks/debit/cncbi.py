"""China CITIC Bank International (中信银行国际) debit account statement provider."""

from __future__ import annotations

import csv
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ....core.types import Transaction
from ... import register_provider
from ...base import BaseProvider

# Header variants accepted: Simplified Chinese, Traditional Chinese, English.
# Map normalized cell text -> logical column purpose.
HEADER_TOKENS: dict[str, set[str]] = {
    "post_date": {"过账日", "過賬日", "post date"},
    "trans_date": {"交易日", "trans.date", "trans date"},
    "description": {"账项资料", "賬項資料", "description"},
    "debit": {"支出", "debit"},
    "credit": {"收入", "credit"},
    "balance": {"结余", "結餘", "balance"},
}

# Balance carry-forward markers — these rows are not real transactions.
# All entries MUST be lowercase; compared against description.lower().
BALANCE_MARKERS: tuple[str, ...] = (
    "承上结余",
    "承上結餘",
    "转承结余",
    "轉承結餘",
    "balance brought forward",
    "balance carried forward",
    "b/f balance",
    "c/f balance",
)

# FPS / 转数快 reference codes appear as: FICT/FOCT/FICB/FOCB + 16 digits
ORDER_ID_PATTERN = re.compile(r"\b(F[IO][A-Z]{2}\d{16,})\b")
DATE_PATTERN = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


@register_provider
class CNCBIDebitProvider(BaseProvider):
    """
    Provider for CITIC Bank International (中信银行国际) Hong Kong debit
    account statements, manually exported from online banking as CSV.

    Source: https://ibanking.cncbinternational.com/

    File format:
    - CSV with one of three header variants (Simplified / Traditional Chinese / English):
        过账日,交易日,账项资料,支出,收入,结余
        過賬日,交易日,賬項資料,支出,收入,結餘
        Post Date,Trans.Date,Description,Debit,Credit,Balance
    - Date format: DD/MM/YYYY
    - Amount: Debit (支出) is expense (positive), Credit (收入) is income (negative)
    - "承上结余" / "转承结余" rows are balance carry-forwards and are skipped
    - Default currency: HKD
    """

    provider_id = "cncbi_debit"
    provider_name = "中信银行（国际）"
    supported_formats = [".csv"]
    filename_keywords = ["CNCBI", "cncbi"]
    content_keywords = [
        "过账日,交易日,账项资料",
        "過賬日,交易日,賬項資料",
        "Post Date,Trans.Date,Description",
    ]

    def parse(self, file_path: Path) -> list[Transaction]:
        rows = self._read_rows(file_path)
        if not rows:
            return []

        col_map = self._find_columns(rows[0])
        if col_map is None:
            raise ValueError(f"Cannot identify CNCBI header in {file_path}")

        transactions: list[Transaction] = []
        for row_num, row in enumerate(rows[1:], start=2):
            txn = self._parse_row(row, row_num, col_map, file_path)
            if txn:
                transactions.append(txn)
        return transactions

    @staticmethod
    def _read_rows(file_path: Path) -> list[list[str]]:
        """Read CSV rows, trying common encodings."""
        for encoding in ["utf-8-sig", "utf-8", "gbk", "big5"]:
            try:
                with open(file_path, encoding=encoding, newline="") as f:
                    return [row for row in csv.reader(f) if any(c.strip() for c in row)]
            except (UnicodeDecodeError, UnicodeError):
                continue
        raise ValueError(f"Cannot decode {file_path}")

    @staticmethod
    def _find_columns(header_row: list[str]) -> dict[str, int] | None:
        """Identify column indices from the header row."""
        normalized = [c.strip().lower() for c in header_row]
        col_map: dict[str, int] = {}
        for purpose, tokens in HEADER_TOKENS.items():
            for idx, cell in enumerate(normalized):
                if cell in tokens:
                    col_map[purpose] = idx
                    break
        required = {"trans_date", "description", "debit", "credit"}
        if not required.issubset(col_map):
            return None
        return col_map

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

        date_str = cell("trans_date")
        description = cell("description")
        if not date_str or not description:
            return None

        desc_lower = description.lower()
        if any(marker in desc_lower for marker in BALANCE_MARKERS):
            return None

        tx_date = self._parse_date(date_str)
        if tx_date is None:
            return None

        amount = self._parse_amount(cell("debit"), cell("credit"))
        if amount is None:
            return None

        post_date_str = cell("post_date")
        post_date = self._parse_date(post_date_str) if post_date_str else None

        order_id = self._extract_order_id(description)
        payee = self._extract_payee(description, order_id)

        metadata: dict[str, str] = {}
        balance = cell("balance")
        if balance:
            metadata["balance"] = balance

        return Transaction(
            date=tx_date,
            post_date=post_date,
            amount=amount,
            currency="HKD",
            description=description,
            payee=payee,
            order_id=order_id,
            provider=self.provider_id,
            source_file=file_path,
            source_line=row_num,
            metadata=metadata,
        )

    @staticmethod
    def _parse_date(date_str: str) -> date | None:
        """Parse DD/MM/YYYY format."""
        match = DATE_PATTERN.match(date_str)
        if not match:
            return None
        try:
            return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        except ValueError:
            return None

    @staticmethod
    def _parse_amount(debit_str: str, credit_str: str) -> Decimal | None:
        """Debit -> positive expense, Credit -> negative income."""

        def to_dec(s: str) -> Decimal | None:
            cleaned = s.replace(",", "").strip()
            if not cleaned:
                return None
            try:
                d = Decimal(cleaned)
                return d if d != 0 else None
            except InvalidOperation:
                return None

        debit = to_dec(debit_str)
        if debit is not None:
            return debit
        credit = to_dec(credit_str)
        if credit is not None:
            return -credit
        return None

    @staticmethod
    def _extract_order_id(description: str) -> str | None:
        match = ORDER_ID_PATTERN.search(description)
        return match.group(1) if match else None

    @staticmethod
    def _extract_payee(description: str, order_id: str | None) -> str | None:
        """For FPS entries, the text after the reference code is the counterparty."""
        if not order_id:
            return None
        tail = description.split(order_id, 1)[-1].strip()
        tail = tail.lstrip("-").strip()
        return tail or None
