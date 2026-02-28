"""Rules engine for account mapping."""

from __future__ import annotations

import contextlib
import re
from datetime import time

from ..config.schema import Config, Rule
from .preset_rules import PresetRule
from .types import MatchSource, Transaction


class RulesEngine:
    """
    Rules engine for matching transactions to accounts.

    Applies preset rules first, then user-defined rules in priority order.
    """

    def __init__(
        self,
        config: Config,
        preset_rules: list[PresetRule] | None = None,
    ):
        self.config = config

        # Preset rules (sorted by priority, higher first)
        self._preset_rules = sorted(
            preset_rules or [],
            key=lambda r: r.priority,
            reverse=True,
        )
        # Compile preset rule patterns
        for rule in self._preset_rules:
            rule.compile_patterns()

        # User-defined rules (sorted by priority, higher first)
        self._rules = sorted(config.rules, key=lambda r: r.priority, reverse=True)
        # Compile regex patterns for performance
        self._desc_patterns: dict[int, re.Pattern] = {}
        self._payee_patterns: dict[int, re.Pattern] = {}
        for i, rule in enumerate(self._rules):
            if rule.condition.description:
                with contextlib.suppress(re.error):
                    self._desc_patterns[i] = re.compile(
                        rule.condition.description, re.IGNORECASE
                    )
            if rule.condition.payee:
                with contextlib.suppress(re.error):
                    self._payee_patterns[i] = re.compile(
                        rule.condition.payee, re.IGNORECASE
                    )

    def apply(self, txn: Transaction) -> Transaction:
        """
        Apply rules to a transaction to fill account mapping.

        Order of application:
        1. Preset rules (code-defined, keyword-based account lookup) - highest priority
        2. Account mappings (based on payment method) - fallback
        3. User-defined rules (YAML config)

        Returns the transaction with account fields populated.
        """
        # 1. Apply preset rules first (keyword-based account lookup)
        txn = self._apply_preset_rules(txn)

        # 2. Fallback to account mapping if preset rules didn't set account
        if not txn.account:
            txn = self._apply_account_mapping(txn)

        # 3. Apply user-defined rules for contra account
        for i, rule in enumerate(self._rules):
            if self._matches_condition(txn, rule, i):
                txn = self._apply_action(txn, rule)
                break

        return txn

    def _apply_preset_rules(self, txn: Transaction) -> Transaction:
        """Apply preset rules to set account from keyword lookup."""
        for preset in self._preset_rules:
            if preset.matches(txn):
                txn = self._apply_preset_action(txn, preset)
                break  # First match wins
        return txn

    def _apply_preset_action(self, txn: Transaction, preset: PresetRule) -> Transaction:
        """Apply preset rule action to transaction."""
        action = preset.action

        if action.ignore:
            txn.metadata["_ignored"] = True
            return txn

        # Keyword-based account lookup
        if action.account_keyword:
            account = self._lookup_account_by_keyword(action.account_keyword)
            if account:
                txn.account = account
                txn.metadata["matched_preset_rule"] = preset.rule_id

        # Negate amount if specified (only if positive, to avoid double-negation)
        if action.negate and txn.amount > 0:
            txn.amount = -txn.amount

        return txn

    def _lookup_account_by_keyword(self, keyword: str) -> str | None:
        """Lookup account from account_mappings by keyword."""
        keyword_lower = keyword.lower()
        for mapping in self.config.account_mappings:
            if keyword_lower in mapping.pattern.lower():
                return mapping.account
        return None

    def _apply_account_mapping(self, txn: Transaction) -> Transaction:
        """Apply account mapping based on payment method."""
        if txn.account:
            return txn

        method = txn.metadata.get("method", "")
        if not method:
            return txn

        for mapping in self.config.account_mappings:
            if mapping.pattern.lower() in method.lower():
                txn.account = mapping.account
                # Set rebate account if configured and transaction has rebate
                if mapping.rebate_account and txn.metadata.get("rebate"):
                    txn.metadata["_rebate_account"] = mapping.rebate_account
                return txn

        return txn

    def _matches_condition(self, txn: Transaction, rule: Rule, rule_idx: int) -> bool:
        """Check if transaction matches rule condition."""
        cond = rule.condition

        # Description regex match
        if cond.description:
            pattern = self._desc_patterns.get(rule_idx)
            if pattern:
                if not pattern.search(txn.description):
                    return False
            else:
                # Fallback to simple substring match
                if cond.description.lower() not in txn.description.lower():
                    return False

        # Payee regex match
        if cond.payee:
            if not txn.payee:
                return False
            pattern = self._payee_patterns.get(rule_idx)
            if pattern:
                if not pattern.search(txn.payee):
                    return False
            else:
                # Fallback to simple substring match
                if cond.payee.lower() not in txn.payee.lower():
                    return False

        # Card suffix match
        if cond.card_last4 and txn.card_last4 != cond.card_last4:
            return False

        # Provider match
        if cond.provider and txn.provider != cond.provider:
            return False

        # Time range match
        if (
            cond.time_range
            and txn.time
            and not self._matches_time_range(txn.time, cond.time_range)
        ):
            return False

        # Direction match
        if cond.direction is not None:
            if cond.direction == "expense" and not txn.is_expense:
                return False
            if cond.direction == "income" and not txn.is_income:
                return False

        # Amount range match
        abs_amount = abs(float(txn.amount))
        if cond.min_amount is not None and abs_amount < cond.min_amount:
            return False
        return not (cond.max_amount is not None and abs_amount > cond.max_amount)

    def _matches_time_range(self, txn_time: time, time_range: str) -> bool:
        """Check if transaction time falls within range."""
        try:
            start_str, end_str = time_range.split("-")
            start = time.fromisoformat(start_str.strip())
            end = time.fromisoformat(end_str.strip())

            if start <= end:
                return start <= txn_time <= end
            else:
                # Handle overnight range (e.g., 22:00-06:00)
                return txn_time >= start or txn_time <= end
        except (ValueError, AttributeError):
            return False

    def _apply_action(self, txn: Transaction, rule: Rule) -> Transaction:
        """Apply rule action to transaction."""
        action = rule.action

        if action.ignore:
            # Mark as ignored (will be filtered out later)
            txn.metadata["_ignored"] = True
            return txn

        if action.contra_account:
            txn.contra_account = action.contra_account
            txn.confidence = 1.0
            txn.match_source = MatchSource.RULE
            # Store which rule matched for debugging
            if rule.condition.description:
                txn.metadata["matched_rule"] = rule.condition.description

        if action.payee:
            # Store original payee before overriding
            if txn.payee and txn.payee != action.payee:
                txn.metadata["original_payee"] = txn.payee
            txn.payee = action.payee

        if action.description:
            # Store original description before overriding
            if txn.description and txn.description != action.description:
                txn.metadata["original_description"] = txn.description
            txn.description = action.description

        if action.tags:
            txn.tags.extend(action.tags)

        if action.flag:
            txn.flag = action.flag

        return txn


def apply_rules(
    transactions: list[Transaction],
    config: Config,
    preset_rules: list[PresetRule] | None = None,
) -> list[Transaction]:
    """
    Apply rules to a list of transactions.

    Convenience function for common usage.

    Args:
        transactions: List of transactions to process
        config: Configuration with user-defined rules and account mappings
        preset_rules: Optional list of preset rules from provider

    Returns:
        List of processed transactions (with ignored ones filtered out)
    """
    engine = RulesEngine(config, preset_rules=preset_rules)
    result = []
    for txn in transactions:
        processed = engine.apply(txn)
        # Filter out ignored transactions
        if not processed.metadata.get("_ignored"):
            result.append(processed)
    return result
