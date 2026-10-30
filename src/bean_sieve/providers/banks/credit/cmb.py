"""China Merchants Bank (招商银行) credit card statement provider."""

from __future__ import annotations

import calendar
import email
import logging
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

logger = logging.getLogger(__name__)

_FOUR_DIGITS_RE = re.compile(r"^\d{4}$")

_PDF_MONTH_RE = re.compile(r"对账单（[^）]*?(\d{4})年(\d{1,2})月）")
_PDF_CLOSING_RE = re.compile(r"账单日\s*(\d{4})年(\d{1,2})月(\d{1,2})日")
_PDF_DATE_RE = re.compile(r"^\d{2}/\d{2}$")
_HEADERS = {
    "交易日": "trans_date",
    "记账日": "post_date",
    "交易摘要": "description",
    "人民币金额": "amount",
    "美元金额": "amount",
    "卡号末四位": "card",
    "交易地金额": "original_amount",
}
_AMOUNT_CURRENCIES = {"人民币金额": "CNY", "美元金额": "USD"}
_PDF_ACCOUNTS = {"人民币账户": "CNY", "美元账户": "USD"}

# The PDF appends 交易地 to 交易地金额; a mail gives it a column of its own.
_MAIL_HEADERS = {**_HEADERS, "交易地": "country"}
# Older mails draw the column header as an image, over columns in this order.
_MAIL_IMAGE_HEADER = (
    "",
    "交易日",
    "记账日",
    "交易摘要",
    "人民币金额",
    "卡号末四位",
    "交易地",
    "交易地金额",
)
_MAIL_MONTH_RE = re.compile(r"(\d{4})年(\d{1,2})月信用卡账单")
# Older mails print the billing cycle instead of the statement month.
_MAIL_CYCLE_RE = re.compile(r"\d{4}/\d{2}/\d{2}\s*-\s*(\d{4})/(\d{2})/(\d{2})")
_YEAR_MONTH_RE = re.compile(r"(\d{4})年(\d{1,2})月")
_CURRENCY_SIGNS = "¥￥$＄"
_AMOUNT_CELL_RE = re.compile(f"^[{_CURRENCY_SIGNS}]")
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


_ROW_TEXT_FIELDS = (
    "trans_date",
    "post_date",
    "description",
    "amount",
    "card",
    "original_amount",
    "country",
)


@dataclass
class _Statement:
    month_span: tuple[date, date]
    # The latest date a row can carry, which places MMDD dates in a year.
    closing: date
    source: str
    # The accounts (人民币账户, 美元账户) the file reports, by currency, and
    # whether each one is itemized.
    accounts: dict[str, bool]
    rows: list[_Row] = field(default_factory=list)


@dataclass
class _PdfLine:
    page: int
    x: float
    y: float
    text: str


