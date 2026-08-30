"""Tests for account_mappings resolution.

The four rules in core.accounts differ in direction, case sensitivity and
literal-vs-regex; each test below flips if one is swapped for another.
"""

from datetime import date
from decimal import Decimal

from bean_sieve.api import _resolve_target_account, _set_target_accounts
from bean_sieve.config.schema import AccountMapping, Config
from bean_sieve.core.accounts import (
    apply_rebate_account,
    resolve_by_keyword_regex,
    resolve_by_keyword_substring,
    resolve_by_method,
    resolve_by_method_ci,
)
from bean_sieve.core.preset_rules import (
    PresetRule,
    PresetRuleAction,
    PresetRuleCondition,
)
from bean_sieve.core.rules import RulesEngine
from bean_sieve.core.types import MatchSource, Transaction


def _txn(**overrides) -> Transaction:
    fields = {
        "date": date(2030, 1, 2),
        "amount": Decimal("10.00"),
        "currency": "CNY",
        "description": "probe",
        "provider": "probe_provider",
    }
    fields.update(overrides)
    return Transaction(**fields)


def _keyword_preset(keyword: str) -> PresetRule:
    return PresetRule(
        rule_id="probe_keyword",
        name="probe keyword",
        condition=PresetRuleCondition(description="^probe$"),
        action=PresetRuleAction(account_keyword=keyword),
    )


def _metadata_key_preset(key: str) -> PresetRule:
    return PresetRule(
        rule_id="probe_metadata_key",
        name="probe metadata key",
        condition=PresetRuleCondition(description="^probe$"),
        action=PresetRuleAction(contra_account_metadata_key=key),
    )


ORDERED_MAPPINGS = [
    AccountMapping(pattern="chan", account="Assets:First"),
    AccountMapping(pattern="chan-bank", account="Assets:Second"),
]


class TestResolveByMethod:
    """Pattern must be a case-SENSITIVE substring of the method."""

    def test_pattern_contained_in_method_matches(self):
        mappings = [
            AccountMapping(
                pattern="CardX", account="Assets:X", rebate_account="Income:Rebate:X"
            )
        ]
        mapping = resolve_by_method(mappings, "CardX(0001)")

        assert mapping is not None
        assert mapping.account == "Assets:X"
        assert mapping.rebate_account == "Income:Rebate:X"

    def test_case_variant_method_does_not_match(self):
        mappings = [AccountMapping(pattern="CardX", account="Assets:X")]

        assert resolve_by_method(mappings, "cardx(0001)") is None

    def test_method_contained_in_pattern_does_not_match(self):
        mappings = [AccountMapping(pattern="chan-bank(0001)", account="Assets:Bank")]

        assert resolve_by_method(mappings, "chan") is None

    def test_first_mapping_in_config_order_wins(self):
        mapping = resolve_by_method(ORDERED_MAPPINGS, "chan-bank(0001)")

        assert mapping is not None
        assert mapping.account == "Assets:First"


class TestResolveByMethodCi:
    """Pattern must be a case-INSENSITIVE substring of the text."""

    def test_case_variant_text_matches(self):
        mappings = [AccountMapping(pattern="CardX", account="Assets:X")]
        mapping = resolve_by_method_ci(mappings, "cardx(0001)")

        assert mapping is not None
        assert mapping.account == "Assets:X"

    def test_text_contained_in_pattern_does_not_match(self):
        mappings = [AccountMapping(pattern="chan-bank(0001)", account="Assets:Bank")]

        assert resolve_by_method_ci(mappings, "chan") is None

    def test_first_mapping_in_config_order_wins(self):
        mapping = resolve_by_method_ci(ORDERED_MAPPINGS, "CHAN-BANK(0001)")

        assert mapping is not None
        assert mapping.account == "Assets:First"


class TestResolveByKeywordSubstring:
    """Keyword must be a case-insensitive substring of the pattern."""

    def test_keyword_contained_in_pattern_matches(self):
        mappings = [AccountMapping(pattern="wallet-main", account="Assets:Wallet")]

        assert resolve_by_keyword_substring(mappings, "WALLET") == "Assets:Wallet"

    def test_pattern_contained_in_keyword_does_not_match(self):
        mappings = [AccountMapping(pattern="wallet", account="Assets:Wallet")]

        assert resolve_by_keyword_substring(mappings, "wallet-main") is None

    def test_keyword_is_a_literal_not_a_regex(self):
        mappings = [AccountMapping(pattern="bank-main", account="Assets:Bank")]

        assert resolve_by_keyword_substring(mappings, "b.nk") is None

    def test_regex_metacharacters_match_literally(self):
        mappings = [AccountMapping(pattern="bank(0001)", account="Assets:Bank")]

        assert resolve_by_keyword_substring(mappings, "bank(0001)") == "Assets:Bank"

    def test_first_mapping_in_config_order_wins(self):
        mappings = [
            AccountMapping(pattern="wallet-main", account="Assets:First"),
            AccountMapping(pattern="wallet-spare", account="Assets:Second"),
        ]

        assert resolve_by_keyword_substring(mappings, "wallet") == "Assets:First"


