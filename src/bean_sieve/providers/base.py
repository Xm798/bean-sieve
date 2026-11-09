"""Base class for statement providers."""

from __future__ import annotations

import base64
import codecs
import email
import quopri
import re
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from email.header import decode_header
from email.message import Message
from pathlib import Path
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup

from ..config import Config
from ..core.coverage import Coverage
from ..core.metadata_keys import STATEMENT_CYCLE
from ..core.types import ReconcileContext, ReconcileResult, Transaction
from ._pdf import first_page_text
from ._tabular import with_bom_encoding

if TYPE_CHECKING:
    from ..core.preset_rules import PresetRule


_META_CHARSET_RE = re.compile(rb"<meta[^>]*charset=[\"']?([\w-]+)", re.IGNORECASE)
_GB_CHARSETS = ("gb2312", "gbk")


def _decode_body(part: Message, body: bytes) -> str:
    """Decode a mail body by the charset its MIME header or HTML <meta> declares.

    Some banks leave the MIME charset empty and name GBK only in a <meta> tag,
    or declare gb2312 while using characters outside it; GB18030 decodes every
    member of that family.
    """
    charset = part.get_content_charset()
    if not charset and (meta := _META_CHARSET_RE.search(body)):
        charset = meta.group(1).decode("ascii").lower()
    if charset in _GB_CHARSETS:
        charset = "gb18030"
    try:
        codecs.lookup(charset or "utf-8")
    except LookupError:
        charset = None
    return body.decode(charset or "utf-8", errors="replace")


@dataclass(eq=False)
class ClosingStatement:
    """A statement printing only its closing date, as stamp_closing_cycle saw it.

    ``cycle`` follows the latest restamp, so it reads as final once the run
    has parsed every file.
    """

    closing: date
    billing_day: int
    cards: frozenset[str]
    rows: list[Transaction]
    cycle: tuple[date, date] | None = None


