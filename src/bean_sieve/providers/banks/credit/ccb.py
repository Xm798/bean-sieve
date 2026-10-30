"""China Construction Bank (建设银行) credit card statement provider."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from ....core.coverage import Coverage
from ....core.metadata_keys import ORIGINAL_AMOUNT, ORIGINAL_CURRENCY
from ....core.types import Transaction
from ... import register_provider
from ..._pdf import pdf_lines
from ...base import BaseProvider

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# A card cell can add a second suffix or a channel to the card's own, as
# "1234/5678" or "1234/扫码"; the leading four digits name the card.
_CARD_RE = re.compile(r"^(\d{4})(?:/\S+)?$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_CYCLE = r"(\d{4})/(\d{2})/(\d{2})-(\d{4})/(\d{2})/(\d{2})"
# The mail prints "Statement Cycle" between the label and the dates.
_MAIL_CYCLE_RE = re.compile(rf"账单周期(?:\s*Statement Cycle)?\s*{_CYCLE}")
# The reissue prints the cycle as a cell of its own, under a header row.
_PDF_CYCLE_RE = re.compile(rf"^{_CYCLE}$")


@register_provider
class CCBCreditProvider(BaseProvider):
    """
    Provider for China Construction Bank (建设银行) credit card statements.

    Two formats carry the same detail table:
    - .eml: the mailed statement, an HTML table opened by "【交易明细】"
    - .pdf: the reissued statement (龙卡信用卡对账单) from the app, a text PDF

    Row columns: 交易日, 银行记账日, 卡号后四位, 交易描述, 交易币/金额,
    结算币/金额, each amount as a currency cell and a number cell. Negative
    amounts are payments and refunds. The cycle is printed as
    "2030/03/01-2030/03/31" under 账单周期.
    """

    provider_id = "ccb_credit"
    provider_name = "建设银行信用卡"
    supported_formats = [".eml", ".pdf"]
    filename_keywords = ["建设银行", "中国建设银行"]
    content_keywords = ["中国建设银行信用卡", "龙卡信用卡对账单"]
    per_card_statement = True  # CCB sends separate statements per card
    coverage_priority = ("statement", "reissue")

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse a CCB credit card statement, mailed or reissued."""
        if file_path.suffix.lower() == ".pdf":
            cycle, rows = self._read_pdf(file_path)
        else:
            cycle, rows = self._read_eml(file_path)

        transactions = [
            txn
            for row_idx, cells in enumerate(rows, start=1)
            if (txn := self._parse_row(cells, file_path, row_idx)) is not None
        ]
        self.stamp_cycle(transactions, cycle)
        return transactions

    def coverage(
        self, file_path: Path, transactions: list[Transaction]
    ) -> list[Coverage]:
        """Cover each card for the printed cycle; the mail outranks its reissue."""
        source = "reissue" if file_path.suffix.lower() == ".pdf" else "statement"
        return self.cycle_coverages(transactions, source=source)

    def _read_eml(
        self, file_path: Path
    ) -> tuple[tuple[date, date] | None, list[list[str]]]:
        soup = self.parse_html(self.extract_html_from_eml(file_path))
        rows: list[list[str]] = []
        for table in soup.find_all("table"):
            first_row = table.find("tr")
            if not first_row or "【交易明细】" not in first_row.get_text(strip=True):
                continue
            for row in table.find_all("tr"):
                cells = [c.get_text(strip=True) for c in row.find_all("td")]
                if len(cells) >= 6 and _DATE_RE.match(cells[0]):
                    rows.append(cells)
        return _cycle(_MAIL_CYCLE_RE.search(soup.get_text())), rows

    def _read_pdf(
        self, file_path: Path
    ) -> tuple[tuple[date, date] | None, list[list[str]]]:
        """Read the reissue's cycle and its detail rows as the mail's eight cells.

        A row's cells share a line: two dates, the card suffix, the
        description (absent when blank) and two currency and amount pairs.
        """
        lines = pdf_lines(file_path)
        cycle = next(
            (m for line in lines for cell in line if (m := _PDF_CYCLE_RE.match(cell))),
            None,
        )
        return _cycle(cycle), [cells for line in lines if (cells := _pdf_row(line))]

    def _parse_row(
        self,
        cells: list[str],
        file_path: Path,
        row_idx: int,
    ) -> Transaction | None:
        """Parse a single transaction row.

        Row format: [交易日, 银行记账日, 卡号后四位, 交易描述, CNY, 金额, CNY, 金额]
        or: [交易日, 银行记账日, 卡号后四位, 交易描述, 交易币/金额, 结算币/金额]
        """
        try:
            trans_date_str = cells[0]  # YYYY-MM-DD
            post_date_str = cells[1]  # YYYY-MM-DD
            card_cell = cells[2]
            card_match = _CARD_RE.match(card_cell)
            card_last4 = card_match[1] if card_match else card_cell
            description = cells[3]

            trans_date = self._parse_date(trans_date_str)
            post_date = self._parse_date(post_date_str)

            pairs = self._amount_pairs(cells[4:])
            if not pairs:
                return None
            # The settlement amount is what the card is billed.
            currency, amount = pairs[-1]
            if amount is None:
                return None
            original_currency, original_amount = pairs[0]
            metadata: dict[str, Any] = {}
            if card_cell != card_last4:
                metadata["card_cell"] = card_cell
            if original_currency != currency and original_amount is not None:
                metadata[ORIGINAL_AMOUNT] = original_amount
                metadata[ORIGINAL_CURRENCY] = original_currency

            return Transaction(
                date=trans_date,
                post_date=post_date,
                amount=amount,
                currency=currency,
                description=description,
                card_last4=card_last4,
                provider=self.provider_id,
                source_file=file_path,
                source_line=row_idx,
                metadata=metadata,
            )
        except (IndexError, ValueError):
            return None

    def _parse_date(self, date_str: str) -> date:
        """Parse date string in YYYY-MM-DD format."""
        year, month, day = map(int, date_str.split("-"))
        return date(year, month, day)

    def _amount_pairs(self, cells: list[str]) -> list[tuple[str, Decimal | None]]:
        """Read the transaction and settlement (currency, amount) pairs.

        Handles two formats:
        1. [CNY, 金额, CNY, 金额] - currency and amount in separate cells
        2. [CNY金额, CNY金额] - combined currency+amount
        """
        if cells and _CURRENCY_RE.match(cells[0]):
            printed = list(zip(cells[0::2], cells[1::2], strict=False))[:2]
            return [(code, self._parse_amount(amount)) for code, amount in printed]
        pairs: list[tuple[str, Decimal | None]] = []
        for cell in cells[:2]:
            match = re.match(r"([A-Z]{3})([\d,.-]+)", cell)
            if match:
                pairs.append((match.group(1), self._parse_amount(match.group(2))))
        return pairs

    def _parse_amount(self, amount_str: str) -> Decimal | None:
        """Parse amount string, handling commas and negative signs."""
        try:
            # Remove commas and whitespace
            cleaned = amount_str.replace(",", "").strip()
            return Decimal(cleaned)
        except Exception:
            return None


def _cycle(match: re.Match[str] | None) -> tuple[date, date] | None:
    if match is None:
        return None
    y1, m1, d1, y2, m2, d2 = map(int, match.groups())
    return date(y1, m1, d1), date(y2, m2, d2)


def _pdf_row(cells: list[str]) -> list[str] | None:
    if len(cells) < 7 or not (
        _DATE_RE.match(cells[0])
        and _DATE_RE.match(cells[1])
        and _CARD_RE.match(cells[2])
        and _CURRENCY_RE.match(cells[-4])
        and _CURRENCY_RE.match(cells[-2])
    ):
        return None
    return [*cells[:3], " ".join(cells[3:-4]), *cells[-4:]]
