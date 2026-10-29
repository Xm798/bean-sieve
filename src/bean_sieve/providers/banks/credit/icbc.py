"""Industrial and Commercial Bank of China (工商银行) credit card provider."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

from ....core.coverage import Coverage
from ....core.metadata_keys import (
    ORIGINAL_AMOUNT,
    ORIGINAL_CURRENCY,
    STATEMENT_CYCLE,
)
from ....core.types import Transaction
from ... import register_provider
from ...base import BaseProvider

logger = logging.getLogger(__name__)

_EXPORT_TITLE = "信用卡历史明细"

_CARD_RE = re.compile(r"^\d{4}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")
_AMOUNT_RE = re.compile(r"^([\d,]+\.\d+)/([A-Z]{3})$")
_BOOKED_RE = re.compile(r"^([\d,]+\.\d+)/([A-Z]{3})\((存入|支出)\)$")
_CN_DATE = r"(\d{4})年(\d{1,2})月(\d{1,2})日"
_CYCLE_RE = re.compile(rf"账单周期\s*{_CN_DATE}\s*[—–-]+\s*{_CN_DATE}")
_EXPORT_RANGE_RE = re.compile(
    r"起止日期[:：]\s*(\d{4}-\d{2}-\d{2})\s*[—–-]+\s*(\d{4}-\d{2}-\d{2})"
)

# The export names currencies in Chinese; the statements print ISO codes.
_CURRENCY_NAMES = {
    "人民币": "CNY",
    "港币": "HKD",
    "美元": "USD",
    "欧元": "EUR",
    "英镑": "GBP",
    "日元": "JPY",
    "澳门元": "MOP",
    "新台币": "TWD",
    "新加坡元": "SGD",
    "澳大利亚元": "AUD",
    "加拿大元": "CAD",
    "瑞士法郎": "CHF",
    "韩元": "KRW",
}

# Header labels of the export table; "收支" is printed as two stacked
# characters, so its column is found by "收".
_EXPORT_COLUMNS = {
    "入账日期": "date",
    "交易卡号": "card",
    "收": "direction",
    "交易币种": "currency",
    "交易金额": "amount",
    "入账币种": "booked_currency",
    "入账金额": "booked_amount",
    "账户余额": "balance",
    "对方户名": "counterparty_name",
    "对方账号": "counterparty_account",
    "摘要": "summary",
    "交易场所": "place",
}

# The export draws its table rotated a quarter turn: text runs up the page,
# a row is a band of x and a column a band of y.
_ROTATED_DIR = (0.0, -1.0)
# Rows of the export sit 18pt apart, which bounds a page's last row when the
# page has no second row to measure.
_ROW_PITCH = 18.0
# A wrapped cell's first line starts up to this far before its row's date.
_X_SLACK = 1.0

# Table text of the reissue is 8pt; its watermark is 13pt.
_WATERMARK_SIZE = 10


@register_provider
class ICBCCreditProvider(BaseProvider):
    """
    Provider for ICBC (工商银行) credit card statements.

    Three sources carry the same rows:
    - The mailed statement (.eml, "ICBC Peony Card Bank Statement") and the
      reissued statement PDF (信用卡对账单（补）) print the same detail table:
      卡号后四位, 交易日, 记账日, 交易类型, 商户名称/城市, 交易金额/币种,
      记账金额/币种 with the direction in brackets ("13.40/RMB(支出)"), under a
      账单周期 line giving the cycle.
    - The online-banking history export PDF (信用卡历史明细) lists booked rows of
      an arbitrary range: 入账日期 with time, the full card number, 借/贷,
      currencies and amounts, balance, counterparty, 摘要 and 交易场所.
    """

    provider_id = "icbc_credit"
    provider_name = "工商银行信用卡"
    supported_formats = [".pdf", ".eml"]
    content_keywords = [
        "信用卡对账单（补）",
        "ICBC Peony Card Bank Statement",
        f"中国工商银行{_EXPORT_TITLE}",
    ]
    per_card_statement = True  # ICBC bills, and is repaid, per card
    coverage_priority = ("statement", "reissue", "export")

    def parse(self, file_path: Path) -> list[Transaction]:
        source = self._source(file_path)
        if source == "statement":
            html = self.extract_html_from_eml(file_path)
            tokens = list(self.parse_html(html).stripped_strings)
        else:
            import fitz

            with fitz.open(file_path) as doc:
                pages = [cast(dict[str, Any], page.get_text("rawdict")) for page in doc]
            if source == "export":
                return self._finish(*self._export_transactions(pages, file_path))
            tokens = [
                line.text
                for page in pages
                for line in _pdf_lines(page)
                # Drops the reissue's watermark, the print time repeated in
                # large type across the page.
                if line.size < _WATERMARK_SIZE
            ]
        cycle = _statement_cycle(tokens)
        if cycle is None:
            logger.warning("No 账单周期 in %s; it covers no period", file_path)
        return self._finish(self._statement_transactions(tokens, file_path), cycle)

    def _finish(
        self, transactions: list[Transaction], cycle: tuple[date, date] | None
    ) -> list[Transaction]:
        """Stamp the file's period on its rows: the cycle, or the export range.

        Coverage reads it back as printed; statement_period is widened by
        delayed postings.
        """
        # A waived annual fee is listed at 0.00 and moves no money.
        transactions = [txn for txn in transactions if txn.amount]
        self.assign_statement_periods(transactions, cycle)
        if cycle:
            for txn in transactions:
                txn.metadata[STATEMENT_CYCLE] = cycle
        return transactions

    def coverage(
        self, file_path: Path, transactions: list[Transaction]
    ) -> list[Coverage]:
        """Cover each card for the printed cycle, or the export's range.

        statement_period is widened by delayed postings, so coverage reads the
        cycle as printed. The mailed statement outranks its reissue, and the
        export only fills days no statement speaks for.
        """
        source = self._source(file_path)
        by_card: dict[str, list[Transaction]] = {}
        for txn in transactions:
            if STATEMENT_CYCLE in txn.metadata:
                by_card.setdefault(txn.card_last4 or "", []).append(txn)
        return [
            Coverage(
                card,
                *rows[0].metadata[STATEMENT_CYCLE],
                rows=rows,
                source=source,
                clip=source == "export",
            )
            for card, rows in by_card.items()
        ]

    @classmethod
    def _source(cls, file_path: Path) -> str:
        if file_path.suffix.lower() == ".eml":
            return "statement"
        if _EXPORT_TITLE in cls._read_pdf_first_page(file_path):
            return "export"
        return "reissue"

    def _statement_transactions(
        self, tokens: list[str], file_path: Path
    ) -> list[Transaction]:
        """Read detail rows from the statement's text in reading order.

        A row is the card suffix and two dates, then the type, the
        merchant/city cell (wrapped over any number of lines, or empty) and the
        two amounts; the booked amount, which ends in its direction, closes it.
        """
        transactions: list[Transaction] = []
        i = 0
        while i < len(tokens):
            if not _starts_row(tokens, i):
                i += 1
                continue
            end = i + 3
            while end < len(tokens) and not (
                _BOOKED_RE.match(tokens[end]) or _starts_row(tokens, end)
            ):
                end += 1
            booked = _BOOKED_RE.match(tokens[end]) if end < len(tokens) else None
            # A row holds at least the card, two dates, the type and the
            # original amount before its booked amount.
            original = _AMOUNT_RE.match(tokens[end - 1]) if end - i >= 5 else None
            if not booked or not original:
                logger.warning("Skipping unreadable ICBC row: %s", tokens[i:end])
                i = end
                continue
            txn_type = tokens[i + 3]
            merchant = "".join(tokens[i + 4 : end - 1])
            amount = Decimal(booked.group(1).replace(",", ""))
            currency = _iso_currency(booked.group(2))
            metadata: dict[str, Any] = {"transaction_type": txn_type}
            if (original_currency := _iso_currency(original.group(2))) != currency:
                metadata[ORIGINAL_AMOUNT] = Decimal(original.group(1).replace(",", ""))
                metadata[ORIGINAL_CURRENCY] = original_currency
            transactions.append(
                Transaction(
                    date=date.fromisoformat(tokens[i + 1]),
                    post_date=date.fromisoformat(tokens[i + 2]),
                    amount=-amount if booked.group(3) == "存入" else amount,
                    currency=currency,
                    description=f"{txn_type} {merchant}".strip(),
                    card_last4=tokens[i],
                    provider=self.provider_id,
                    source_file=file_path,
                    source_line=len(transactions) + 1,
                    metadata=metadata,
                )
            )
            i = end + 1
        return transactions

    def _export_transactions(
        self, pages: list[dict[str, Any]], file_path: Path
    ) -> tuple[list[Transaction], tuple[date, date] | None]:
        period = None
        transactions: list[Transaction] = []
        for page in pages:
            lines = sorted(
                (line for line in _pdf_lines(page) if line.direction == _ROTATED_DIR),
                key=lambda line: line.x0,
            )
            if period is None:
                text = " ".join(line.text for line in lines)
                if match := _EXPORT_RANGE_RE.search(text):
                    period = (
                        date.fromisoformat(match.group(1)),
                        date.fromisoformat(match.group(2)),
                    )
            for cells in _export_table(lines):
                txn = self._export_transaction(cells, file_path, len(transactions) + 1)
                if txn is not None:
                    transactions.append(txn)
        if period is None:
            logger.warning("No 起止日期 in %s; it covers no period", file_path)
        return transactions, period

    def _export_transaction(
        self, cells: dict[str, list[str]], file_path: Path, index: int
    ) -> Transaction | None:
        def cell(name: str) -> str:
            return "".join(cells.get(name, []))

        dates = cells.get("date", [])
        card = cell("card")
        currency = _CURRENCY_NAMES.get(cell("booked_currency"))
        amount = _decimal(cell("booked_amount"))
        direction = cell("direction")
        if (
            not dates
            or not _DATE_RE.match(dates[0])
            or len(card) < 4
            or currency is None
            or amount is None
            or direction not in ("借", "贷")
        ):
            logger.warning("Skipping unreadable ICBC export row: %s", cells)
            return None
        metadata: dict[str, Any] = {
            key: value
            for key in ("summary", "counterparty_name", "counterparty_account")
            if (value := cell(key))
        }
        if (balance := _decimal(cell("balance"))) is not None:
            metadata["balance"] = balance
        original_currency = _CURRENCY_NAMES.get(cell("currency"))
        original_amount = _decimal(cell("amount"))
        if (
            original_currency
            and original_currency != currency
            and original_amount is not None
        ):
            metadata[ORIGINAL_AMOUNT] = abs(original_amount)
            metadata[ORIGINAL_CURRENCY] = original_currency
        clock = dates[1] if len(dates) > 1 and _TIME_RE.match(dates[1]) else None
        return Transaction(
            date=date.fromisoformat(dates[0]),
            time=time.fromisoformat(clock) if clock else None,
            # A reversal of an earlier charge prints as 借 with a negative amount.
            amount=amount if direction == "借" else -amount,
            currency=currency,
            description=f"{cell('summary')} {cell('place')}".strip(),
            card_last4=card[-4:],
            provider=self.provider_id,
            source_file=file_path,
            source_line=index,
            metadata=metadata,
        )


@dataclass(frozen=True)
class _Line:
    """A run of text on one baseline, as one table cell prints it."""

    text: str
    x0: float
    y_mid: float
    size: float
    direction: tuple[float, float]


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def _iso_currency(code: str) -> str:
    return "CNY" if code == "RMB" else code


def _statement_cycle(tokens: list[str]) -> tuple[date, date] | None:
    match = _CYCLE_RE.search(" ".join(tokens))
    if not match:
        return None
    y1, m1, d1, y2, m2, d2 = map(int, match.groups())
    return date(y1, m1, d1), date(y2, m2, d2)


def _starts_row(tokens: list[str], i: int) -> bool:
    return (
        i + 2 < len(tokens)
        and _CARD_RE.match(tokens[i]) is not None
        and _DATE_RE.match(tokens[i + 1]) is not None
        and _DATE_RE.match(tokens[i + 2]) is not None
    )


def _pdf_lines(page: dict[str, Any]) -> list[_Line]:
    """Split the page's text lines into cells, in content order.

    PyMuPDF joins neighbouring cells on one baseline into a single line when
    they are written one after another, so a line is cut wherever its
    characters leave a gap wider than the font size.
    """
    lines: list[_Line] = []
    for block in page.get("blocks", []):
        for line in block.get("lines", []):
            dx, dy = line["dir"]
            direction = (round(dx, 3), round(dy, 3))
            for span in line["spans"]:
                run: list[dict[str, Any]] = []
                last_end: float | None = None
                for char in span["chars"]:
                    x0, y0, x1, y1 = char["bbox"]
                    a, b = x0 * dx + y0 * dy, x1 * dx + y1 * dy
                    begin, end = min(a, b), max(a, b)
                    if last_end is not None and begin - last_end > span["size"]:
                        lines.append(_cell(run, span["size"], direction))
                        run = []
                    run.append(char)
                    last_end = end
                lines.append(_cell(run, span["size"], direction))
    return [line for line in lines if line.text]


def _cell(
    chars: list[dict[str, Any]], size: float, direction: tuple[float, float]
) -> _Line:
    text = "".join(char["c"] for char in chars).strip()
    x0 = min((char["bbox"][0] for char in chars), default=0.0)
    y0 = min((char["bbox"][1] for char in chars), default=0.0)
    y1 = max((char["bbox"][3] for char in chars), default=0.0)
    return _Line(text, x0, (y0 + y1) / 2, size, direction)


def _export_table(lines: list[_Line]) -> list[dict[str, list[str]]]:
    """Group a page's export lines into rows of named cells.

    Every row starts with its 入账日期, at the row's left edge; a wrapped cell
    spreads around that edge but stays short of the next row's. The last row
    on a page is bounded by the usual row pitch, which keeps the footer totals
    out of it.
    """
    headers = [line for line in lines if line.text in _EXPORT_COLUMNS]
    if "入账日期" not in {line.text for line in headers}:
        return []
    header_x = max(line.x0 for line in headers)

    def column(line: _Line) -> str:
        nearest = min(headers, key=lambda h: abs(h.y_mid - line.y_mid))
        return _EXPORT_COLUMNS[nearest.text]

    body = [(line, column(line)) for line in lines if line.x0 > header_x + _X_SLACK]
    starts = [
        line.x0 for line, col in body if col == "date" and _DATE_RE.match(line.text)
    ]
    if not starts:
        return []
    pitch = min((b - a for a, b in zip(starts, starts[1:])), default=_ROW_PITCH)
    bounds = [*starts[1:], starts[-1] + pitch]

    table: list[dict[str, list[str]]] = [{} for _ in starts]
    for line, col in body:
        for cells, start, stop in zip(table, starts, bounds, strict=True):
            if start - _X_SLACK <= line.x0 < stop - _X_SLACK:
                cells.setdefault(col, []).append(line.text)
                break
    return table
