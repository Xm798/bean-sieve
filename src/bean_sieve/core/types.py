"""Core data types for Bean-Sieve."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from beancount.core.data import TxnPosting
from pydantic import BaseModel, ConfigDict, Field, computed_field

if TYPE_CHECKING:
    from ..config import Config


class MatchSource(StrEnum):
    """Source of account matching."""

    RULE = "rule"
    FIXME = "fixme"


class Transaction(BaseModel):
    """Standardized transaction record from statement parsing."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=True,
    )

    # Required fields
    date: date
    amount: Decimal
    currency: str
    description: str

    # Optional time information
    time: dt.time | None = None
    post_date: date | None = None

    # Transaction identifiers
    payee: str | None = None
    card_last4: str | None = None
    order_id: str | None = None

    # Source information
    provider: str = ""
    source_file: Path | None = None
    source_line: int | None = None

    # Statement scope (for per-card statement providers)
    statement_period: tuple[date, date] | None = None

    # Price annotation for multi-currency transactions (e.g., currency exchange)
    price_amount: Decimal | None = None
    price_currency: str | None = None

    # Matching results (filled by rules engine)
    account: str | None = None
    contra_account: str | None = None
    confidence: float = 0.0
    match_source: MatchSource | None = None
    tags: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    flag: str = "*"

    # Extended metadata (for Beancount metadata)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @computed_field
    @property
    def match_key(self) -> tuple:
        """Key for deduplication matching (prefers order_id)."""
        if self.order_id:
            return (self.order_id,)
        return (self.date, abs(self.amount), self.card_last4)

    @computed_field
    @property
    def tx_datetime(self) -> dt.datetime | None:
        """Complete datetime if time is available."""
        if self.time:
            return dt.datetime.combine(self.date, self.time)
        return None

    @computed_field
    @property
    def is_expense(self) -> bool:
        """True if this is an expense (positive amount)."""
        return self.amount > 0

    @computed_field
    @property
    def is_income(self) -> bool:
        """True if this is income (negative amount)."""
        return self.amount < 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for JSON serialization / GUI communication."""
        return {
            "date": self.date.isoformat(),
            "time": self.time.isoformat() if self.time else None,
            "post_date": self.post_date.isoformat() if self.post_date else None,
            "amount": str(self.amount),
            "currency": self.currency,
            "description": self.description,
            "payee": self.payee,
            "card_last4": self.card_last4,
            "order_id": self.order_id,
            "provider": self.provider,
            "source_file": str(self.source_file) if self.source_file else None,
            "source_line": self.source_line,
            "statement_period": (
                [
                    self.statement_period[0].isoformat(),
                    self.statement_period[1].isoformat(),
                ]
                if self.statement_period
                else None
            ),
            "account": self.account,
            "contra_account": self.contra_account,
            "confidence": self.confidence,
            "match_source": self.match_source.value if self.match_source else None,
            "tags": self.tags,
            "flag": self.flag,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Transaction:
        """Create from dict for JSON deserialization / GUI communication."""
        match_source = None
        if data.get("match_source"):
            match_source = MatchSource(data["match_source"])

        return cls(
            date=date.fromisoformat(data["date"]),
            time=dt.time.fromisoformat(data["time"]) if data.get("time") else None,
            post_date=(
                date.fromisoformat(data["post_date"]) if data.get("post_date") else None
            ),
            amount=Decimal(data["amount"]),
            currency=data["currency"],
            description=data["description"],
            payee=data.get("payee"),
            card_last4=data.get("card_last4"),
            order_id=data.get("order_id"),
            provider=data.get("provider", ""),
            source_file=Path(data["source_file"]) if data.get("source_file") else None,
            source_line=data.get("source_line"),
            statement_period=(
                (
                    date.fromisoformat(data["statement_period"][0]),
                    date.fromisoformat(data["statement_period"][1]),
                )
                if data.get("statement_period")
                else None
            ),
            account=data.get("account"),
            contra_account=data.get("contra_account"),
            confidence=data.get("confidence", 0.0),
            match_source=match_source,
            tags=data.get("tags", []),
            flag=data.get("flag", "*"),
            metadata=data.get("metadata", {}),
        )


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


class MatchResult(BaseModel):
    """Reconciliation result from Sieve engine."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    matched: list[tuple[Transaction, TxnPosting]] = Field(default_factory=list)
    missing: list[Transaction] = Field(default_factory=list)
    extra: list[TxnPosting] = Field(default_factory=list)
    meta_diagnostics: list[MetaDiagnostic] = Field(default_factory=list)

    @computed_field
    @property
    def summary(self) -> str:
        """Human-readable summary."""
        return (
            f"Matched: {len(self.matched)}, "
            f"Missing: {len(self.missing)}, "
            f"Extra: {len(self.extra)}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for GUI communication."""
        return {
            "matched_count": len(self.matched),
            "missing": [t.to_dict() for t in self.missing],
            "extra_count": len(self.extra),
            "summary": self.summary,
        }


class ReconcileResult(BaseModel):
    """Full reconciliation result with processed transactions."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    match_result: MatchResult
    processed: list[Transaction] = Field(default_factory=list)

    @computed_field
    @property
    def summary(self) -> str:
        """Human-readable summary."""
        categorized = sum(
            1 for t in self.processed if t.match_source != MatchSource.FIXME
        )
        return (
            f"{self.match_result.summary}, "
            f"Categorized: {categorized}/{len(self.processed)}"
        )


@dataclass
class ReconcileContext:
    """
    Context passed to provider lifecycle hooks.

    Contains all information a provider might need during
    pre/post reconciliation processing.
    """

    statement_paths: list[Path]
    ledger_path: Path | None = None
    config: Config | None = None
    date_range: tuple[date, date] | None = None
    account_filter: str | None = None
    output_path: Path | None = None
    extra: dict[str, Any] = field(default_factory=dict)
