"""WeLab Bank (汇立银行) debit account statement provider."""

from __future__ import annotations

import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

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
SECTION_RE = re.compile(r"Core Account.*?-\s*\S+\s+([A-Z]{3})\b")
# A transaction date in the Date column, e.g. "20 Apr 2026"
DATE_RE = re.compile(r"^\d{1,2}\s+[A-Z][a-z]{2}\s+\d{4}$")
# A signed money amount, e.g. "-12.34", "1,234.56"
AMOUNT_RE = re.compile(r"-?[\d,]+\.\d{2}")
# Transaction reference, e.g. "Ref: FX00000000"
REF_RE = re.compile(r"Ref:\s*([A-Z0-9]+)")
# Funding-exchange reference on a card spending, e.g. "FX Ref: FX00000000"
FX_REF_RE = re.compile(r"FX Ref:\s*([A-Z0-9]+)")
# Actual transaction date on a card spending, e.g. "Transaction Date: 7 May 2026"
TXN_DATE_RE = re.compile(r"Transaction Date:\s*(\d{1,2}\s+[A-Z][a-z]{2}\s+\d{4})")
# Incoming-transfer description, e.g. "Receive money from <name>"
_TRANSFER_RE = re.compile(r"(?:Receive money from|Transfer to)\s+(.+)")
# Normalized description for cross-currency exchange legs (the raw cell holds
# only the rate/value-date detail, which is kept in metadata instead).
_EXCHANGE_DESC = "Foreign currency exchange"

