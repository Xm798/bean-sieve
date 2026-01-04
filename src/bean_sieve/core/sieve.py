"""Sieve engine for matching and deduplication."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from beancount import loader
from beancount.core.data import Directive, TxnPosting
from beancount.core.data import Transaction as BeanTransaction

from .types import MatchResult, Transaction


@dataclass
class SieveConfig:
    """Configuration for the Sieve engine."""

    date_tolerance: int = 2  # days
    amount_tolerance: Decimal = Decimal("0.01")


class Sieve:
    """
    Matching engine for reconciling statement transactions with ledger entries.

    Implements fuzzy matching with configurable tolerances.
    """

    def __init__(self, config: SieveConfig | None = None):
        self.config = config or SieveConfig()
        self._ledger_entries: list[TxnPosting] = []
        self._ledger_index: dict[tuple, list[TxnPosting]] = {}

    def load_ledger(
        self,
        path: Path,
        account_filter: str | None = None,
        date_range: tuple[date, date] | None = None,
    ) -> None:
        """
        Load Beancount ledger and build index for matching.

        Args:
            path: Path to main beancount file or directory
            account_filter: Only include transactions with this account prefix
            date_range: Only include transactions within this range
        """
        if path.is_dir():
            # Find main.bean or *.bean files
            main_file = path / "main.bean"
            if not main_file.exists():
                bean_files = list(path.glob("*.bean"))
                if not bean_files:
                    raise ValueError(f"No .bean files found in {path}")
                main_file = bean_files[0]
            path = main_file

        entries, errors, options = loader.load_file(str(path))

        if errors:
            # Log errors but continue
            for error in errors[:5]:  # limit error output
                print(f"Warning: {error}")

        self._process_entries(entries, account_filter, date_range)

    def _process_entries(
        self,
        entries: list[Directive],
        account_filter: str | None,
        date_range: tuple[date, date] | None,
    ) -> None:
        """Process ledger entries and build matching index."""
        self._ledger_entries = []
        self._ledger_index = {}

        for entry in entries:
            if not isinstance(entry, BeanTransaction):
                continue

            # Date filter
            if date_range and (
                entry.date < date_range[0] or entry.date > date_range[1]
            ):
                continue

            # Process postings
            for posting in entry.postings:
                if account_filter and not posting.account.startswith(account_filter):
                    continue

                txn_posting = TxnPosting(entry, posting)
                self._ledger_entries.append(txn_posting)

                # Build index for fast lookup
                # Index by (date, abs_amount) for fuzzy matching
                if posting.units:
                    key = (entry.date, abs(posting.units.number))
                    if key not in self._ledger_index:
                        self._ledger_index[key] = []
                    self._ledger_index[key].append(txn_posting)

    def match(self, transactions: Iterable[Transaction]) -> MatchResult:
        """
        Match statement transactions against loaded ledger entries.

        Returns:
            MatchResult with matched pairs, missing, and extra transactions
        """
        transactions = list(transactions)
        matched: list[tuple[Transaction, TxnPosting]] = []
        missing: list[Transaction] = []
        used_ledger_entries: set[int] = set()

        for txn in transactions:
            match = self._find_match(txn, used_ledger_entries)
            if match:
                matched.append((txn, match))
                used_ledger_entries.add(id(match))
            else:
                missing.append(txn)

        # Find extra ledger entries (not matched to any statement transaction)
        extra = [
            entry
            for entry in self._ledger_entries
            if id(entry) not in used_ledger_entries
        ]

        return MatchResult(matched=matched, missing=missing, extra=extra)

    def _find_match(self, txn: Transaction, used: set[int]) -> TxnPosting | None:
        """Find a matching ledger entry for the given transaction."""
        # First try exact order_id match if available
        if txn.order_id:
            for entry in self._ledger_entries:
                if id(entry) in used:
                    continue
                if self._match_by_order_id(txn, entry):
                    return entry

        # Try fuzzy matching by date and amount
        candidates = self._get_candidates(txn)
        for candidate in candidates:
            if id(candidate) in used:
                continue
            if self._is_match(txn, candidate):
                return candidate

        return None

    def _get_candidates(self, txn: Transaction) -> list[TxnPosting]:
        """Get candidate ledger entries for matching."""
        candidates = []
        abs_amount = abs(txn.amount)

        # Check dates within tolerance
        for delta in range(self.config.date_tolerance + 1):
            for sign in [0, 1, -1]:
                if delta == 0 and sign != 0:
                    continue
                check_date = txn.date + timedelta(days=delta * sign if sign else 0)
                key = (check_date, abs_amount)
                candidates.extend(self._ledger_index.get(key, []))

        return candidates

    def _match_by_order_id(self, txn: Transaction, entry: TxnPosting) -> bool:
        """Check if order_id matches in entry metadata."""
        if not txn.order_id:
            return False

        meta = entry.txn.meta
        return meta.get("order_id") == txn.order_id

    def _is_match(self, txn: Transaction, entry: TxnPosting) -> bool:
        """Check if transaction matches ledger entry."""
        posting = entry.posting
        bean_txn = entry.txn

        # Amount must match (with tolerance)
        if posting.units:
            amount_diff = abs(abs(txn.amount) - abs(posting.units.number))
            if amount_diff > self.config.amount_tolerance:
                return False

        # Date must be within tolerance
        date_diff = abs((txn.date - bean_txn.date).days)
        if date_diff > self.config.date_tolerance:
            return False

        # Card suffix must match if present in both
        if txn.card_suffix:
            meta_card = bean_txn.meta.get("card_suffix") or bean_txn.meta.get("card")
            if meta_card and meta_card != txn.card_suffix:
                return False

        return True


def create_sieve(
    ledger_path: Path,
    account_filter: str | None = None,
    date_range: tuple[date, date] | None = None,
    date_tolerance: int = 2,
) -> Sieve:
    """
    Create and initialize a Sieve engine.

    Convenience function for common usage.
    """
    config = SieveConfig(date_tolerance=date_tolerance)
    sieve = Sieve(config)
    sieve.load_ledger(ledger_path, account_filter, date_range)
    return sieve
