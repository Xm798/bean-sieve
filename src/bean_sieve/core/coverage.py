"""Choose one statement per account and period when statement files overlap.

A bank delivers the same period through several sources (the mailed
statement, a reissue, an online-banking export). Rows from two of them are
duplicates no per-row key can pair safely: same-day same-amount purchases are
real, and formats disagree on which date and currency a row carries. So the
choice is made per file: within one provider and account, overlapping sources
compete and only one is adopted for the overlap.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .types import Transaction


@dataclass(eq=False)
class Coverage:
    """
    The span one statement file reports for one account.

    Attributes:
        account: The provider's own key for the account (e.g. card suffix);
            it only has to tell accounts of the same provider apart.
        start, end: The period the file speaks for, inclusive.
        rows: The file's transactions on this account.
        itemized: False for a summary-only file, which never displaces a file
            that lists the transactions.
        source: Provider-defined source kind, ranked by the provider's
            ``coverage_priority``.
        clip: True for a source with arbitrary bounds, such as an export:
            losing trims its rows by post date to outside the adopted periods
            instead of dropping the whole file.
    """

    account: str
    start: date
    end: date
    rows: list[Transaction] = field(default_factory=list)
    itemized: bool = True
    source: str = "statement"
    clip: bool = False


@dataclass(eq=False)
class CoverageEntry:
    """A coverage in the run, with the precedence its provider gives its source."""

    provider_id: str
    path: Path
    coverage: Coverage
    rank: int = 0


@dataclass(eq=False)
class CoverageDrop:
    """Rows of an entry discarded because adopted entries cover them."""

    entry: CoverageEntry
    winners: list[CoverageEntry]
    rows: list[Transaction]


@dataclass(eq=False)
class CoverageMismatch:
    """Two itemized sources disagree on the transactions of their overlap."""

    winner: CoverageEntry
    loser: CoverageEntry
    start: date
    end: date
    winner_count: int
    winner_total: Decimal
    loser_count: int
    loser_total: Decimal


@dataclass
class CoverageSelection:
    """The outcome of select(): adopted entries, what lost, and disagreements."""

    adopted: list[CoverageEntry] = field(default_factory=list)
    drops: list[CoverageDrop] = field(default_factory=list)
    mismatches: list[CoverageMismatch] = field(default_factory=list)

    def keep(self, transactions: list[Transaction]) -> list[Transaction]:
        """Filter out the discarded rows.

        Rows are told apart by identity, so this takes the objects that were
        selected on, not model_copy results of them.
        """
        dropped = {id(row) for drop in self.drops for row in drop.rows}
        return [t for t in transactions if id(t) not in dropped]


def _booked(row: Transaction) -> date:
    return row.post_date or row.date


def _overlaps(a: Coverage, b: Coverage) -> bool:
    return a.start <= b.end and b.start <= a.end


def _within(row: Transaction, start: date, end: date) -> bool:
    return start <= _booked(row) <= end


def _tally(entry: CoverageEntry, start: date, end: date) -> tuple[int, Decimal]:
    rows = [r for r in entry.coverage.rows if _within(r, start, end)]
    return len(rows), sum((r.amount for r in rows), Decimal(0))


def _compare(winner: CoverageEntry, loser: CoverageEntry) -> CoverageMismatch | None:
    start = max(winner.coverage.start, loser.coverage.start)
    end = min(winner.coverage.end, loser.coverage.end)
    won, lost = _tally(winner, start, end), _tally(loser, start, end)
    if won == lost:
        return None
    return CoverageMismatch(winner, loser, start, end, *won, *lost)


def _covered_rows(
    entry: CoverageEntry, winners: list[CoverageEntry]
) -> list[Transaction]:
    """The rows of a losing entry that its winners account for."""
    if not entry.coverage.clip:
        return list(entry.coverage.rows)
    return [
        r
        for r in entry.coverage.rows
        if any(_within(r, w.coverage.start, w.coverage.end) for w in winners)
    ]


def select(entries: Sequence[CoverageEntry]) -> CoverageSelection:
    """
    Adopt one source per provider, account and overlapping period.

    Entries compete by rank, ties going to the earlier entry. An entry
    overlapping one already adopted loses as a whole, unless it clips, in
    which case it keeps the rows booked outside every adopted period.

    Whole-period entries compete first and clipping ones only fill the gaps
    they leave: a clipping source can give up part of its span, a statement
    only all of it, so letting an export win would discard statement days the
    export never covered.
    """
    groups: dict[tuple[str, str], list[CoverageEntry]] = defaultdict(list)
    for entry in entries:
        if entry.coverage.itemized:
            groups[(entry.provider_id, entry.coverage.account)].append(entry)

    selection = CoverageSelection()
    adopted_all: set[CoverageEntry] = set()
    for group in groups.values():
        adopted: list[CoverageEntry] = []
        for entry in sorted(group, key=lambda e: (e.coverage.clip, e.rank)):
            winners = [a for a in adopted if _overlaps(a.coverage, entry.coverage)]
            if not winners:
                adopted.append(entry)
                continue
            selection.drops.append(
                CoverageDrop(entry, winners, _covered_rows(entry, winners))
            )
            # A clipping entry keeps its uncovered rows, so it still stands for
            # its span against the entries ranked after it.
            if entry.coverage.clip:
                adopted.append(entry)
            selection.mismatches.extend(
                m for w in winners if (m := _compare(w, entry)) is not None
            )
        adopted_all.update(adopted)

    selection.adopted = [e for e in entries if e in adopted_all]
    return selection
