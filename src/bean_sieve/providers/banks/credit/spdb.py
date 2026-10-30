"""Shanghai Pudong Development Bank (浦发银行) credit card provider."""

from __future__ import annotations

import calendar
import logging
import re
from collections.abc import Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import xlrd
from xlrd.biffh import XLRDError

from ....core.coverage import Coverage
from ....core.metadata_keys import (
    ORIGINAL_AMOUNT,
    ORIGINAL_CURRENCY,
    STATEMENT_CYCLE,
)
from ....core.types import Transaction
from ... import register_provider
from ..._tabular import currency_code
from ...base import BaseProvider

logger = logging.getLogger(__name__)

_PDF_TITLE = "浦发个人信用卡对账单"
_PDF_DETAIL_MARKER = "本期账务明细"
_PDF_MONTH_RE = re.compile(r"(\d{4})(\d{2})信用卡账单")
_PDF_BILLING_DAY_RE = re.compile(
    r"账单日[：:]\s*\n\s*Statement Due Date\s*\n\s*(\d{1,2})"
)
_PDF_DATE_RE = re.compile(r"^\d{8}$")
_PDF_CARD_RE = re.compile(r"^\d{4}$")
_PDF_AMOUNT_RE = re.compile(r"^([￥$])\s*(-?[\d,]+\.\d+)$")
_PDF_ORIGINAL_RE = re.compile(r"^-?[\d,]+\.\d+\([A-Z]{3}\)$")
# A description wraps onto a few lines at most; a longer run between two
# dates is page furniture, not a row.
_PDF_MAX_DESCRIPTION_LINES = 4
_PDF_CURRENCY_SYMBOLS = {"￥": "CNY", "$": "USD"}


