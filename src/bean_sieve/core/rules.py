"""Rules engine for account mapping."""

from __future__ import annotations

import contextlib
import re
from datetime import time

from ..config.schema import Config, Rule
from .accounts import (
    apply_rebate_account,
    resolve_by_bank_and_suffix,
    resolve_by_keyword_substring,
    resolve_by_method_ci,
)
from .metadata_keys import (
    IGNORED,
    MATCHED_PRESET_RULE,
    MATCHED_RULE,
    ORIGINAL_DESCRIPTION,
    ORIGINAL_PAYEE,
)
from .preset_rules import PresetRule
from .types import MatchSource, Transaction


class RulesEngine:
    def __init__(
        self,
        config: Config,
        preset_rules: list[PresetRule] | None = None,
    ):
        self.config = config

        self._preset_rules = sorted(
            preset_rules or [],
            key=lambda r: r.priority,
            reverse=True,
        )
        for rule in self._preset_rules:
            rule.compile_patterns()

        self._rules = sorted(config.rules, key=lambda r: r.priority, reverse=True)
        # A condition that is not a valid regex has no entry here and matches
        # as a case-insensitive substring in _matches_condition.
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
        Fill the row's accounts in three steps:

        1. The first matching preset rule.
        2. When no account is set yet, the account mapping of its method.
        3. The first matching user rule.
        """
        txn = self._apply_preset_rules(txn)

        if not txn.account:
            txn = self._apply_account_mapping(txn)

        for i, rule in enumerate(self._rules):
            if self._matches_condition(txn, rule, i):
                txn = self._apply_action(txn, rule)
                break

        return txn

    def _apply_preset_rules(self, txn: Transaction) -> Transaction:
        for preset in self._preset_rules:
            if preset.matches(txn):
                txn = self._apply_preset_action(txn, preset)
                break
        return txn

    def _apply_preset_action(self, txn: Transaction, preset: PresetRule) -> Transaction:
        action = preset.action

        if action.ignore:
            txn.metadata[IGNORED] = True
            return txn

        if action.account_keyword:
            account = resolve_by_keyword_substring(
                self.config.account_mappings, action.account_keyword
            )
            if account:
                txn.account = account
                txn.metadata[MATCHED_PRESET_RULE] = preset.rule_id

        if action.contra_account_metadata_key and not txn.contra_account:
            value = txn.metadata.get(action.contra_account_metadata_key, "")
            if value:
                # The value is first read as a full method string (e.g.
                # "交通银行(8888)"). WeChat spells a 交易类型 destination as
                # `bank(XXXX)` while users pattern the 支付方式 spelling such as
                # `bank储蓄卡(XXXX)`, so bank name plus suffix is the fallback.
                mapping = resolve_by_method_ci(
                    self.config.account_mappings, value
                ) or resolve_by_bank_and_suffix(self.config.account_mappings, value)
                if mapping:
                    txn.contra_account = mapping.account
                    txn.match_source = MatchSource.RULE
                    txn.metadata[MATCHED_PRESET_RULE] = preset.rule_id

        # Idempotent: api._apply_negate_rules has already applied it to rows
        # that went through matching.
        if action.negate:
            txn.amount = -abs(txn.amount)

        return txn

    def _apply_account_mapping(self, txn: Transaction) -> Transaction:
        if txn.account:
            return txn

        method = txn.metadata.get("method", "")
        if not method:
            return txn

        mapping = resolve_by_method_ci(self.config.account_mappings, method)
        if mapping:
            txn.account = mapping.account
            apply_rebate_account(txn, mapping)

        return txn

    def _matches_condition(self, txn: Transaction, rule: Rule, rule_idx: int) -> bool:
        cond = rule.condition

        if cond.description:
            pattern = self._desc_patterns.get(rule_idx)
            if pattern:
                if not pattern.search(txn.description):
                    return False
            else:
                if cond.description.lower() not in txn.description.lower():
                    return False

        if cond.payee:
            if not txn.payee:
                return False
            pattern = self._payee_patterns.get(rule_idx)
            if pattern:
                if not pattern.search(txn.payee):
                    return False
            else:
                if cond.payee.lower() not in txn.payee.lower():
                    return False

        if cond.card_last4 and txn.card_last4 != cond.card_last4:
            return False

        if cond.provider and txn.provider != cond.provider:
            return False

        if (
            cond.time_range
            and txn.time
            and not self._matches_time_range(txn.time, cond.time_range)
        ):
            return False

        if cond.direction is not None:
            if cond.direction == "expense" and not txn.is_expense:
                return False
            if cond.direction == "income" and not txn.is_income:
                return False

        abs_amount = abs(float(txn.amount))
        if cond.min_amount is not None and abs_amount < cond.min_amount:
            return False
        return not (cond.max_amount is not None and abs_amount > cond.max_amount)

    def _matches_time_range(self, txn_time: time, time_range: str) -> bool:
        """A malformed range matches nothing."""
        try:
            start_str, end_str = time_range.split("-")
            start = time.fromisoformat(start_str.strip())
            end = time.fromisoformat(end_str.strip())

            if start <= end:
                return start <= txn_time <= end
            else:
                # An overnight range such as 22:00-06:00.
                return txn_time >= start or txn_time <= end
        except (ValueError, AttributeError):
            return False

    def _apply_action(self, txn: Transaction, rule: Rule) -> Transaction:
        action = rule.action

        if action.ignore:
            txn.metadata[IGNORED] = True
            return txn

        if action.contra_account:
            txn.contra_account = action.contra_account
            txn.confidence = 1.0
            txn.match_source = MatchSource.RULE
            if rule.condition.description:
                txn.metadata[MATCHED_RULE] = rule.condition.description

        if action.payee is not None:
            # An empty target_payee clears the payee; the header then omits it.
            new_payee = action.payee or None
            if txn.payee and txn.payee != new_payee:
                txn.metadata[ORIGINAL_PAYEE] = txn.payee
            txn.payee = new_payee

        if action.description:
            if txn.description and txn.description != action.description:
                txn.metadata[ORIGINAL_DESCRIPTION] = txn.description
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
    """Run a RulesEngine over the rows, dropping those a rule ignores."""
    engine = RulesEngine(config, preset_rules=preset_rules)
    result = []
    for txn in transactions:
        processed = engine.apply(txn)
        if not processed.metadata.get(IGNORED):
            result.append(processed)
    return result
