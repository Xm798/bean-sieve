"""Core data types for Bean-Sieve."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from beancount.core.data import Directive, TxnPosting
from pydantic import BaseModel, ConfigDict, Field, InstanceOf, computed_field

from .coverage import CoverageSelection
from .metadata_keys import ORIGINAL_AMOUNT, ORIGINAL_CURRENCY

if TYPE_CHECKING:
    from ..config import Config


class MatchSource(StrEnum):
    RULE = "rule"
    FIXME = "fixme"


class Transaction(BaseModel):
    """One statement row; `amount` is positive for an outflow (expense) and
    negative for an inflow (income)."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=True,
    )

    date: date
    amount: Decimal
    currency: str
    description: str

    time: dt.time | None = None
    post_date: date | None = None

    payee: str | None = None
    card_last4: str | None = None
    order_id: str | None = None

    provider: str = ""
    source_file: Path | None = None
    source_line: int | None = None

    # The card's printed cycle joined with its booked dates; see
    # BaseProvider.assign_statement_periods.
    statement_period: tuple[date, date] | None = None

    # An exchange within the account: `amount` of `currency` buys
    # `price_amount` of `price_currency`.
    price_amount: Decimal | None = None
    price_currency: str | None = None

    # Unsigned charge taken out of the primary account alongside the transfer:
    # the contra side receives amount minus fee, and the fee posts separately.
    fee: Decimal | None = None

    # Filled by api target-account resolution and the rules engine.
    account: str | None = None
    contra_account: str | None = None
    confidence: float = 0.0
    match_source: MatchSource | None = None
    tags: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    flag: str = "*"

    # Keys starting with `_` are internal and never written (metadata_keys).
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def booked_date(self) -> date:
        """The statement's posting date, or the trade date when it prints none."""
        return self.post_date or self.date

    @computed_field
    @property
    def match_key(self) -> tuple:
        if self.order_id:
            return (self.order_id,)
        return (self.date, abs(self.amount), self.card_last4)

    @computed_field
    @property
    def tx_datetime(self) -> dt.datetime | None:
        if self.time:
            return dt.datetime.combine(self.date, self.time)
        return None

    @computed_field
    @property
    def is_expense(self) -> bool:
        return self.amount > 0

    @computed_field
    @property
    def is_income(self) -> bool:
        return self.amount < 0

    @property
    def original_amount(self) -> Decimal | None:
        """Unsigned amount in the original currency of a foreign transaction."""
        # Metadata restored by from_dict holds the JSON string, not a Decimal.
        value = self.metadata.get(ORIGINAL_AMOUNT)
        return None if value is None else abs(Decimal(value))

    @property
    def original_currency(self) -> str | None:
        return self.metadata.get(ORIGINAL_CURRENCY) or None

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready fields; metadata values such as Decimal become strings."""
        return self.model_dump(
            mode="json", exclude=set(type(self).model_computed_fields)
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Transaction:
        return cls.model_validate(data)


class MetaDiagnostic(BaseModel):
    """Posting metadata diagnostic emitted during reconciliation."""

    severity: Literal["hint", "warn"]  # hint=missing, warn=conflict
    file: str
    line: int
    account: str
    key: str
    expected: str
    actual: str | None = None
    message: str


class MatchDiagnostic(BaseModel):
    """A statement row without a target account matched a ledger posting
    while postings of other ledger transactions were equally valid
    candidates, so the greedy pick may be wrong."""

    file: str
    line: int
    chosen_account: str
    alternatives: int
    message: str


class MatchResult(BaseModel):
    """`missing` holds statement rows the ledger lacks; `extra` holds ledger
    postings in the covered scope that no row settles."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    matched: list[tuple[Transaction, TxnPosting]] = Field(default_factory=list)
    missing: list[Transaction] = Field(default_factory=list)
    extra: list[TxnPosting] = Field(default_factory=list)
    meta_diagnostics: list[MetaDiagnostic] = Field(default_factory=list)
    match_diagnostics: list[MatchDiagnostic] = Field(default_factory=list)

    @computed_field
    @property
    def summary(self) -> str:
        return (
            f"Matched: {len(self.matched)}, "
            f"Missing: {len(self.missing)}, "
            f"Extra: {len(self.extra)}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched_count": len(self.matched),
            "missing": [t.to_dict() for t in self.missing],
            "extra_count": len(self.extra),
            "summary": self.summary,
        }


class ReconcileResult(BaseModel):
    """`processed` holds the missing rows after rules, ready for output."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    match_result: MatchResult
    processed: list[Transaction] = Field(default_factory=list)
    # Which overlapping statement files were adopted; None when the run kept
    # every file (--keep-overlap). Checked by type only: a schema built from
    # the dataclass fields would need the Transaction forward reference there.
    coverage: InstanceOf[CoverageSelection] | None = None

    @computed_field
    @property
    def summary(self) -> str:
        categorized = sum(
            1 for t in self.processed if t.match_source != MatchSource.FIXME
        )
        return (
            f"{self.match_result.summary}, "
            f"Categorized: {categorized}/{len(self.processed)}"
        )


@dataclass
class ReconcileContext:
    """Context passed to provider lifecycle hooks."""

    statement_paths: list[Path]
    ledger_path: Path | None = None
    config: Config | None = None
    date_range: tuple[date, date] | None = None
    account_filter: str | None = None
    output_path: Path | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    # Per-path parse output, captured before date filtering, hooks and rules
    # run. Amounts stay at their parsed values (the pipeline only changes them
    # via model_copy); other fields are shared objects and may show later
    # in-place edits.
    parsed: dict[Path, list[Transaction]] = field(default_factory=dict)
    # Per-path id of the provider that parsed it: an explicitly named provider
    # owns files that detection would not recognise.
    provider_ids: dict[Path, str] = field(default_factory=dict)
    # The whole loaded ledger, unfiltered, so a hook can read balances
    # outside the reconciled window; empty until the ledger loads.
    ledger_directives: list[Directive] = field(default_factory=list)
