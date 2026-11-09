"""Preset rules for automatic account matching."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .types import Transaction


@dataclass
class PresetRuleCondition:
    """Case-insensitive regexes, all of which must find a match.

    A set ``payee`` fails a row without a payee; a ``metadata`` key the row
    lacks is searched as the empty string.
    """

    description: str | None = None
    payee: str | None = None
    # e.g. {"method": r".*花呗.*"}
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class PresetRuleAction:
    # Looked up in account_mappings to set txn.account.
    account_keyword: str | None = None

    # Metadata key whose value is looked up in account_mappings to set
    # contra_account, for transfers whose destination varies per row.
    contra_account_metadata_key: str | None = None

    # For transfers whose statement sign is inverted.
    negate: bool = False

    ignore: bool = False


@dataclass
class PresetRule:
    """A rule a provider defines in code, applied before the user's rules."""

    # Recorded as `matched_preset_rule` metadata on the rows it sets.
    rule_id: str

    name: str

    # None applies to every provider.
    provider: str | None = None

    condition: PresetRuleCondition = field(default_factory=PresetRuleCondition)

    action: PresetRuleAction = field(default_factory=PresetRuleAction)

    # Higher is tried first among preset rules.
    priority: int = 0

    # Filled by compile_patterns(); matches() compiles on the fly without it.
    _desc_pattern: re.Pattern | None = field(default=None, repr=False)
    _payee_pattern: re.Pattern | None = field(default=None, repr=False)
    _metadata_patterns: dict[str, re.Pattern] = field(default_factory=dict, repr=False)

    def compile_patterns(self) -> None:
        if self.condition.description:
            self._desc_pattern = re.compile(self.condition.description, re.IGNORECASE)
        if self.condition.payee:
            self._payee_pattern = re.compile(self.condition.payee, re.IGNORECASE)
        for key, pattern in self.condition.metadata.items():
            self._metadata_patterns[key] = re.compile(pattern, re.IGNORECASE)

    def matches(self, txn: Transaction) -> bool:
        if self.provider and txn.provider != self.provider:
            return False

        if self.condition.description:
            pattern = self._desc_pattern or re.compile(
                self.condition.description, re.IGNORECASE
            )
            if not pattern.search(txn.description):
                return False

        if self.condition.payee:
            if not txn.payee:
                return False
            pattern = self._payee_pattern or re.compile(
                self.condition.payee, re.IGNORECASE
            )
            if not pattern.search(txn.payee):
                return False

        for key, pattern_str in self.condition.metadata.items():
            value = txn.metadata.get(key, "")
            pattern = self._metadata_patterns.get(key) or re.compile(
                pattern_str, re.IGNORECASE
            )
            if not pattern.search(value):
                return False

        return True
