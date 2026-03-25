"""Preset rules for automatic account matching."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .types import Transaction


@dataclass
class PresetRuleCondition:
    """Condition for matching a preset rule."""

    # Regex pattern to match transaction description
    description: str | None = None

    # Regex pattern to match payee
    payee: str | None = None

    # Regex patterns to match metadata fields (e.g., {"method": r".*花呗.*"})
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class PresetRuleAction:
    """Action to take when a preset rule matches."""

    # Keyword to lookup in account_mappings to set txn.account
    account_keyword: str | None = None

    # Metadata key whose value is looked up in account_mappings to set contra_account
    # (for transfers where the destination varies per transaction)
    contra_account_metadata_key: str | None = None

    # Negate amount (flip sign) - use for transfers where direction is inverted
    negate: bool = False

    # Mark transaction as ignored
    ignore: bool = False


@dataclass
class PresetRule:
    """A preset rule defined in code."""

    # Unique rule ID for debugging/logging
    rule_id: str

    # Human-readable name
    name: str

    # Provider this rule applies to (None = all providers)
    provider: str | None = None

    # Match condition
    condition: PresetRuleCondition = field(default_factory=PresetRuleCondition)

    # Action to apply
    action: PresetRuleAction = field(default_factory=PresetRuleAction)

    # Priority within preset rules (higher = first)
    priority: int = 0

    # Compiled regex patterns (set during matching)
    _desc_pattern: re.Pattern | None = field(default=None, repr=False)
    _payee_pattern: re.Pattern | None = field(default=None, repr=False)
    _metadata_patterns: dict[str, re.Pattern] = field(default_factory=dict, repr=False)

    def compile_patterns(self) -> None:
        """Compile regex patterns for performance."""
        if self.condition.description:
            self._desc_pattern = re.compile(self.condition.description, re.IGNORECASE)
        if self.condition.payee:
            self._payee_pattern = re.compile(self.condition.payee, re.IGNORECASE)
        for key, pattern in self.condition.metadata.items():
            self._metadata_patterns[key] = re.compile(pattern, re.IGNORECASE)

    def matches(self, txn: Transaction) -> bool:
        """Check if transaction matches this rule's condition."""
        # Provider filter
        if self.provider and txn.provider != self.provider:
            return False

        # Description regex
        if self.condition.description:
            pattern = self._desc_pattern or re.compile(
                self.condition.description, re.IGNORECASE
            )
            if not pattern.search(txn.description):
                return False

        # Payee regex
        if self.condition.payee:
            if not txn.payee:
                return False
            pattern = self._payee_pattern or re.compile(
                self.condition.payee, re.IGNORECASE
            )
            if not pattern.search(txn.payee):
                return False

        # Metadata patterns
        for key, pattern_str in self.condition.metadata.items():
            value = txn.metadata.get(key, "")
            pattern = self._metadata_patterns.get(key) or re.compile(
                pattern_str, re.IGNORECASE
            )
            if not pattern.search(value):
                return False

        return True
