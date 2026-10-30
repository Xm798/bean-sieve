"""WeLab Bank (汇立银行) debit account statement provider."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ....core.types import Transaction
from ... import register_provider
from ...base import BaseProvider

logger = logging.getLogger(__name__)

MONTH_MAP = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}

# A currency section header, e.g.
# "核心账户 Core Account (1234567890) - 港元 HKD (包括智安存 Include Money Safe)"
SECTION_RE = re.compile(r"Core Account.*?-\s*(?:\S+\s+)?([A-Z]{3})\b")
# A transaction date in the Date column, e.g. "20 Apr 2026"
DATE_RE = re.compile(r"^\d{1,2}\s+[A-Z][a-z]{2}\s+\d{4}$")
# A signed money amount, e.g. "-12.34", "1,234.56"
AMOUNT_RE = re.compile(r"-?[\d,]+\.\d{2}")
# Transaction reference, e.g. "Ref: FX00000000"
REF_RE = re.compile(r"Ref:\s*([A-Z0-9]+)")
# Funding-exchange reference on a card spending, e.g. "FX Ref: FX00000000"
FX_REF_RE = re.compile(r"FX Ref:\s*([A-Z0-9]+)")
# Posting date on a card spending, e.g. "Transaction Date: 7 May 2026". Despite
# the label it is the later of the two dates: the Date column holds the trade.
POST_DATE_RE = re.compile(r"Transaction Date:\s*(\d{1,2}\s+[A-Z][a-z]{2}\s+\d{4})")
# Incoming-transfer description, e.g. "Receive money from <name>"
_TRANSFER_RE = re.compile(r"(?:Receive money from|Transfer to)\s+(.+)")
# Normalized description for cross-currency exchange legs (the raw cell holds
# only the rate/value-date detail, which is kept in metadata instead).
_EXCHANGE_DESC = "Foreign currency exchange"

# The statement is bilingual and its Chinese comes in Simplified or Traditional
# script, so rows are recognised by their English labels only.
_OPENING_LABEL = "Balance From Previous"
_CLOSING_LABEL = "Closing Balance"

# Table lines are drawn as strokes or as filled boxes thinner than this.
_RULE_MAX_HEIGHT = 1.5
# The rows above and below a border each draw it, a fraction of a point apart.
_EDGE_TOLERANCE = 1.0
# Row borders and shaded rows are drawn cell by cell; a line with fewer pieces
# is decoration such as an underline below a section title.
_MIN_CELLS = 3

_Word = tuple[float, float, float, float, str]


def _overlaps(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] < b[1] - _EDGE_TOLERANCE and b[0] < a[1] - _EDGE_TOLERANCE


@dataclass(frozen=True)
class _Grid:
    """Left edges of the Type, Description and Amount cells of transaction rows."""

    type_x: float
    desc_x: float
    amount_x: float

    def column(self, word: _Word) -> str:
        center = (word[0] + word[2]) / 2
        if center < self.type_x:
            return "date"
        if center < self.desc_x:
            return "type"
        if center < self.amount_x:
            return "desc"
        return "amt"


@dataclass
class _Section:
    opening: Decimal | None = None
    closing: Decimal | None = None
    rows: list[Transaction] = field(default_factory=list)


@register_provider
class WeLabProvider(BaseProvider):
    """
    Provider for WeLab Bank (汇立银行) consolidated monthly PDF statements.

    File format:
    - PDF with multiple currency sections (HKD, USD, CNY, ...)
    - Each section is a 4-column table: Date, Type, Transaction Description, Amount
    - Amount sign in the statement is bank-side: debit (outflow) is negative,
      credit (inflow) is positive. Negated to bean-sieve convention
      (expense positive, income negative).
    - Statement period in header: "Your Bank Statement (1 Apr 2026 - 30 Apr 2026)"
    - Each section opens with "Balance From Previous Statement" (absent for a
      currency first used in the period) and ends with "Closing Balance"; both
      are checked against the rows and carried into `balance` metadata.
    - A cross-currency exchange appears as two legs (a debit in the sell-side
      currency section, a credit in the buy-side section) that share the same
      "Ref: FX...". Both legs are kept as separate transactions so each matches
      its corresponding posting in the ledger (the natural ledger form is a
      same-account conversion: `Assets:WeLab -X HKD @@ Y CNY` + `Assets:WeLab Y
      CNY`, i.e. two postings on the WeLab account). Merging them into a single
      @@ entry would leave the counterpart ledger posting unmatched ("Extra").

    ``page.find_tables`` jams multi-line rows into a single cell, so rows are
    read from the drawn table instead: every row is outlined cell by cell, which
    gives both the row bands and the column edges. The column headers are no
    guide, as their text does not start at the cell edges.
    """

    provider_id = "welab_debit"
    provider_name = "汇立银行"
    supported_formats = [".pdf"]
    filename_keywords = ["welab", "汇立"]
    content_keywords = ["WeLab", "汇立"]

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse WeLab consolidated monthly PDF statement."""
        import fitz

        sections: dict[str, _Section] = defaultdict(_Section)
        row_counter = 0
        with fitz.open(file_path) as doc:
            statement_period = self._extract_statement_period(doc)
            # A currency section can span several pages; continuation pages
            # carry no section marker, so the currency carries across breaks.
            carried_currency: str | None = None
            for page_num in range(doc.page_count):
                page = doc[page_num]
                page_sections, carried_currency = self._page_sections(
                    page, carried_currency
                )
                for currency, grid, bands in page_sections:
                    section = sections[currency]
                    for band in bands:
                        text = " ".join(w[4] for w in band)
                        if _OPENING_LABEL in text:
                            section.opening = self._balance_amount(band)
                            continue
                        if _CLOSING_LABEL in text:
                            section.closing = self._balance_amount(band)
                            continue
                        if grid is None:
                            logger.warning(
                                "%s page %d: no table grid in the %s section; "
                                "row skipped: %s",
                                file_path.name,
                                page_num + 1,
                                currency,
                                self._date_text(band),
                            )
                            continue
                        txn = self._build_transaction(
                            currency,
                            self._cells(band, grid),
                            file_path,
                            row_counter,
                            statement_period,
                        )
                        if txn:
                            section.rows.append(txn)
                            row_counter += 1

        transactions: list[Transaction] = []
        for currency, section in sections.items():
            # Rows are listed by trade date; a card spending books on its
            # posting date, which may fall after a later-listed row.
            rows = sorted(section.rows, key=lambda t: t.booked_date)
            self._stamp_balances(file_path, currency, section, rows)
            transactions.extend(rows)
        return transactions

    # === Page extraction ===

    def _page_sections(
        self, page, carried_currency: str | None
    ) -> tuple[list[tuple[str, _Grid | None, list[list[_Word]]]], str | None]:
        """Split a page into currency sections and their dated row bands.

        ``carried_currency`` is the currency in effect at the top of the page.
        Returns ``(sections, trailing_currency)``, each section being
        ``(currency, grid, bands)`` where a band holds the words of one row.
        """
        markers: list[tuple[float, str]] = []
        for block in page.get_text("blocks"):
            match = SECTION_RE.search(str(block[4]).replace("\n", " "))
            if match:
                markers.append((float(block[1]), match.group(1)))
        if carried_currency is not None:
            markers.append((0.0, carried_currency))
        markers.sort()
        if not markers:
            return [], carried_currency

        words: list[_Word] = [
            (float(w[0]), float(w[1]), float(w[2]), float(w[3]), str(w[4]))
            for w in page.get_text("words")
        ]
        drawings: list[dict[str, Any]] = page.get_drawings()
        results: list[tuple[str, _Grid | None, list[list[_Word]]]] = []
        for idx, (top, currency) in enumerate(markers):
            bottom = markers[idx + 1][0] if idx + 1 < len(markers) else page.rect.y1
            rules, shaded = self._table_lines(drawings, top, bottom)
            edges = self._row_edges(rules, shaded)
            bands = [
                band
                for upper, lower in zip(edges, edges[1:], strict=False)
                if (band := self._band_words(words, upper, lower))
                and self._date_text(band)
            ]
            in_table = [w for band in bands for w in band]
            results.append((currency, self._grid(rules, shaded, in_table), bands))
        return results, markers[-1][1]

    @staticmethod
    def _table_lines(
        drawings: list[dict[str, Any]], top: float, bottom: float
    ) -> tuple[
        dict[float, set[tuple[float, float]]], dict[float, set[tuple[float, float]]]
    ]:
        """Collect row borders and shaded rows between ``top`` and ``bottom``.

        Both map a y position to the x spans of the cells drawn there; a shaded
        row is keyed by its top and reports its height in the span set's key.
        """
        rules: dict[float, set[tuple[float, float]]] = defaultdict(set)
        shaded: dict[float, set[tuple[float, float]]] = defaultdict(set)
        for drawing in drawings:
            rect = drawing["rect"]
            if not top <= rect.y0 < bottom or rect.width <= 1:
                continue
            span = (round(rect.x0, 1), round(rect.x1, 1))
            if rect.height < _RULE_MAX_HEIGHT:
                rules[round(rect.y0, 1)].add(span)
            elif drawing.get("fill") is not None:
                shaded[round(rect.y0, 1)].add(span)
                shaded[round(rect.y1, 1)].add(span)
        return (
            {y: s for y, s in rules.items() if len(s) >= _MIN_CELLS},
            {y: s for y, s in shaded.items() if len(s) >= _MIN_CELLS},
        )

    @staticmethod
    def _row_edges(
        rules: dict[float, set[tuple[float, float]]],
        shaded: dict[float, set[tuple[float, float]]],
    ) -> list[float]:
        edges: list[float] = []
        for y in sorted({*rules, *shaded}):
            if not edges or y - edges[-1] > _EDGE_TOLERANCE:
                edges.append(y)
        return edges

    @staticmethod
    def _band_words(words: list[_Word], upper: float, lower: float) -> list[_Word]:
        return sorted(
            (w for w in words if upper < (w[1] + w[3]) / 2 < lower),
            key=lambda w: (round(w[1]), w[0]),
        )

    @staticmethod
    def _grid(
        rules: dict[float, set[tuple[float, float]]],
        shaded: dict[float, set[tuple[float, float]]],
        words: list[_Word],
    ) -> _Grid | None:
        """Find the transaction cell edges from the drawn row borders.

        A balance row is shaded and has cells of its own, some shared with the
        transaction rows; where the two meet, both draw their cells on the
        shared border. A balance cell is left out when it overlaps a cell only
        transaction rows draw. The Amount, Description and Type cells are then
        the three rightmost spans holding text.
        """
        drawn = {span for spans in rules.values() for span in spans}
        balance_spans = {span for spans in shaded.values() for span in spans}
        own = drawn - balance_spans
        spans = sorted(
            own
            | {
                span
                for span in drawn & balance_spans
                if not any(_overlaps(span, other) for other in own)
            }
        )
        holding_text = [
            (x0, x1)
            for x0, x1 in spans
            if any(x0 <= (w[0] + w[2]) / 2 < x1 for w in words)
        ]
        if len(holding_text) < 3 or any(
            prev[1] > nxt[0] + _EDGE_TOLERANCE
            for prev, nxt in zip(holding_text, holding_text[1:], strict=False)
        ):
            return None
        (type_x, _), (desc_x, _), (amount_x, _) = holding_text[-3:]
        return _Grid(type_x, desc_x, amount_x)

    @staticmethod
    def _date_text(band: list[_Word]) -> str | None:
        """Return the row's date: the leftmost words on the leftmost word's line."""
        first = min(band, key=lambda w: w[0])
        line = sorted((w for w in band if abs(w[1] - first[1]) < 1), key=lambda w: w[0])
        text = " ".join(w[4] for w in line[:3])
        return text if DATE_RE.match(text) else None

    @staticmethod
    def _cells(band: list[_Word], grid: _Grid) -> dict[str, str]:
        cells: dict[str, list[str]] = defaultdict(list)
        for word in band:
            cells[grid.column(word)].append(word[4])
        return {column: " ".join(texts) for column, texts in cells.items()}

    @staticmethod
    def _balance_amount(band: list[_Word]) -> Decimal:
        """Read a balance row's figure; the statement prints "-" for nil."""
        last = max(band, key=lambda w: w[2])[4]
        match = AMOUNT_RE.fullmatch(last)
        return Decimal(match.group(0).replace(",", "")) if match else Decimal(0)

    # === Balances ===

    @staticmethod
    def _stamp_balances(
        file_path: Path, currency: str, section: _Section, rows: list[Transaction]
    ) -> None:
        """Check the rows against the section's balances, then stamp each row's.

        A row's balance follows posting order, so the last row carries the
        closing balance. A section whose rows do not add up gets no balances:
        a row was misread or skipped.
        """
        if section.closing is None:
            if rows:
                logger.warning(
                    "%s: %s section has no closing balance; balances not checked",
                    file_path.name,
                    currency,
                )
            return
        opening = section.opening or Decimal(0)
        # Row amounts follow bean-sieve's sign; the account moves the other way.
        moved = -sum((t.amount for t in rows), Decimal(0))
        if opening + moved != section.closing:
            logger.warning(
                "%s: %s rows move the balance by %s, but the statement goes from "
                "%s to %s (off by %s); balances not stamped",
                file_path.name,
                currency,
                moved,
                opening,
                section.closing,
                section.closing - opening - moved,
            )
            return
        balance = opening
        for txn in rows:
            balance -= txn.amount
            txn.metadata["balance"] = str(balance)

    # === Row -> Transaction ===

    def _build_transaction(
        self,
        currency: str,
        cells: dict[str, str],
        file_path: Path,
        row_idx: int,
        statement_period: tuple[date, date] | None,
    ) -> Transaction | None:
        """Build a Transaction from extracted row cells."""
        date_str = cells.get("date", "")
        type_str = cells.get("type", "")
        desc_str = cells.get("desc", "")
        tx_date = self._parse_date(date_str)
        amount = self._parse_amount(cells.get("amt", ""))
        if tx_date is None or amount is None:
            logger.warning(
                "%s: unreadable row skipped (date %r, amount %r)",
                file_path.name,
                date_str,
                cells.get("amt", ""),
            )
            return None

        # order_id is the transaction's own "Ref:" (the first one). The detail is
        # everything before it (the merchant / rate); the tail of richer rows
        # ("Transaction Date: ...", "FX Ref: ...") is captured separately so it
        # doesn't clutter the description.
        ref = REF_RE.search(desc_str)
        order_id = ref.group(1) if ref else None
        detail = desc_str.split("Ref:", 1)[0].strip()
        type_en = self._english_only(type_str)
        is_exchange = "Foreign currency exchange" in type_en

        metadata: dict[str, str] = {}
        if type_en:
            metadata["transaction_type"] = type_en
        fx_ref = FX_REF_RE.search(desc_str)
        if fx_ref:
            metadata["fx_ref"] = fx_ref.group(1)
        post_date_match = POST_DATE_RE.search(desc_str)
        post_date = (
            self._parse_date(post_date_match.group(1)) if post_date_match else None
        )

        if is_exchange:
            # The raw detail is just the rate / value-date; use a stable
            # description and keep the detail in metadata.
            description = _EXCHANGE_DESC
            if detail:
                metadata["exchange_info"] = detail
            payee = None
        else:
            description = detail or type_en or type_str
            payee = self._extract_payee(detail)
            # For incoming transfers the payee name is also the counterparty.
            if payee and _TRANSFER_RE.search(detail):
                metadata["counterparty"] = payee

        # Tag refunds with #refund and link the order id via ^<order_id> so the
        # refund can be associated with its order in the ledger.
        tags: list[str] = []
        links: list[str] = []
        if "Refund" in type_en:
            tags.append("refund")
            if order_id:
                links.append(order_id)

        return Transaction(
            date=tx_date,
            post_date=post_date,
            amount=amount,
            currency=currency,
            description=description,
            payee=payee,
            order_id=order_id,
            card_last4=currency,  # used as account mapping key (per-currency account)
            provider=self.provider_id,
            source_file=file_path,
            source_line=row_idx + 1,
            statement_period=statement_period,
            tags=tags,
            links=links,
            metadata=metadata,
        )

    @staticmethod
    def _parse_date(date_str: str) -> date | None:
        """Parse date from 'dd Mon yyyy' format (e.g., '20 Apr 2026')."""
        match = re.match(r"(\d{1,2})\s+(\w{3})\s+(\d{4})", date_str)
        if not match:
            return None
        month = MONTH_MAP.get(match.group(2))
        if month is None:
            return None
        try:
            return date(int(match.group(3)), month, int(match.group(1)))
        except ValueError:
            return None

    @staticmethod
    def _parse_amount(amount_str: str) -> Decimal | None:
        """Parse amount, negating to bean-sieve sign convention.

        Statement shows debit (outflow) as negative and credit (inflow) as
        positive. bean-sieve uses expense=positive, income=negative, so negate.
        """
        match = AMOUNT_RE.search(amount_str)
        if not match:
            return None
        try:
            return -Decimal(match.group(0).replace(",", ""))
        except (InvalidOperation, ValueError):
            return None

    @staticmethod
    def _english_only(type_str: str) -> str:
        """Return the English label, which follows the Chinese one.

        Text before the last Chinese character belongs to the Chinese label,
        including a currency prefix ("HKD 存款利息 HKD Deposit interest").
        """
        english = re.split(r"[一-鿿（）]", type_str)[-1]
        return re.sub(r"\s+", " ", english).lstrip(" )").strip()

    @staticmethod
    def _extract_payee(detail: str) -> str | None:
        """Extract a merchant/payee name from the description detail."""
        if not detail:
            return None
        # "Receive money from <name>" -> "<name>"
        recv = _TRANSFER_RE.search(detail)
        if recv:
            return recv.group(1).strip()
        # Card spending merchants: "WEIXIN*<merchant> CHN Online 5999"
        if re.match(r"(?:WEIXIN|Alipay)\*", detail) or " CHN " in detail:
            merchant = re.split(r"\s+CHN\b", detail)[0].strip()
            return merchant or None
        return None

    # === Statement period & detection ===

    def _extract_statement_period(self, doc) -> tuple[date, date] | None:
        """Extract statement period from PDF header."""
        if doc.page_count < 1:
            return None
        text = doc[0].get_text()[:600]
        match = re.search(
            r"(\d{1,2}\s+\w{3}\s+\d{4})\s*-\s*(\d{1,2}\s+\w{3}\s+\d{4})", text
        )
        if match:
            start = self._parse_date(match.group(1))
            end = self._parse_date(match.group(2))
            if start and end:
                return (start, end)
        return None

    @classmethod
    def _match_content(cls, file_path: Path) -> bool:
        """Check if PDF content matches WeLab.

        The bank name ("汇立"/"welab.bank") only appears in the notes on a later,
        variable page. The first page always carries WeLab-specific product names
        ("Global Wallet Core Account", "智安存 Money Safe"), so detect on those.
        The time-deposit label is no marker: "GoSave 2.0" became "Time Deposit"
        from the September 2026 statement on.
        """
        try:
            text = cls._read_pdf_first_page(file_path)
        except Exception:
            return False
        if any(kw in text for kw in ("WeLab", "汇立", "welab.bank")):
            return True
        return "Global Wallet Core Account" in text and "Money Safe" in text
