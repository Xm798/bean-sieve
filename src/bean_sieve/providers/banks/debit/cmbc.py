"""China Minsheng Bank (民生银行) debit card statement provider."""

from __future__ import annotations

import dataclasses
import logging
import re
from bisect import bisect_right
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from ....core.coverage import Coverage
from ....core.types import Transaction
from ... import register_provider
from ..._tabular import currency_code
from ...base import BaseProvider

if TYPE_CHECKING:
    import fitz

logger = logging.getLogger(__name__)

# The title alone is a generic phrase; the bank name alone appears in other
# banks' counterparty columns. Detection needs both.
_MARKERS = ("个人账户对账单", "民生银行")
_CARD_RE = re.compile(r"客户账号:\s*(\d+)")
_PERIOD_RE = re.compile(r"起止日期:\s*(\d{4}/\d{2}/\d{2})-(\d{4}/\d{2}/\d{2})")
_CURRENCY_RE = re.compile(r"币\s*种:\s*(\S+)")
_COUNT_RE = re.compile(r"合计笔数:\s*(\d+)")
_DATE_RE = re.compile(r"\d{4}/\d{2}/\d{2}")
# The rule line, the totals line or the print stamp closes the table on a page.
_FOOTER_PREFIXES = ("__", "支出交易总额", "打印渠道")

# 凭证类型 | 凭证号码 | 交易时间 | 摘要 | 交易金额 | 账户余额 | 现转标志 |
# 交易渠道 | 交易机构 | 对方户名/账号 | 对方行名
_NUM_COLS = 11
_COL_TIME = 2
_COL_SUMMARY = 3
_COL_AMOUNT = 4
_COL_BALANCE = 5
_COL_CASH_TRANSFER = 6
_COL_CHANNEL = 7
_COL_BRANCH = 8
_COL_COUNTERPARTY = 9
_COL_COUNTERPARTY_BANK = 10
# Text in a row's other cells may start a fraction above the date's top edge.
_ROW_SLACK = 1.0


class _Word(NamedTuple):
    x0: float
    y0: float
    text: str


def _join_cell(words: list[_Word]) -> str:
    """Rejoin a cell the PDF wrapped at the column width, mid-word included."""
    lines: dict[float, list[_Word]] = {}
    for word in words:
        lines.setdefault(round(word.y0, 1), []).append(word)
    return "".join(" ".join(w.text for w in sorted(lines[y])) for y in sorted(lines))


def _is_footer(words: list[_Word], i: int) -> bool:
    """Whether words[i] opens a footer line: a fixed label or "第 n 页"."""
    text = words[i].text
    if text.startswith(_FOOTER_PREFIXES):
        return True
    return text == "第" and i + 1 < len(words) and words[i + 1].text.isdigit()


class _PageTable(NamedTuple):
    """One page of the table: cell texts above its first date, then its rows.

    ``lead`` holds the tail of a row the previous page broke off, one text per
    column, all empty when the page opens with a row; a page without the
    table has no lead at all, and nothing on it continues a row.
    """

    lead: list[str]
    rows: list[list[str]]


def _page_table(page: fitz.Page) -> _PageTable | None:
    """The transaction cells on one page, None for an unknown layout.

    The table has ruled header cells but no ruled body: a row starts at the
    date in the 交易时间 column and runs until the next row's date.
    """
    words = [_Word(float(w[0]), float(w[1]), str(w[4])) for w in page.get_text("words")]
    label = next((w for w in words if w.text == "交易时间"), None)
    if label is None:
        return _PageTable([], [])
    cells = [
        d["rect"]
        for d in page.get_drawings()
        if d["rect"].y0 <= label.y0 <= d["rect"].y1
    ]
    edges = sorted({round(x, 1) for r in cells for x in (r.x0, r.x1)})
    if len(edges) != _NUM_COLS + 1:
        return None
    top = max(r.y1 for r in cells)
    bottom = min(
        (w.y0 for i, w in enumerate(words) if w.y0 >= top and _is_footer(words, i)),
        default=page.rect.y1,
    )
    body = [(bisect_right(edges, w.x0) - 1, w) for w in words if top <= w.y0 < bottom]
    starts = sorted(
        {w.y0 for col, w in body if col == _COL_TIME and _DATE_RE.fullmatch(w.text)}
    )
    # Slot 0 takes the words above the page's first date: the tail of a row
    # the previous page cut off.
    cells_by_row = [[[] for _ in range(_NUM_COLS)] for _ in range(len(starts) + 1)]
    for col, word in body:
        if 0 <= col < _NUM_COLS:
            cells_by_row[bisect_right(starts, word.y0 + _ROW_SLACK)][col].append(word)
    lead, *rows = [[_join_cell(cell) for cell in row] for row in cells_by_row]
    return _PageTable(lead, rows)


def _parse_period(header: str) -> tuple[date, date] | None:
    match = _PERIOD_RE.search(header)
    if not match:
        return None
    start, end = (datetime.strptime(g, "%Y/%m/%d").date() for g in match.groups())
    return start, end