class BaseProvider(ABC):
    """
    Base class for statement parsers.

    Subclasses set provider_id, provider_name and supported_formats (lowercase
    extensions with the dot) and implement parse().
    """

    provider_id: str
    provider_name: str
    supported_formats: list[str]

    def __init__(self) -> None:
        # Statements of this run passed to stamp_closing_cycle; an instance
        # belongs to one run, as resolve_providers creates it.
        self._closing_statements: list[ClosingStatement] = []

    @abstractmethod
    def parse(self, file_path: Path) -> list[Transaction]:
        """Return the file's rows in booking order, oldest first."""
        pass

    # File detection rules; see can_handle().
    filename_pattern: re.Pattern | None = None  # e.g., re.compile(r"平安.*借记")
    filename_keywords: list[str] = []  # e.g., ["微信", "wechat"]
    content_keywords: list[str] = []  # e.g., ["微信支付账单明细"]

    # Statement scope: True if the bank sends separate statements per card.
    # It decides how far coverage reaches past the rows the config can place: a
    # row whose card is not a config key is dropped (True) or covers the whole
    # statement (False), and a configured account the file never names is off
    # this statement (True) or on it, covered by the file's periods (False).
    per_card_statement: bool = False

    # True when a row without a card_last4 belongs to the account every
    # configured key maps to (sole_account()): the file names no account or
    # card, or the bank prints none on account-level rows. A payment platform
    # keys its config on the wallet and leaves this False.
    unkeyed_rows_share_account: bool = False

    # True when a file printing no billing cycle still lists one billed cycle,
    # so every row posts inside it. False keeps the trade-date span: a
    # trade-dated history may list rows not yet posted.
    uncycled_export_is_statement: bool = False

    # Source kinds of coverage() in descending precedence; an unlisted kind
    # ranks below every listed one.
    coverage_priority: tuple[str, ...] = ()

    # Content keywords are searched in this many leading characters of a
    # text file (after decoding), so they must sit near the top.
    CONTENT_HEADER_CHARS = 500

    @classmethod
    def can_handle(cls, file_path: Path) -> bool:
        """
        Accept a file with a supported extension that matches the filename rule
        or, when that misses, the content rule.

        A provider with no filename or content rule (keywords, pattern or an
        overridden ``_match_content``) accepts every file with its extension.
        """
        if file_path.suffix.lower() not in cls.supported_formats:
            return False

        if (
            cls.filename_pattern
            or cls.filename_keywords
            or cls.content_keywords
            or cls._match_content.__func__ is not BaseProvider._match_content.__func__
        ):
            return cls._match_filename(file_path) or cls._match_content(file_path)

        return True

    @classmethod
    def _match_filename(cls, file_path: Path) -> bool:
        # A filename_pattern replaces filename_keywords.
        if cls.filename_pattern:
            return bool(cls.filename_pattern.search(file_path.name))
        if not cls.filename_keywords:
            return False
        filename_lower = file_path.name.lower()
        return any(kw.lower() in filename_lower for kw in cls.filename_keywords)

    @classmethod
    def _match_content(cls, file_path: Path) -> bool:
        if not cls.content_keywords:
            return False
        try:
            suffix = file_path.suffix.lower()
            if suffix == ".pdf":
                content = cls._read_pdf_first_page(file_path)
            else:
                content = cls._read_file_header(file_path, cls.CONTENT_HEADER_CHARS)
            if suffix == ".eml":
                content += cls._read_eml_text(file_path)
            return any(kw in content for kw in cls.content_keywords)
        except Exception:
            return False

    @classmethod
    def _read_pdf_first_page(cls, file_path: Path) -> str:
        """Return the text of a PDF's first page; PDF bytes are compressed."""
        return first_page_text(file_path)

    @classmethod
    def _read_eml_text(cls, file_path: Path) -> str:
        """Return an EML's decoded subject and body.

        Mail clients encode the subject (RFC 2047) and body (base64 or
        quoted-printable), so keywords only appear once these are decoded.
        """
        with open(file_path, "rb") as f:
            msg = email.message_from_binary_file(f)
        provider = cls()
        return provider.decode_subject(msg) + provider._extract_html_from_message(msg)

    @classmethod
    def _read_file_header(cls, file_path: Path, size: int) -> str:
        encodings = ("utf-8", "gbk", "gb2312", "utf-16")
        for encoding in with_bom_encoding(file_path, encodings):
            try:
                with open(file_path, encoding=encoding) as f:
                    return f.read(size)
            except (UnicodeDecodeError, UnicodeError):
                continue
        return ""

    def own_statement_paths(self, context: ReconcileContext) -> list[Path]:
        """Statement paths this run assigned to this provider, in run order."""
        return [
            path
            for path in context.statement_paths
            if context.provider_ids.get(path) == self.provider_id
        ]

    def pre_reconcile(
        self,
        transactions: list[Transaction],
        context: ReconcileContext,  # noqa: ARG002
    ) -> list[Transaction]:
        """
        Transform this provider's rows before they are matched against the ledger.

        A provider named explicitly receives every row of the run.
        """
        return transactions

    def post_output(
        self,
        content: str,
        result: ReconcileResult,  # noqa: ARG002
        context: ReconcileContext,  # noqa: ARG002
    ) -> str:
        """
        Return ``content``, the whole output so far, with this provider's
        additions such as settlement entries or reconciliation summaries.

        An entry appended for a statement item the ledger lacks also goes into
        ``result.match_result.missing``, and an Extra posting the hook recognises
        as such an item moves to ``matched``; the CLI counts both after every hook.
        """
        return content

    def coverage(
        self,
        file_path: Path,  # noqa: ARG002
        transactions: list[Transaction],  # noqa: ARG002
    ) -> list[Coverage]:
        """
        Return the account periods this file reports, one per account.

        Files of the same provider whose coverages overlap compete, and only
        one is adopted for the overlap (see core/coverage.py). A provider
        returning none keeps every row of every file.
        """
        return []

    @staticmethod
    def cycle_coverages(
        transactions: list[Transaction],
        *,
        source: str = "statement",
        clip: bool = False,
        uncycled_span: bool = False,
    ) -> list[Coverage]:
        """Cover each card for the cycle stamped on its rows by stamp_cycle().

        statement_period also spans any row booked outside the cycle, which
        would make consecutive statements overlap, so this reads the cycle as
        printed. A card whose rows carry no stamp covers nothing, or with
        ``uncycled_span`` its rows' booked-date span.
        """
        by_card: dict[str, list[Transaction]] = {}
        for txn in transactions:
            if uncycled_span or STATEMENT_CYCLE in txn.metadata:
                by_card.setdefault(txn.card_last4 or "", []).append(txn)
        return [
            Coverage(
                card,
                *_cycle_or_span(rows),
                rows=rows,
                source=source,
                clip=clip,
            )
            for card, rows in by_card.items()
        ]

    @classmethod
    def stamp_cycle(
        cls, transactions: list[Transaction], cycle: tuple[date, date] | None
    ) -> None:
        """Assign statement periods and record the printed cycle on every row."""
        cls.assign_statement_periods(transactions, cycle)
        if cycle:
            for txn in transactions:
                txn.metadata[STATEMENT_CYCLE] = cycle

    def stamp_closing_cycle(
        self,
        transactions: list[Transaction],
        closing: date,
        billing_day: int | None = None,
        cards: Iterable[str] = (),
    ) -> ClosingStatement:
        """Stamp the cycle of a statement that prints only its closing date.

        The cycle starts the day after the previous statement of the run
        closed. Every statement this instance parsed is a candidate, or on a
        per-card provider only those sharing a card with this one: its rows'
        cards and ``cards``, which a quiet statement prints without rows. The
        previous statement is the first of:

        - the candidate closing on the billing day a month earlier;
        - the latest candidate closing from the first of the previous month
          on, for a bank that moved its billing day;
        - neither: month_cycle().

        Each call restamps the statements this instance parsed before, so once
        the run has parsed all its files every cycle holds, whatever the
        order. ``billing_day`` defaults to the closing day, which a short
        month clamps.
        """
        record = ClosingStatement(
            closing,
            billing_day or closing.day,
            frozenset(cards) | {t.card_last4 for t in transactions if t.card_last4},
            transactions,
        )
        statements = self._closing_statements
        statements.append(record)
        for statement in statements:
            closings = {
                other.closing
                for other in statements
                if not self.per_card_statement or statement.cards & other.cards
            }
            start, end = self.month_cycle(statement.closing, statement.billing_day)
            previous = start - timedelta(days=1)
            if previous not in closings:
                floor = previous.replace(day=1)
                earlier = [c for c in closings if floor <= c < end]
                start = max(earlier, default=previous) + timedelta(days=1)
            statement.cycle = (start, end)
            self.stamp_cycle(statement.rows, statement.cycle)
        return record

    @staticmethod
    def month_cycle(closing: date, billing_day: int | None = None) -> tuple[date, date]:
        """Return the cycle from the billing day a month earlier to ``closing``.

        A billing day past the end of the previous month falls on its last day.
        """
        previous = closing.replace(day=1) - timedelta(days=1)
        day = billing_day or closing.day
        return previous.replace(day=min(day, previous.day)) + timedelta(days=1), closing

    @classmethod
    def assign_statement_periods(
        cls,
        transactions: list[Transaction],
        cycle: tuple[date, date] | None = None,
    ) -> None:
        """
        Stamp each card's rows with the period its statement speaks for.

        With a printed cycle the period is the cycle joined with the card's
        booked dates: an installment or a delayed settlement is traded before
        the cycle that books it, and the ledger around its trade date belongs
        to the statement covering that date. The booked dates still extend a
        cycle a provider approximates, such as a calendar month.

        Without a cycle the period is the card's span of booked dates when
        ``uncycled_export_is_statement`` holds, else of trade dates. Each card
        gets its own, since a file-wide span would cover another card's tail.
        """
        by_card: dict[str | None, list[Transaction]] = defaultdict(list)
        for txn in transactions:
            by_card[txn.card_last4].append(txn)

        by_booking = cycle is not None or cls.uncycled_export_is_statement
        for card_txns in by_card.values():
            days = [txn.booked_date if by_booking else txn.date for txn in card_txns]
            if cycle:
                days.extend(cycle)
            period = (min(days), max(days))
            for txn in card_txns:
                txn.statement_period = period

    def sole_account(self, config: Config) -> str | None:
        """
        Return the account of every unkeyed row, when the config has one.

        Several keys may map to it, as the cards of one household account do;
        with several accounts there is nothing to choose by.
        """
        if not self.unkeyed_rows_share_account:
            return None
        accounts = set(config.get_provider_config(self.provider_id).accounts.values())
        if len(accounts) != 1:
            return None
        return accounts.pop()

    def get_covered_accounts(
        self,
        transactions: list[Transaction],
        config: Config,
        coverages: Sequence[Coverage] = (),
    ) -> list[str]:
        """
        Return the accounts whose ledger entries can be reported as Extra.

        These are the accounts get_covered_ranges attributes a period to.
        Without any period the statement says nothing about dates, so a
        statement-wide provider falls back to all configured accounts while a
        per-card one claims none.
        """
        # Sieve leaves a covered account without a range unfiltered by date, so
        # the two answers have to agree on which accounts are covered.
        covered_ranges = self.get_covered_ranges(transactions, config, coverages)
        if covered_ranges is not None:
            return list(covered_ranges)
        if self.per_card_statement:
            return []
        return list(config.get_provider_config(self.provider_id).accounts.values())

    def get_covered_ranges(
        self,
        transactions: list[Transaction],
        config: Config,
        coverages: Sequence[Coverage] = (),
    ) -> dict[str, list[tuple[date, date]]] | None:
        """
        Return per account the date ranges inside which a ledger entry is
        reported as Extra, or None to apply no date filter.

        Each period lands on the account of the row's card. What the file says
        beyond that is left to per_card_statement: a statement-wide file (a
        household statement, or a multi-currency one keyed on currency) speaks
        for the whole set of accounts, so a row it cannot place covers every
        configured account, and an account the file never names is covered by
        the union of the file's periods, its ledger entries there really being
        Extra. A per-card file claims neither.

        A coverage stands for its period the way a row of its account key
        stands for the row's, so a statement without rows still covers its
        account.
        """
        card_to_account = config.get_provider_config(self.provider_id).accounts
        sole_account = self.sole_account(config)

        # Every statement period stays a range of its own: merging them into one
        # span would cover the gap between two statements, where the ledger has
        # entries no statement accounts for.
        periods_by_account: dict[str, list[tuple[date, date]]] = {}
        all_periods: list[tuple[date, date]] = []
        attributed: set[str] = set()

        def cover(account: str, period: tuple[date, date]) -> None:
            ranges = periods_by_account.setdefault(account, [])
            if period not in ranges:
                ranges.append(period)

        spans = [(t.card_last4, t.statement_period) for t in transactions]
        spans.extend((c.account, (c.start, c.end)) for c in coverages)
        for key, period in spans:
            if not period:
                continue

            # Payment platforms key their config on the wallet while card_last4
            # holds a bank card suffix, so a row whose card is not a key says
            # nothing about which account it belongs to.
            account = card_to_account.get(key) if key else sole_account
            if account is not None:
                attributed.add(account)
                cover(account, period)

            if self.per_card_statement:
                continue
            if period not in all_periods:
                all_periods.append(period)
            if account is None:
                for configured in card_to_account.values():
                    cover(configured, period)

        for account in card_to_account.values():
            if account not in attributed:
                for period in all_periods:
                    cover(account, period)

        return periods_by_account or None

    @classmethod
    def get_preset_rules(cls) -> list[PresetRule]:
        """Rules that identify this provider's transaction types and their accounts."""
        return []

    def extract_html_from_eml(self, file_path: Path) -> str:
        with open(file_path, "rb") as f:
            msg = email.message_from_binary_file(f)

        return self._extract_html_from_message(msg)

    def _extract_html_from_message(self, msg: Message) -> str:
        """Decode a multipart mail's first text/html part, else its first text/plain.

        A single-part mail decodes its only body.
        """
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/html":
                    return self._decode_payload(part)
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain":
                    return self._decode_payload(part)
        else:
            return self._decode_payload(msg)

        return ""

    def _decode_payload(self, part: Message) -> str:
        """Undo the transfer encoding, then decode the charset as _decode_body does.

        A body that fails to decode is returned in its transfer-encoded form.
        """
        payload = part.get_payload(decode=False)
        encoding = part.get("Content-Transfer-Encoding", "").lower()

        if isinstance(payload, bytes):
            return _decode_body(part, payload)

        if not isinstance(payload, str):
            return ""

        if encoding == "base64":
            try:
                return _decode_body(part, base64.b64decode(payload))
            except Exception:
                return payload

        if encoding == "quoted-printable":
            try:
                return _decode_body(part, quopri.decodestring(payload.encode()))
            except Exception:
                return payload

        # The str form of a 7bit/8bit body is already decoded as ASCII, losing
        # its non-ASCII bytes; the decoded form returns them as received.
        body = part.get_payload(decode=True)
        return _decode_body(part, body) if isinstance(body, bytes) else payload

    def decode_subject(self, msg: Message) -> str:
        """Decode the RFC 2047 encoded words of the Subject header."""
        subject = msg.get("Subject", "")
        if not subject:
            return ""

        decoded_parts = decode_header(subject)
        result = []
        for part, encoding in decoded_parts:
            if isinstance(part, bytes):
                result.append(part.decode(encoding or "utf-8", errors="replace"))
            else:
                result.append(part)
        return "".join(result)

    def parse_html(self, html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "html.parser")

    def clean_text(self, text: str) -> str:
        if not text:
            return ""
        return " ".join(text.split())


def _cycle_or_span(rows: list[Transaction]) -> tuple[date, date]:
    if STATEMENT_CYCLE in rows[0].metadata:
        return rows[0].metadata[STATEMENT_CYCLE]
    booked = [txn.booked_date for txn in rows]
    return min(booked), max(booked)
