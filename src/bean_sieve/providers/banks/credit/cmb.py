"""China Merchants Bank (招商银行) credit card statement provider."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ....core.coverage import Coverage
from ....core.metadata_keys import ORIGINAL_AMOUNT
from ....core.types import Transaction
from ... import register_provider
from ...base import BaseProvider

_FOUR_DIGITS_RE = re.compile(r"^\d{4}$")

_PDF_MONTH_RE = re.compile(r"对账单（[^）]*?(\d{4})年(\d{1,2})月）")
_PDF_CLOSING_RE = re.compile(r"账单日\s*(\d{4})年(\d{1,2})月(\d{1,2})日")
_PDF_DATE_RE = re.compile(r"^\d{2}/\d{2}$")
_PDF_HEADERS = {
    "交易日": "trans_date",
    "记账日": "post_date",
    "交易摘要": "description",
    "人民币金额": "amount",
    "美元金额": "amount",
    "卡号末四位": "card",
    "交易地金额": "original",
}
_PDF_ACCOUNTS = {"人民币账户": "CNY", "美元账户": "USD"}
# Amounts are right-aligned, so a long one starts left of its header label.
_PDF_COLUMN_SLACK = 12.0
_PDF_ROW_TOLERANCE = 3.0


@dataclass
class _Row:
    """One statement row as printed, before dates and amounts are read."""

    trans_date: str  # MMDD, empty for repayments
    post_date: str  # MMDD
    description: str
    amount: str
    card: str
    original_amount: str
    country: str
    currency: str
    line: int


@dataclass
class _Statement:
    month_span: tuple[date, date]
    # The latest date a row can carry, which places MMDD dates in a year.
    closing: date
    source: str
    itemized: bool
    # The accounts (人民币账户, 美元账户) the file reports, as currencies.
    currencies: tuple[str, ...]
    rows: list[_Row] = field(default_factory=list)


@dataclass
class _PdfLine:
    page: int
    x: float
    y: float
    text: str


def _month_span(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _resolve_date(mmdd: str, latest: date) -> date:
    """Place an MMDD date in the latest year that keeps it on or before latest.

    An instalment row keeps its purchase date, which can lie many months
    before the cycle, so the year is not simply the statement's.
    """
    month, day = int(mmdd[:2]), int(mmdd[2:])
    # 02/29 needs a leap year, at most four years back.
    for year in range(latest.year, latest.year - 5, -1):
        try:
            resolved = date(year, month, day)
        except ValueError:
            continue
        if resolved <= latest:
            return resolved
    raise ValueError(f"invalid statement date {mmdd}")


@register_provider
class CMBCreditProvider(BaseProvider):
    """
    Provider for China Merchants Bank (招商银行) credit card statements.

    Two formats carry the same table:
    - .eml: the mailed statement (正本), an HTML table of class "bgTable";
      older mails carry only the summary and no table
    - .pdf: the reissued statement (补发账单) from the app, a text PDF

    Row columns: 交易日 (empty for repayments), 记账日, 交易摘要, 人民币金额
    (美元金额 in the USD account section), 卡号末四位, 交易地金额 and 交易地
    (the PDF appends it to 交易地金额 in parentheses).

    CMB uses unified account management (按户管理), all cards share one statement.
    """

    provider_id = "cmb_credit"
    provider_name = "招商银行信用卡"
    supported_formats = [".eml", ".pdf"]
    filename_keywords = ["招商银行信用卡", "招行信用卡"]
    content_keywords = ["招商银行信用卡"]
    coverage_priority = ("statement", "reissue")

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse a CMB credit card statement, mailed or reissued."""
        statement = self._read(file_path)
        transactions = [
            txn
            for row in statement.rows
            if (txn := self._build(row, statement.closing, file_path)) is not None
        ]
        self.assign_statement_periods(transactions, statement.month_span)
        return transactions

    def coverage(
        self, file_path: Path, transactions: list[Transaction]
    ) -> list[Coverage]:
        """Cover the statement month for each currency account, all cards.

        Most mails print no billing cycle, only the statement month, so both
        formats cover that calendar month: a mail and a reissue of one month
        always overlap, and consecutive months never do. Mails list only the
        人民币账户, so a reissue still supplies the 美元账户 of a mailed month.
        """
        statement = self._read(file_path)
        return [
            Coverage(
                currency,
                *statement.month_span,
                rows=[txn for txn in transactions if txn.currency == currency],
                itemized=statement.itemized,
                source=statement.source,
            )
            for currency in statement.currencies
        ]

    def _read(self, file_path: Path) -> _Statement:
        if file_path.suffix.lower() == ".pdf":
            return self._read_pdf(file_path)
        return self._read_eml(file_path)

    def _read_eml(self, file_path: Path) -> _Statement:
        html = self.extract_html_from_eml(file_path)
        month = _month_span(*self._extract_statement_month(html, file_path))
        tables = self.parse_html(html).find_all("table", class_="bgTable")
        rows = [row for table in tables for row in self._eml_rows(table)]
        return _Statement(
            month,
            closing=month[1],
            source="statement",
            itemized=bool(tables),
            currencies=("CNY",),
            rows=rows,
        )

    def _extract_statement_month(self, html: str, file_path: Path) -> tuple[int, int]:
        match = re.search(r"(\d{4})年(\d{1,2})月.*?账单", html)
        if match:
            return int(match.group(1)), int(match.group(2))

        match = re.search(r"(\d{4})年(\d{1,2})月", file_path.name)
        if match:
            return int(match.group(1)), int(match.group(2))

        today = date.today()
        return today.year, today.month

    def _eml_rows(self, table) -> list[_Row]:
        rows: list[_Row] = []
        for row_idx, tr in enumerate(table.find_all("tr")):
            cells = [self.clean_text(td.get_text()) for td in tr.find_all("td")]
            # Only 9-cell rows hold a transaction; 8-cell rows repeat them.
            if len(cells) != 9:
                continue
            rows.append(
                _Row(
                    trans_date=cells[2],
                    post_date=cells[3],
                    description=cells[4],
                    amount=cells[5],
                    card=cells[6],
                    original_amount=cells[7],
                    country=cells[8],
                    currency="CNY",
                    line=row_idx + 1,
                )
            )
        return rows

    def _read_pdf(self, file_path: Path) -> _Statement:
        import fitz

        lines: list[_PdfLine] = []
        text = ""
        with fitz.open(file_path) as doc:
            for page_no in range(doc.page_count):
                page = doc[page_no]
                text += str(page.get_text())
                layout: dict = page.get_text("dict")  # pyright: ignore[reportAssignmentType]
                for block in layout["blocks"]:
                    for line in block.get("lines", []):
                        content = "".join(s["text"] for s in line["spans"]).strip()
                        if content:
                            x0, y0 = line["bbox"][:2]
                            lines.append(_PdfLine(page_no, x0, y0, content))
        lines.sort(key=lambda ln: (ln.page, ln.y, ln.x))

        month_match = _PDF_MONTH_RE.search(text)
        if month_match is None:
            raise ValueError(f"{file_path.name}: no CMB statement month found")
        month = _month_span(int(month_match.group(1)), int(month_match.group(2)))
        closing_match = _PDF_CLOSING_RE.search(text)
        closing = (
            date(*(int(g) for g in closing_match.groups()))
            if closing_match
            else month[1]
        )
        return _Statement(
            month,
            closing=closing,
            source="reissue",
            itemized=True,
            currencies=tuple(
                dict.fromkeys(
                    code
                    for ln in lines
                    for heading, code in _PDF_ACCOUNTS.items()
                    if ln.text.startswith(heading)
                )
            )
            or ("CNY",),
            rows=self._pdf_rows(lines),
        )

    def _pdf_rows(self, lines: list[_PdfLine]) -> list[_Row]:
        """Group the page lines into rows, one per 记账日 cell.

        Each cell is a line of its own; a row's cells share the post date's
        baseline, and the header label above a column gives its x.
        """
        columns: dict[str, float] = {}
        for ln in lines:
            if ln.text in _PDF_HEADERS:
                columns.setdefault(_PDF_HEADERS[ln.text], ln.x)
        edges = sorted((x, name) for name, x in columns.items())

        def column(ln: _PdfLine) -> str | None:
            names = [name for x, name in edges if x <= ln.x + _PDF_COLUMN_SLACK]
            return names[-1] if names else None

        rows: list[_Row] = []
        currency = "CNY"
        for ln in lines:
            for heading, code in _PDF_ACCOUNTS.items():
                if ln.text.startswith(heading):
                    currency = code
            if not _PDF_DATE_RE.match(ln.text) or column(ln) != "post_date":
                continue
            cells: dict[str, str] = {}
            for other in lines:
                if other.page != ln.page or abs(other.y - ln.y) > _PDF_ROW_TOLERANCE:
                    continue
                name = column(other)
                # A section label (还款, 消费, …) can share the 交易日 column
                # with the first row of its section.
                if name == "trans_date" and not _PDF_DATE_RE.match(other.text):
                    continue
                if name:
                    cells[name] = (cells.get(name, "") + " " + other.text).strip()
            # 交易地金额 carries the country code, as "92.60(HK)".
            original, _, country = cells.get("original", "").partition("(")
            rows.append(
                _Row(
                    trans_date=cells.get("trans_date", "").replace("/", ""),
                    post_date=ln.text.replace("/", ""),
                    description=cells.get("description", ""),
                    amount=cells.get("amount", ""),
                    card=cells.get("card", ""),
                    original_amount=original,
                    country=country.rstrip(")"),
                    currency=currency,
                    line=len(rows) + 1,
                )
            )
        return rows

    def _build(self, row: _Row, closing: date, file_path: Path) -> Transaction | None:
        if not _FOUR_DIGITS_RE.match(row.post_date) or not row.description:
            return None
        amount = self._parse_amount(row.amount)
        if amount is None:
            return None
        try:
            post_date = _resolve_date(row.post_date, closing)
            # A mail prints no 账单日, so its closing is the month end; bounding
            # the trans date by the post date places it alike in both formats.
            trans_date = (
                _resolve_date(row.trans_date, post_date)
                if _FOUR_DIGITS_RE.match(row.trans_date)
                else None
            )
        except ValueError:
            return None

        metadata: dict = {}
        if trans_date and trans_date != post_date:
            metadata["posting_date"] = post_date.isoformat()

        # The statement gives the original amount without its currency, so
        # only the number is kept; a country code does not pin a currency.
        original_amount = self._parse_amount(row.original_amount)
        if original_amount is not None and original_amount != amount:
            metadata[ORIGINAL_AMOUNT] = original_amount
            if row.country:
                metadata["country"] = row.country

        return Transaction(
            date=trans_date or post_date,
            amount=amount,
            currency=row.currency,
            description=row.description,
            card_last4=row.card if _FOUR_DIGITS_RE.match(row.card) else None,
            provider=self.provider_id,
            source_file=file_path,
            source_line=row.line,
            metadata=metadata,
        )

    def _parse_amount(self, text: str) -> Decimal | None:
        cleaned = self.clean_text(text).replace("¥", "").replace(",", "").strip()
        if not cleaned:
            return None
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return None
