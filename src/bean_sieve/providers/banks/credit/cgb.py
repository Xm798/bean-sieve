"""CGB credit card statement provider."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from ....core.coverage import Coverage
from ....core.metadata_keys import ORIGINAL_AMOUNT, ORIGINAL_CURRENCY, STATEMENT_CYCLE
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

# Spans of one PDF line have tops within this many points of each other.
_PDF_LINE_TOLERANCE = 2.0


@register_provider
class CGBCreditProvider(BaseProvider):
    """
    Provider for CGB credit card statements.

    Two formats print the same text:
    - .eml: the mailed statement, base64-encoded GBK HTML
    - .pdf: the reissued statement, a text PDF whose issuer name is only in
      its logo image

    Layout:
    - Statement period: 账单周期:YYYY/MM/DD-YYYY/MM/DD (the PDF prints
      "账单周期 YYYY/MM/DD - YYYY/MM/DD")
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
    supported_formats = [".eml", ".pdf"]
    filename_keywords = [f"{_ISSUER_SHORT}信用卡", _ISSUER]
    # 发现精彩 is the issuer's app, which the PDF's notes name.
    content_keywords = [f"{_ISSUER}信用卡", f"{_ISSUER_SHORT}信用卡", "发现精彩"]
    per_card_statement = (
        True  # CGB sends combined statement but needs per-card tracking
    )
    coverage_priority = ("statement", "reissue")

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse a CGB credit card statement, mailed or reissued."""
        if file_path.suffix.lower() == ".pdf":
            # Joining a line's cells with spaces gives the mail's text:
            # "date date (type)summary amount currency ...".
            text = "\n".join(" ".join(line) for line in _pdf_lines(file_path))
        else:
            html = self.extract_html_from_eml(file_path)
            text = self.parse_html(html).get_text()

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
        if statement_period:
            for txn in transactions:
                txn.metadata[STATEMENT_CYCLE] = statement_period
        return transactions

    def coverage(
        self, file_path: Path, transactions: list[Transaction]
    ) -> list[Coverage]:
        """Cover each card for the printed cycle; the mail outranks its reissue.

        statement_period is widened by rows dated outside the cycle, so
        coverage reads the cycle as printed.
        """
        source = "reissue" if file_path.suffix.lower() == ".pdf" else "statement"
        by_card: dict[str, list[Transaction]] = {}
        for txn in transactions:
            if STATEMENT_CYCLE in txn.metadata:
                by_card.setdefault(txn.card_last4 or "", []).append(txn)
        return [
            Coverage(card, *rows[0].metadata[STATEMENT_CYCLE], rows=rows, source=source)
            for card, rows in by_card.items()
        ]

    def _extract_statement_period(self, text: str) -> tuple[date, date] | None:
        """Extract statement period (e.g., '账单周期:2025/12/26-2026/01/25')."""
        match = re.search(
            r"账单周期[:：]?\s*(\d{4})/(\d{2})/(\d{2})\s*-\s*(\d{4})/(\d{2})/(\d{2})",
            text,
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


def _pdf_lines(file_path: Path) -> list[list[str]]:
    """Return the PDF's printed lines, each as its cells read left to right.

    A line is the spans whose tops lie within _PDF_LINE_TOLERANCE of its
    first span's. PyMuPDF can join neighbouring cells into one span, so a
    span is cut into cells wherever its characters leave a gap wider than
    the font size.
    """
    import fitz

    lines: list[list[str]] = []
    with fitz.open(file_path) as doc:
        for page in doc:
            layout = cast(dict[str, Any], page.get_text("rawdict"))
            cells = sorted(
                cell
                for block in layout["blocks"]
                for line in block.get("lines", [])
                for span in line["spans"]
                for cell in _span_cells(span)
            )
            row: list[tuple[float, float, str]] = []
            for cell in cells:
                if row and cell[0] - row[0][0] > _PDF_LINE_TOLERANCE:
                    lines.append([text for _, _, text in sorted(row, key=_cell_x)])
                    row = []
                row.append(cell)
            if row:
                lines.append([text for _, _, text in sorted(row, key=_cell_x)])
    return lines


def _span_cells(span: dict[str, Any]) -> list[tuple[float, float, str]]:
    """Cut a span into (top, x, text) cells at gaps wider than its font size."""
    runs: list[list[dict[str, Any]]] = [[]]
    for char in span["chars"]:
        if runs[-1] and char["bbox"][0] - runs[-1][-1]["bbox"][2] > span["size"]:
            runs.append([])
        runs[-1].append(char)
    top = span["bbox"][1]
    return [
        (top, run[0]["bbox"][0], text)
        for run in runs
        if run and (text := "".join(char["c"] for char in run).strip())
    ]


def _cell_x(cell: tuple[float, float, str]) -> float:
    return cell[1]