class TestResolveByKeywordRegex:
    """Keyword is regex-searched against the pattern, IGNORECASE."""

    def test_keyword_is_a_regex(self):
        mappings = [AccountMapping(pattern="bank-main", account="Assets:Bank")]

        assert resolve_by_keyword_regex(mappings, "b.nk") == "Assets:Bank"

    def test_regex_metacharacters_do_not_match_literally(self):
        mappings = [AccountMapping(pattern="bank(0001)", account="Assets:Bank")]

        assert resolve_by_keyword_regex(mappings, "bank(0001)") is None

    def test_match_ignores_case(self):
        mappings = [AccountMapping(pattern="bank-main", account="Assets:Bank")]

        assert resolve_by_keyword_regex(mappings, "BANK") == "Assets:Bank"

    def test_pattern_contained_in_keyword_does_not_match(self):
        mappings = [AccountMapping(pattern="wallet", account="Assets:Wallet")]

        assert resolve_by_keyword_regex(mappings, "wallet-main") is None

    def test_first_mapping_in_config_order_wins(self):
        mappings = [
            AccountMapping(pattern="wallet-main", account="Assets:First"),
            AccountMapping(pattern="wallet-spare", account="Assets:Second"),
        ]

        assert resolve_by_keyword_regex(mappings, "wallet") == "Assets:First"


class TestApplyRebateAccount:
    def test_writes_rebate_account_when_transaction_has_rebate(self):
        mapping = AccountMapping(
            pattern="CardX", account="Assets:X", rebate_account="Income:Rebate:X"
        )
        txn = _txn(metadata={"rebate": "0.50"})

        apply_rebate_account(txn, mapping)
        assert txn.metadata["_rebate_account"] == "Income:Rebate:X"

    def test_leaves_metadata_untouched_without_rebate(self):
        mapping = AccountMapping(
            pattern="CardX", account="Assets:X", rebate_account="Income:Rebate:X"
        )
        txn = _txn(metadata={})

        apply_rebate_account(txn, mapping)
        assert "_rebate_account" not in txn.metadata

    def test_leaves_metadata_untouched_without_rebate_account(self):
        mapping = AccountMapping(pattern="CardX", account="Assets:X")
        txn = _txn(metadata={"rebate": "0.50"})

        apply_rebate_account(txn, mapping)
        assert "_rebate_account" not in txn.metadata


class TestApiCallSites:
    """api resolves methods case-sensitively and preset keywords as regexes."""

    def test_method_lookup_is_case_sensitive(self):
        cfg = Config(
            account_mappings=[AccountMapping(pattern="CardX", account="Assets:X")]
        )
        txn = _txn(metadata={"method": "cardx(0001)"})

        assert _resolve_target_account(txn, cfg) is None
        [result] = _set_target_accounts([txn], cfg)
        assert result.account is None

    def test_method_lookup_matches_same_case(self):
        cfg = Config(
            account_mappings=[AccountMapping(pattern="CardX", account="Assets:X")]
        )
        txn = _txn(metadata={"method": "CardX(0001)"})

        assert _resolve_target_account(txn, cfg) == "Assets:X"
        [result] = _set_target_accounts([txn], cfg)
        assert result.account == "Assets:X"

    def test_preset_keyword_lookup_is_a_regex(self):
        cfg = Config(
            account_mappings=[
                AccountMapping(pattern="bank-main", account="Assets:Bank")
            ]
        )

        [result] = _set_target_accounts(
            [_txn()], cfg, preset_rules=[_keyword_preset("b.nk")]
        )
        assert result.account == "Assets:Bank"
        assert result.metadata["matched_preset_rule"] == "probe_keyword"

    def test_method_lookup_sets_rebate_account(self):
        cfg = Config(
            account_mappings=[
                AccountMapping(
                    pattern="CardX",
                    account="Assets:X",
                    rebate_account="Income:Rebate:X",
                )
            ]
        )
        txn = _txn(metadata={"method": "CardX(0001)", "rebate": "0.50"})

        [result] = _set_target_accounts([txn], cfg)
        assert result.metadata["_rebate_account"] == "Income:Rebate:X"


class TestRulesCallSites:
    """rules resolves methods case-insensitively and preset keywords as substrings."""

    def test_method_lookup_is_case_insensitive(self):
        cfg = Config(
            account_mappings=[AccountMapping(pattern="CardX", account="Assets:X")]
        )
        engine = RulesEngine(cfg)

        result = engine.apply(_txn(metadata={"method": "cardx(0001)"}))
        assert result.account == "Assets:X"

    def test_preset_keyword_lookup_is_a_substring(self):
        cfg = Config(
            account_mappings=[
                AccountMapping(pattern="wallet-main", account="Assets:Wallet")
            ]
        )
        engine = RulesEngine(cfg, preset_rules=[_keyword_preset("WALLET")])

        result = engine.apply(_txn())
        assert result.account == "Assets:Wallet"
        assert result.metadata["matched_preset_rule"] == "probe_keyword"

    def test_contra_account_from_metadata_value_is_case_insensitive(self):
        cfg = Config(
            account_mappings=[AccountMapping(pattern="CardX", account="Assets:X")]
        )
        engine = RulesEngine(cfg, preset_rules=[_metadata_key_preset("_target")])

        result = engine.apply(_txn(metadata={"_target": "cardx(0001)"}))
        assert result.contra_account == "Assets:X"
        assert result.match_source == MatchSource.RULE

    def test_method_lookup_sets_rebate_account(self):
        cfg = Config(
            account_mappings=[
                AccountMapping(
                    pattern="CardX",
                    account="Assets:X",
                    rebate_account="Income:Rebate:X",
                )
            ]
        )
        engine = RulesEngine(cfg)

        result = engine.apply(
            _txn(metadata={"method": "cardx(0001)", "rebate": "0.50"})
        )
        assert result.metadata["_rebate_account"] == "Income:Rebate:X"