@register_provider
class CMBCDebitProvider(BaseProvider):
    """
    Provider for China Minsheng Bank debit card statements (个人账户对账单, PDF).

    File format:
    - Landscape pages, each repeating the title 个人账户对账单 and the header
      block: 客户姓名, 客户账号 (card number), 产品名称, 币种, 钞汇标志,
      开户机构, 账户账号, 起止日期 (YYYY/MM/DD-YYYY/MM/DD), 筛选条件, 证件号码
    - Ruled header cells, unruled body: 凭证类型, 凭证号码, 交易时间
      (YYYY/MM/DD HH:MM:SS), 摘要, 交易金额 (signed, negative = outflow),
      账户余额, 现转标志, 交易渠道, 交易机构, 对方户名/账号, 对方行名
    - Cells wrap at the column width onto extra lines
    - The last page closes with 支出交易总额, 收入交易总额 and 合计笔数
    """

    provider_id = "cmbc_debit"
    provider_name = "民生银行借记卡"
    supported_formats = [".pdf"]
    per_card_statement = True  # CMBC issues one statement per account
    # A statement missing a page for its layout still stands for its period
    # when it is the only one, but a complete copy holds the rows it lost.
    coverage_priority = ("statement", "partial")

    def __init__(self) -> None:
        super().__init__()
        # Each file's coverage as parse() read it, rows left to coverage();
        # None when the file covers no period.
        self._coverages: dict[Path, Coverage | None] = {}

    @classmethod
    def can_handle(cls, file_path: Path) -> bool:
        if file_path.suffix.lower() not in cls.supported_formats:
            return False
        try:
            text = cls._read_pdf_first_page(file_path)
        except Exception:
            return False
        return all(marker in text for marker in _MARKERS)

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse a CMBC debit card statement PDF."""
        import fitz

        self._coverages[file_path] = None
        with fitz.open(file_path) as doc:
            if not doc.page_count:
                return []
            header = str(doc[0].get_text())
            footer = str(doc[-1].get_text())
            rows: list[list[str]] = []
            skipped = False
            previous_read = False
            for index in range(doc.page_count):
                table = _page_table(doc[index])
                if table is None:
                    logger.warning(
                        "Skipping page %d of %s: unexpected header layout",
                        index + 1,
                        file_path,
                    )
                    skipped = True
                else:
                    # A row broken off at a page end continues in the same
                    # columns above the next page's first date.
                    if any(table.lead) and previous_read and rows:
                        rows[-1] = [
                            a + b for a, b in zip(rows[-1], table.lead, strict=True)
                        ]
                    rows.extend(table.rows)
                previous_read = table is not None and bool(table.lead)

        currency_name = _CURRENCY_RE.search(header)
        currency = currency_code(currency_name.group(1)) if currency_name else None
        if currency is None:
            logger.warning("Unsupported currency in %s", file_path)
            return []
        card = _CARD_RE.search(header)
        card_last4 = card.group(1)[-4:] if card else None

        count = _COUNT_RE.search(footer)
        if count and int(count.group(1)) != len(rows):
            logger.warning(
                "%s lists %s transactions, parsed %d",
                file_path,
                count.group(1),
                len(rows),
            )

        transactions = [
            txn
            for row_num, row in enumerate(rows, start=1)
            if (txn := self._build(row, row_num, card_last4, currency, file_path))
        ]
        period = _parse_period(header)
        self.assign_statement_periods(transactions, period)
        if period:
            self._coverages[file_path] = Coverage(
                card_last4 or "", *period, source="partial" if skipped else "statement"
            )
        return transactions

    def coverage(
        self, file_path: Path, transactions: list[Transaction]
    ) -> list[Coverage]:
        """Cover the printed 起止日期 for the account, quiet periods included.

        Requires parse() of file_path on this provider: reads the period it
        kept.
        """
        if file_path not in self._coverages:
            raise RuntimeError(f"coverage() of {file_path} before parse()")
        kept = self._coverages[file_path]
        return [dataclasses.replace(kept, rows=transactions)] if kept else []

    def _build(
        self,
        row: list[str],
        row_num: int,
        card_last4: str | None,
        currency: str,
        file_path: Path,
    ) -> Transaction | None:
        try:
            stamp = datetime.strptime(row[_COL_TIME], "%Y/%m/%d %H:%M:%S")
            value = Decimal(row[_COL_AMOUNT].replace(",", ""))
        except (ValueError, InvalidOperation):
            logger.warning("Skipping malformed row %d in %s", row_num, file_path)
            return None

        counterparty = row[_COL_COUNTERPARTY]
        name, sep, account = counterparty.rpartition("/")
        if not sep:
            name = counterparty
        metadata = {
            "balance": row[_COL_BALANCE],
            "cash_transfer": row[_COL_CASH_TRANSFER],
            "channel": row[_COL_CHANNEL],
            "branch": row[_COL_BRANCH],
            "counterparty_account": account,
            "counterparty_bank": row[_COL_COUNTERPARTY_BANK],
        }

        return Transaction(
            date=stamp.date(),
            time=stamp.time(),
            # The statement signs outflows negative; expenses are positive here.
            amount=-value,
            currency=currency,
            description=row[_COL_SUMMARY] or "Unknown",
            payee=name or None,
            card_last4=card_last4,
            provider=self.provider_id,
            source_file=file_path,
            source_line=row_num,
            metadata={key: val for key, val in metadata.items() if val},
        )