def _section_currency(labels: tuple[str, ...]) -> str | None:
    return next(
        (_AMOUNT_CURRENCIES[lb] for lb in labels if lb in _AMOUNT_CURRENCIES), None
    )


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
    - .eml: the mailed statement (正本), an HTML table whose sections each
      open with a loopHeader2 band: a row of column labels, or in older
      mails an image over the columns of ``_MAIL_IMAGE_HEADER``; some mails
      carry only the summary and no table
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

    def __init__(self) -> None:
        super().__init__()
        # Each file's statement as parse() read it, None for a skipped mail;
        # reading a file again would repeat its warnings.
        self._statements: dict[Path, _Statement | None] = {}

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse a CMB credit card statement, mailed or reissued."""
        statement = self._statements[file_path] = self._read(file_path)
        if statement is None:
            return []
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
        always overlap, and consecutive months never do. A reissue still
        supplies an account its month's mail does not list.

        Requires parse() of file_path on this provider: reads the statement
        it kept.
        """
        if file_path not in self._statements:
            raise RuntimeError(f"coverage() of {file_path} before parse()")
        statement = self._statements[file_path]
        if statement is None:
            return []
        return [
            Coverage(
                currency,
                *statement.month_span,
                rows=[txn for txn in transactions if txn.currency == currency],
                itemized=itemized,
                source=statement.source,
            )
            for currency, itemized in statement.accounts.items()
        ]

    def _read(self, file_path: Path) -> _Statement | None:
        if file_path.suffix.lower() == ".pdf":
            return self._read_pdf(file_path)
        return self._read_eml(file_path)

    def _read_eml(self, file_path: Path) -> _Statement | None:
        with open(file_path, "rb") as f:
            msg = email.message_from_binary_file(f)
        soup = self.parse_html(self._extract_html_from_message(msg))
        found = self._mail_month(
            self.clean_text(soup.get_text()), self.decode_subject(msg), file_path
        )
        if found is None:
            # Guessing a month would make the run depend on the day it runs.
            # Known mails without one are summaries; the count exposes a new
            # template that drops the month from an itemized mail.
            dated = sum(1 for tr in soup.find_all("tr") if self._is_dated_row(tr))
            logger.warning(
                "No statement month in %s, skipping it and its %d dated rows",
                file_path,
                dated,
            )
            return None
        year, month, closing = found
        span = _month_span(year, month)
        sections, rows, skipped = self._mail_rows(soup)
        recognised = bool(sections)
        if recognised and skipped:
            logger.warning("Unrecognised CMB statement section in %s", file_path)
        if not recognised:
            sections = {
                "CNY": any(self._is_dated_row(tr) for tr in soup.find_all("tr"))
            }
        accounts: dict[str, bool] = {}
        for currency, unread in sections.items():
            # Dated rows that no section reads mean the bank changed its
            # layout; an empty account alone would look like a quiet month,
            # and an itemized one would displace the reissue's account.
            unread = unread and not any(row.currency == currency for row in rows)
            if unread:
                logger.warning(
                    "Unrecognised CMB statement layout in %s (%s account)",
                    file_path,
                    currency,
                )
            accounts[currency] = recognised and not unread
        return _Statement(
            span,
            closing=closing or span[1],
            source="statement",
            accounts=accounts,
            rows=rows,
        )

    @staticmethod
    def _mail_month(
        text: str, subject: str, file_path: Path
    ) -> tuple[int, int, date | None] | None:
        """Return the statement month and, when printed, the closing date."""
        if match := _MAIL_MONTH_RE.search(text):
            return int(match.group(1)), int(match.group(2)), None
        if match := _MAIL_CYCLE_RE.search(text):
            closing = date(*(int(g) for g in match.groups()))
            return closing.year, closing.month, closing
        for source in (subject, file_path.name):
            if match := _YEAR_MONTH_RE.search(source):
                return int(match.group(1)), int(match.group(2)), None
        return None

    def _mail_rows(self, soup) -> tuple[dict[str, bool], list[_Row], bool]:
        """Read the rows of each section, in mail order.

        A section is the loopBand2 band holding its header. Returns, by the
        currency of each recognised section, whether a dated row inside it
        was left unread; the rows; and whether a section with an
        unrecognised header holds dated rows.
        """
        unread: dict[str, bool] = {}
        skipped = False
        band = None
        # The field name (None for an unread cell) of each column, and the
        # currency, of the section being read.
        section: tuple[list[str | None], str] | None = None
        rows: list[_Row] = []
        for el in soup.find_all(["span", "tr"]):
            if el.name == "span":
                if el.get("id") == "loopHeader2":
                    header = self._section_header(el)
                    band = section = None
                    if header is None:
                        own_band = el.find_parent("span", id="loopBand2") or el.parent
                        skipped |= any(
                            self._is_dated_row(tr) for tr in own_band.find_all("tr")
                        )
                    else:
                        labels, currency = header
                        band = el.find_parent("span", id="loopBand2") or el.parent
                        section = ([_MAIL_HEADERS.get(lb) for lb in labels], currency)
                        unread.setdefault(currency, False)
                continue
            if band is None:
                continue
            if not any(parent is band for parent in el.parents):
                band = section = None
                continue
            if section is None or el.find_parent("span", id="loopHeader2"):
                continue
            columns, currency = section
            cells = el.find_all("td", recursive=False)
            if len(cells) != len(columns):
                unread[currency] |= self._is_dated_row(el)
                continue
            fields = dict.fromkeys(_ROW_TEXT_FIELDS, "") | {
                name: self.clean_text(cell.get_text())
                for name, cell in zip(columns, cells, strict=True)
                if name
            }
            rows.append(_Row(**fields, currency=currency, line=len(rows) + 1))
        return unread, rows, skipped

    def _section_header(self, header) -> tuple[tuple[str, ...], str] | None:
        """Return the column labels and currency of a section header.

        None when the header is unrecognised.
        """
        for tr in header.find_all("tr"):
            labels = tuple(self._cell_texts(tr))
            if "记账日" in labels and (currency := _section_currency(labels)):
                return labels, currency
        if header.find("img") and not self.clean_text(header.get_text()):
            return _MAIL_IMAGE_HEADER, "CNY"
        return None

    def _is_dated_row(self, tr) -> bool:
        # Wrapper rows hold whole nested tables; only leaf rows are table rows.
        if tr.find("tr"):
            return False
        cells = self._cell_texts(tr)
        return any(_FOUR_DIGITS_RE.match(c) for c in cells) and any(
            _AMOUNT_CELL_RE.match(c) for c in cells
        )

    def _cell_texts(self, tr) -> list[str]:
        return [
            self.clean_text(td.get_text()) for td in tr.find_all("td", recursive=False)
        ]

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
            accounts=dict.fromkeys(
                [
                    code
                    for ln in lines
                    for heading, code in _PDF_ACCOUNTS.items()
                    if ln.text.startswith(heading)
                ]
                or ["CNY"],
                True,
            ),
            rows=self._pdf_rows(lines),
        )

    def _pdf_rows(self, lines: list[_PdfLine]) -> list[_Row]:
        """Group the page lines into rows, one per 记账日 cell.

        Each cell is a line of its own; a row's cells share the post date's
        baseline, and the header label above a column gives its x.
        """
        columns: dict[str, float] = {}
        for ln in lines:
            if ln.text in _HEADERS:
                columns.setdefault(_HEADERS[ln.text], ln.x)
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
            original, _, country = cells.get("original_amount", "").partition("(")
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
            post_date=post_date,
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
        # Older mails put a space between the sign and the digits.
        cleaned = re.sub(rf"[{_CURRENCY_SIGNS},\s]", "", text)
        if not cleaned:
            return None
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return None