@register_provider
class SPDBCreditProvider(BaseProvider):
    """
    Provider for SPDB credit card transaction-detail XLS reports and PDF
    statements.

    XLS file format:
    - Format: XLS (BIFF8)
    - Header: first 10 rows, normally row 0
    - Columns: 交易日期, 记账日期, 交易摘要, 卡号末四位, 卡片类型,
      交易币种, 交易金额, 原始交易金额&币种
    - Amount sign: positive = expense, negative = payment/refund
    - Date format: YYYYMMDD
    - One monthly report may contain transactions from multiple cards

    PDF file format:
    - Title "YYYYMM信用卡账单", billing day under "账单日"
    - Detail table: 交易日期, 记账日期, 交易摘要, 卡号末四位, 交易金额
      (currency symbol prefix), and on newer templates 原始交易金额&币种
    - Rows continue across pages without repeating the header
    """

    provider_id = "spdb_credit"
    provider_name = "浦发银行信用卡"
    supported_formats = [".xls", ".pdf"]
    filename_keywords = ["浦发", "spdb"]
    content_keywords = [_PDF_TITLE]
    # The XLS export is the bank's own record of the cycle; a PDF is
    # downloaded later as a reissue, and nothing in it tells an original
    # statement from a reissue.
    coverage_priority = ("statement", "reissue")

    EXPECTED_HEADERS = (
        "交易日期",
        "记账日期",
        "交易摘要",
        "卡号末四位",
        "卡片类型",
        "交易币种",
        "交易金额",
        "原始交易金额&币种",
    )
    EXPECTED_COLS = len(EXPECTED_HEADERS)

    COL_TRANS_DATE = 0
    COL_POST_DATE = 1
    COL_DESCRIPTION = 2
    COL_CARD_LAST4 = 3
    COL_CARD_TYPE = 4
    COL_CURRENCY = 5
    COL_AMOUNT = 6
    COL_ORIGINAL_AMOUNT = 7

    @classmethod
    def can_handle(cls, file_path: Path) -> bool:
        """Detect explicit SPDB filenames or the report's unique XLS header.

        PDFs are told by their title alone: the filename keywords also match
        SPDB debit-card statements.
        """
        suffix = file_path.suffix.lower()
        if suffix == ".pdf":
            return cls._match_content(file_path)
        if suffix not in cls.supported_formats:
            return False

        filename = file_path.name.lower()
        filename_matches = any(
            keyword.lower() in filename for keyword in cls.filename_keywords
        )
        if not file_path.is_file():
            return filename_matches

        try:
            workbook = xlrd.open_workbook(str(file_path), on_demand=True)
            sheet = workbook.sheet_by_index(0)
            return (
                sheet.ncols >= cls.EXPECTED_COLS
                and cls._find_header_row(sheet) is not None
            )
        except (OSError, XLRDError):
            return False

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse an SPDB credit card XLS report or PDF statement."""
        if file_path.suffix.lower() == ".pdf":
            return self._parse_pdf(file_path)
        return self._parse_xls(file_path)

    def coverage(
        self,
        file_path: Path,
        transactions: list[Transaction],
    ) -> list[Coverage]:
        """Cover each card for the PDF's cycle or the XLS's booked dates.

        The XLS prints no cycle. Its rows' post dates all fall in the billing
        cycle while a transaction date can precede it, so the post-date span
        stays inside the cycle without reaching into the previous one.
        """
        source = "reissue" if file_path.suffix.lower() == ".pdf" else "statement"
        by_card: dict[str, list[Transaction]] = {}
        for txn in transactions:
            by_card.setdefault(txn.card_last4 or "", []).append(txn)

        coverages: list[Coverage] = []
        for card, rows in by_card.items():
            cycle = rows[0].metadata.get(STATEMENT_CYCLE)
            if isinstance(cycle, tuple):
                start, end = cycle
            else:
                booked = [txn.booked_date for txn in rows]
                start, end = min(booked), max(booked)
            coverages.append(Coverage(card, start, end, rows=rows, source=source))
        return coverages

    def _parse_pdf(self, file_path: Path) -> list[Transaction]:
        import fitz

        with fitz.open(file_path) as doc:
            pages = [str(page.get_text()) for page in doc]
        text = "\n".join(pages)

        closing = self._parse_pdf_closing(text)
        if closing is None:
            logger.warning("No SPDB statement cycle found")

        transactions: list[Transaction] = []
        for index, row in enumerate(self._extract_pdf_rows(text, file_path), start=1):
            transaction = self._parse_row(
                row, source_line=index, file_path=file_path, datemode=0
            )
            if transaction:
                transactions.append(transaction)

        if closing:
            self.stamp_closing_cycle(transactions, *closing)
        else:
            self.assign_statement_periods(transactions)
        return transactions

    @staticmethod
    def _parse_pdf_closing(text: str) -> tuple[date, int] | None:
        """Return the closing date and billing day of the statement month.

        The statement prints no cycle start; stamp_closing_cycle derives it.
        """
        month_match = _PDF_MONTH_RE.search(text)
        day_match = _PDF_BILLING_DAY_RE.search(text)
        if not month_match or not day_match:
            return None

        year, month = int(month_match.group(1)), int(month_match.group(2))
        billing_day = int(day_match.group(1))
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, min(billing_day, last_day)), billing_day

    @classmethod
    def _extract_pdf_rows(cls, text: str, file_path: Path) -> list[list[str]]:
        """Read the detail table into rows shaped like the XLS report's.

        The text runs one cell per line; a row is two dates, the description,
        the card suffix and the symbol-prefixed amount, then an original
        amount on templates that print it. Page footers and notices between
        rows match none of this and are passed over.
        """
        _, marker, details = text.partition(_PDF_DETAIL_MARKER)
        if not marker:
            return []
        lines = [
            stripped for line in details.splitlines() if (stripped := line.strip())
        ]

        rows: list[list[str]] = []
        i = 0
        while i + 1 < len(lines):
            if not (_PDF_DATE_RE.match(lines[i]) and _PDF_DATE_RE.match(lines[i + 1])):
                i += 1
                continue
            row = cls._read_pdf_row(lines, i)
            if row is None:
                logger.warning(
                    "%s: skipped an SPDB PDF row at text line %d",
                    file_path.name,
                    i + 1,
                )
                i += 1
                continue
            cells, i = row
            rows.append(cells)
        return rows

    @classmethod
    def _read_pdf_row(
        cls, lines: list[str], start: int
    ) -> tuple[list[str], int] | None:
        """Read the row starting at two date lines; return it and the next index.

        The description ends at the card suffix. A date line before that
        starts the next row, so this one has no card or amount and is rejected.
        """
        stop = min(len(lines) - 1, start + 3 + _PDF_MAX_DESCRIPTION_LINES)
        for card_at in range(start + 2, stop):
            if _PDF_DATE_RE.match(lines[card_at]):
                return None
            amount = _PDF_AMOUNT_RE.match(lines[card_at + 1])
            if card_at == start + 2 or not (
                amount and _PDF_CARD_RE.match(lines[card_at])
            ):
                continue
            end = card_at + 2
            original = "-"
            if end < len(lines) and _PDF_ORIGINAL_RE.match(lines[end]):
                original = lines[end]
                end += 1
            # The XLS column layout, so both formats share _parse_row; the PDF
            # prints no card type.
            cells = [
                lines[start],
                lines[start + 1],
                "".join(lines[start + 2 : card_at]),
                lines[card_at],
                "-",
                _PDF_CURRENCY_SYMBOLS[amount.group(1)],
                amount.group(2),
                original,
            ]
            return cells, end
        return None

    def _parse_xls(self, file_path: Path) -> list[Transaction]:
        try:
            workbook = xlrd.open_workbook(str(file_path))
            sheet = workbook.sheet_by_index(0)
        except (OSError, XLRDError):
            logger.warning("Unable to read SPDB statement")
            return []

        if sheet.ncols < self.EXPECTED_COLS:
            logger.warning(
                "SPDB statement: expected %d+ columns, found %d",
                self.EXPECTED_COLS,
                sheet.ncols,
            )
            return []

        header_row = self._find_header_row(sheet)
        if header_row is None:
            logger.warning("No SPDB transaction header found")
            return []

        transactions: list[Transaction] = []
        for row_idx in range(header_row + 1, sheet.nrows):
            row = [sheet.cell_value(row_idx, col) for col in range(sheet.ncols)]
            transaction = self._parse_row(
                row,
                source_line=row_idx + 1,
                file_path=file_path,
                datemode=workbook.datemode,
            )
            if transaction:
                transactions.append(transaction)

        # The export has no explicit query period, so use each card's own
        # transaction-date range as the narrowest safe approximation.
        self.assign_statement_periods(transactions)

        return transactions

    @classmethod
    def _find_header_row(cls, sheet: xlrd.sheet.Sheet) -> int | None:
        """Find the exact report header within the first 10 rows."""
        for row_idx in range(min(10, sheet.nrows)):
            headers = tuple(
                cls._normalize_cell_str(sheet.cell_value(row_idx, col))
                for col in range(cls.EXPECTED_COLS)
            )
            if headers == cls.EXPECTED_HEADERS:
                return row_idx
        return None

    def _parse_row(
        self,
        row: Sequence[object],
        source_line: int,
        file_path: Path,
        datemode: int,
    ) -> Transaction | None:
        """Parse one transaction row, skipping malformed rows with a warning."""
        if not any(self._normalize_cell_str(value) for value in row):
            return None

        try:
            transaction_date = self._parse_date(row[self.COL_TRANS_DATE], datemode)
            post_date = self._parse_date(row[self.COL_POST_DATE], datemode)
            description = self._normalize_cell_str(row[self.COL_DESCRIPTION])
            if not description:
                raise ValueError("empty description")

            amount = self._parse_amount(row[self.COL_AMOUNT])
            if amount is None:
                raise ValueError("invalid amount")

            currency = self._map_currency(
                self._normalize_cell_str(row[self.COL_CURRENCY])
            )
            if not currency:
                raise ValueError("empty currency")

            card_last4 = self._normalize_card_last4(row[self.COL_CARD_LAST4])
            metadata: dict[str, str | Decimal] = {}

            card_type = self._normalize_cell_str(row[self.COL_CARD_TYPE])
            if card_type and card_type != "-":
                metadata["card_type"] = card_type

            original_value = self._normalize_cell_str(row[self.COL_ORIGINAL_AMOUNT])
            original_amount, original_currency = self._parse_original_amount(
                original_value
            )
            if original_amount is not None:
                metadata[ORIGINAL_AMOUNT] = original_amount
            if original_currency:
                metadata[ORIGINAL_CURRENCY] = original_currency
            if (
                original_value
                and original_value != "-"
                and original_amount is None
                and not original_currency
            ):
                metadata["original_transaction"] = original_value

            return Transaction(
                date=transaction_date,
                post_date=post_date,
                amount=amount,
                currency=currency,
                description=description,
                card_last4=card_last4,
                provider=self.provider_id,
                source_file=file_path,
                source_line=source_line,
                metadata=metadata,
            )
        except (IndexError, ValueError):
            logger.warning("Failed to parse SPDB row %d", source_line)
            return None

    @staticmethod
    def _normalize_cell_str(value: object) -> str:
        """Convert an XLS cell to a trimmed string without a trailing .0."""
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value).strip()

    @classmethod
    def _normalize_card_last4(cls, value: object) -> str | None:
        """Normalize a card suffix, restoring leading zeroes for numeric cells."""
        card_last4 = cls._normalize_cell_str(value)
        if not card_last4 or not card_last4.isdigit() or len(card_last4) > 4:
            return None
        return card_last4.zfill(4)

    @classmethod
    def _parse_date(cls, value: object, datemode: int = 0) -> date:
        """Parse YYYYMMDD text/numbers or an Excel date cell."""
        if isinstance(value, float) and value < 10_000_000:
            year, month, day, _, _, _ = xlrd.xldate_as_tuple(
                value,
                datemode,  # type: ignore[arg-type]
            )
            return date(year, month, day)

        date_value = cls._normalize_cell_str(value)
        compact = date_value.replace("-", "").replace("/", "")
        if len(compact) != 8 or not compact.isdigit():
            raise ValueError("invalid date")
        return date(int(compact[:4]), int(compact[4:6]), int(compact[6:8]))

    @staticmethod
    def _parse_amount(value: object) -> Decimal | None:
        """Parse a signed transaction amount."""
        try:
            if isinstance(value, float):
                return Decimal(str(value))
            cleaned = str(value).replace(",", "").strip()
            return Decimal(cleaned)
        except (InvalidOperation, ValueError):
            return None

    @classmethod
    def _parse_original_amount(cls, value: str) -> tuple[Decimal | None, str | None]:
        """Parse an original amount formatted as ``amount(currency)``."""
        if not value or value == "-" or not value.endswith(")") or "(" not in value:
            return None, None

        amount_value, currency_value = value.rsplit("(", 1)
        amount = cls._parse_amount(amount_value)
        currency = cls._map_currency(currency_value[:-1].strip())
        if amount is None or not currency:
            return None, None
        return amount, currency

    @staticmethod
    def _map_currency(value: str) -> str:
        """Map statement currency labels to ISO currency codes."""
        return currency_code(value) or value.upper()
