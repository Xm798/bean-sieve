"""Rules engine for account mapping."""

import contextlib
import re
from datetime import time

from ..config.schema import Config, Rule
from .types import MatchSource, Transaction


class RulesEngine:
    """
    Rules engine for matching transactions to accounts.

    Applies user-defined rules in priority order.
    """

    def __init__(self, config: Config):
        self.config = config
        # Sort rules by priority (higher first)
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

        Returns the transaction with account fields populated.
        """
        # First, try to get the asset/liability account from card mapping
        txn = self._apply_account_mapping(txn)

        # Then, try to match rules for contra account
        for i, rule in enumerate(self._rules):
            if self._matches_condition(txn, rule, i):
                txn = self._apply_action(txn, rule)
                break

        return txn

    def _apply_account_mapping(self, txn: Transaction) -> Transaction:
        """Apply account mapping based on payment method."""
        if txn.account:
            return txn

        method = txn.metadata.get("method", "")
        if not method:
            return txn

        for mapping in self.config.account_mappings:
            if mapping.pattern in method:
                txn.account = mapping.account
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
        if cond.card_suffix and txn.card_suffix != cond.card_suffix:
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

        if action.tags:
            txn.tags.extend(action.tags)

        if action.flag:
            txn.flag = action.flag

        return txn


def apply_rules(transactions: list[Transaction], config: Config) -> list[Transaction]:
    """
    Apply rules to a list of transactions.

    Convenience function for common usage.
    """
    engine = RulesEngine(config)
    result = []
    for txn in transactions:
        processed = engine.apply(txn)
        # Filter out ignored transactions
        if not processed.metadata.get("_ignored"):
            result.append(processed)
    return result