# Column boundaries (word x0 in PDF points), measured from the 595pt-wide page:
# Date | Type (种类) | Transaction Description (交易详情) | Amount (金额, right-aligned).
# The Date column ends ~140, Type ~245, Description ~480; Amount is right-aligned
# near the page edge. A word is assigned to a column by its x0 (see `_column`).
COL_DATE_MAX = 140.0
COL_TYPE_MAX = 245.0
COL_DESC_MAX = 480.0


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
    - "承上结余 Balance From Previous Statement" / "帐户结余 Closing Balance"
      rows are skipped.
    - A cross-currency exchange appears as two legs (a debit in the sell-side
      currency section, a credit in the buy-side section) that share the same
      "Ref: FX...". Both legs are kept as separate transactions so each matches
      its corresponding posting in the ledger (the natural ledger form is a
      same-account conversion: `Assets:WeLab -X HKD @@ Y CNY` + `Assets:WeLab Y
      CNY`, i.e. two postings on the WeLab account). Merging them into a single
      @@ entry would leave the counterpart ledger posting unmatched ("Extra").

    The table cells cannot be reliably recovered with ``page.find_tables`` because
    multi-line rows get jammed into a single cell, so transactions are reconstructed
    from word coordinates: words are bucketed into columns by x position and into
    rows by clustering around each Date-column anchor.
    """

    provider_id = "welab_debit"
    provider_name = "汇立银行"
    supported_formats = [".pdf"]
    filename_keywords = ["welab", "汇立"]
    content_keywords = ["WeLab", "汇立"]

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse WeLab consolidated monthly PDF statement."""
        import fitz

        doc = fitz.open(file_path)
        statement_period = self._extract_statement_period(doc)
        transactions: list[Transaction] = []
        row_counter = 0
        # A currency section can span several pages; continuation pages carry no
        # section marker, so remember the currency in effect across page breaks.
        carried_currency: str | None = None

        for page_num in range(doc.page_count):
            page = doc[page_num]
            page_results, carried_currency = self._extract_section_rows(
                page, carried_currency
            )
            for currency, rows in page_results:
                for tx_date_str, type_str, amount_str, desc_str in rows:
                    txn = self._build_transaction(
                        currency,
                        tx_date_str,
                        type_str,
                        amount_str,
                        desc_str,
                        file_path,
                        row_counter,
                        statement_period,
                    )
                    if txn:
                        transactions.append(txn)
                        row_counter += 1

        doc.close()
        return transactions

    # === Page extraction ===

    def _extract_section_rows(
        self, page, carried_currency: str | None
    ) -> tuple[list[tuple[str, list[tuple[str, str, str, str]]]], str | None]:
        """Extract per-currency transaction rows from a page.

        ``carried_currency`` is the currency in effect at the top of the page,
        carried over from a section that began on an earlier page (continuation
        pages have no section marker of their own).

        Returns ``(results, trailing_currency)`` where results is a list of
        (currency, rows), each row is (date_str, type_str, amount_str, desc_str),
        and trailing_currency is the currency still in effect at the page bottom.
        """
        blocks: list = page.get_text("blocks")  # type: ignore[assignment]
        sections: list[tuple[float, str]] = []
        header_bottoms: list[tuple[float, float]] = []
        footer_y = float("inf")

        for block in blocks:
            text = str(block[4])
            flat = text.replace("\n", " ")
            match = SECTION_RE.search(flat)
            if match:
                sections.append((float(block[1]), match.group(1)))
            if "Transaction Description" in text and "交易详情" in text:
                header_bottoms.append((float(block[1]), float(block[3])))
            if "Page" in flat and " of" in flat and float(block[1]) > 600:
                footer_y = min(footer_y, float(block[1]))

        # Prepend the carried-over currency so rows above the first marker (or
        # the whole page, on a continuation page) are attributed correctly.
        if carried_currency is not None:
            sections.append((0.0, carried_currency))
        sections.sort()
        if not sections:
            return [], carried_currency
        trailing_currency = sections[-1][1]

        words: list = page.get_text("words")  # type: ignore[assignment]
        results: list[tuple[str, list[tuple[str, str, str, str]]]] = []

        for idx, (sec_top, currency) in enumerate(sections):
            sec_bottom = sections[idx + 1][0] if idx + 1 < len(sections) else footer_y
            # Section content starts below the column header, if present.
            inner_headers = [
                hb for hy, hb in header_bottoms if sec_top < hy < sec_bottom
            ]
            top = max(inner_headers) if inner_headers else sec_top
            bottom = min(sec_bottom, footer_y)

            section_words = [w for w in words if top < w[1] < bottom]
            anchors = self._find_date_anchors(section_words)
            rows: list[tuple[str, str, str, str]] = []

            for i, (anchor_y, date_str) in enumerate(anchors):
                lo = (anchors[i - 1][0] + anchor_y) / 2 if i > 0 else top
                hi = (
                    (anchor_y + anchors[i + 1][0]) / 2
                    if i + 1 < len(anchors)
                    else bottom
                )
                cells: dict[str, list[str]] = {"type": [], "desc": [], "amt": []}
                band = sorted(
                    (w for w in section_words if lo <= w[1] < hi),
                    key=lambda w: (round(w[1]), w[0]),
                )
                for w in band:
                    column = self._column(float(w[0]))
                    if column != "date":
                        cells[column].append(str(w[4]))
                rows.append(
                    (
                        date_str,
                        " ".join(cells["type"]),
                        " ".join(cells["amt"]),
                        " ".join(cells["desc"]),
                    )
                )

            if rows:
                results.append((currency, rows))

        return results, trailing_currency

    @staticmethod
    def _column(x0: float) -> str:
        """Map a word's x position to its table column."""
        if x0 < COL_DATE_MAX:
            return "date"
        if x0 < COL_TYPE_MAX:
            return "type"
        if x0 < COL_DESC_MAX:
            return "desc"
        return "amt"

    @classmethod
    def _find_date_anchors(cls, section_words: list) -> list[tuple[float, str]]:
        """Find Date-column rows that hold a transaction date, as (y, text)."""
        date_lines: dict[int, list[tuple[float, str]]] = {}
        for w in section_words:
            if cls._column(float(w[0])) == "date":
                date_lines.setdefault(round(w[1]), []).append((float(w[0]), str(w[4])))

        anchors: list[tuple[float, str]] = []
        for y, toks in sorted(date_lines.items()):
            text = " ".join(t for _, t in sorted(toks))
            if DATE_RE.match(text):
                anchors.append((float(y), text))
        return anchors

    # === Row -> Transaction ===

    def _build_transaction(
        self,
        currency: str,
        date_str: str,
        type_str: str,
        amount_str: str,
        desc_str: str,
        file_path: Path,
        row_idx: int,
        statement_period: tuple[date, date] | None,
    ) -> Transaction | None:
        """Build a Transaction from extracted row cells."""
        if "Closing Balance" in type_str or "帐户结余" in type_str:
            return None
        if "Balance From Previous" in desc_str or "承上结余" in type_str:
            return None

        tx_date = self._parse_date(date_str)
        if tx_date is None:
            return None

        amount = self._parse_amount(amount_str)
        if amount is None:
            return None

        # order_id is the transaction's own "Ref:" (the first one). The detail is
        # everything before it (the merchant / rate); the tail of richer rows
        # ("Transaction Date: ...", "FX Ref: ...") is captured into metadata so it
        # doesn't clutter the description.
        ref = REF_RE.search(desc_str)
        order_id = ref.group(1) if ref else None
        detail = desc_str.split("Ref:", 1)[0].strip()
        type_en = self._english_only(type_str)
        is_exchange = "外币兑换" in type_str or "Foreign currency exchange" in type_str

        metadata: dict[str, str] = {}
        if type_en:
            metadata["transaction_type"] = type_en
        fx_ref = FX_REF_RE.search(desc_str)
        if fx_ref:
            metadata["fx_ref"] = fx_ref.group(1)
        txn_date = TXN_DATE_RE.search(desc_str)
        if txn_date:
            metadata["transaction_date"] = txn_date.group(1)

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
        if "退款" in type_str or "Refund" in type_str:
            tags.append("refund")
            if order_id:
                links.append(order_id)

        return Transaction(
            date=tx_date,
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
        """Strip Chinese characters/brackets, leaving the English type label."""
        cleaned = re.sub(r"[一-鿿（）()]", " ", type_str)
        return re.sub(r"\s+", " ", cleaned).strip()

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
        """Check if PDF content matches WeLab (binary, so read via fitz).

        The bank name ("汇立"/"welab.bank") only appears in the notes on a later,
        variable page. The first page always carries WeLab-specific product names
        ("Global Wallet Core Account", "GoSave", "智安存 Money Safe"), so detect on
        those.
        """
        try:
            import fitz

            doc = fitz.open(file_path)
            try:
                text = doc[0].get_text() if doc.page_count else ""
            finally:
                doc.close()
        except Exception:
            return False
        if any(kw in text for kw in ("WeLab", "汇立", "welab.bank")):
            return True
        return "Global Wallet Core Account" in text and "GoSave" in text
