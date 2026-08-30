"""Characterisation tests for account_mappings resolution semantics.

Four distinct rules coexist; each test below flips if one is swapped for another.
"""

from datetime import date
from decimal import Decimal

from bean_sieve.api import _resolve_target_account, _set_target_accounts
from bean_sieve.config.schema import AccountMapping, Config
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


class TestMethodLookupInApi:
    """api: pattern must be a case-SENSITIVE substring of metadata['method']."""

    def test_case_variant_method_does_not_match(self):
        cfg = Config(
            account_mappings=[AccountMapping(pattern="CardX", account="Assets:X")]
        )
        txn = _txn(metadata={"method": "cardx(0001)"})

        assert _resolve_target_account(txn, cfg) is None
        [result] = _set_target_accounts([txn], cfg)
        assert result.account is None

    def test_same_case_method_matches(self):
        cfg = Config(
            account_mappings=[AccountMapping(pattern="CardX", account="Assets:X")]
        )
        txn = _txn(metadata={"method": "CardX(0001)"})

        assert _resolve_target_account(txn, cfg) == "Assets:X"
        [result] = _set_target_accounts([txn], cfg)
        assert result.account == "Assets:X"

    def test_method_contained_in_pattern_does_not_match(self):
        cfg = Config(
            account_mappings=[
                AccountMapping(pattern="chan-bank(0001)", account="Assets:Bank")
            ]
        )
        txn = _txn(metadata={"method": "chan"})

        assert _resolve_target_account(txn, cfg) is None
        [result] = _set_target_accounts([txn], cfg)
        assert result.account is None

    def test_first_mapping_in_config_order_wins(self):
        cfg = Config(
            account_mappings=[
                AccountMapping(pattern="chan", account="Assets:First"),
                AccountMapping(pattern="chan-bank", account="Assets:Second"),
            ]
        )
        txn = _txn(metadata={"method": "chan-bank(0001)"})

        assert _resolve_target_account(txn, cfg) == "Assets:First"
        [result] = _set_target_accounts([txn], cfg)
        assert result.account == "Assets:First"


class TestMethodLookupInRules:
    """rules: pattern must be a case-INSENSITIVE substring of the text."""

    def test_case_variant_method_matches(self):
        cfg = Config(
            account_mappings=[AccountMapping(pattern="CardX", account="Assets:X")]
        )
        engine = RulesEngine(cfg)

        result = engine.apply(_txn(metadata={"method": "cardx(0001)"}))
        assert result.account == "Assets:X"

    def test_method_contained_in_pattern_does_not_match(self):
        cfg = Config(
            account_mappings=[
                AccountMapping(pattern="chan-bank(0001)", account="Assets:Bank")
            ]
        )
        engine = RulesEngine(cfg)

        result = engine.apply(_txn(metadata={"method": "chan"}))
        assert result.account is None

    def test_first_mapping_in_config_order_wins(self):
        cfg = Config(
            account_mappings=[
                AccountMapping(pattern="chan", account="Assets:First"),
                AccountMapping(pattern="chan-bank", account="Assets:Second"),
            ]
        )
        engine = RulesEngine(cfg)

        result = engine.apply(_txn(metadata={"method": "chan-bank(0001)"}))
        assert result.account == "Assets:First"

    def test_contra_account_from_metadata_value_is_case_insensitive(self):
        cfg = Config(
            account_mappings=[AccountMapping(pattern="CardX", account="Assets:X")]
        )
        engine = RulesEngine(cfg, preset_rules=[_metadata_key_preset("_target")])

        result = engine.apply(_txn(metadata={"_target": "cardx(0001)"}))
        assert result.contra_account == "Assets:X"
        assert result.match_source == MatchSource.RULE

    def test_contra_account_takes_first_mapping_in_config_order(self):
        cfg = Config(
            account_mappings=[
                AccountMapping(pattern="chan", account="Assets:First"),
                AccountMapping(pattern="chan-bank", account="Assets:Second"),
            ]
        )
        engine = RulesEngine(cfg, preset_rules=[_metadata_key_preset("_target")])

        result = engine.apply(_txn(metadata={"_target": "chan-bank(0001)"}))
        assert result.contra_account == "Assets:First"


class TestKeywordLookupInRules:
    """rules: keyword must be a case-insensitive substring of the pattern."""

    def test_keyword_contained_in_pattern_matches(self):
        cfg = Config(
            account_mappings=[
                AccountMapping(pattern="wallet-main", account="Assets:Wallet")
            ]
        )
        engine = RulesEngine(cfg, preset_rules=[_keyword_preset("WALLET")])

        result = engine.apply(_txn())
        assert result.account == "Assets:Wallet"
        assert result.metadata["matched_preset_rule"] == "probe_keyword"

    def test_keyword_is_a_literal_not_a_regex(self):
        literal_cfg = Config(
            account_mappings=[
                AccountMapping(pattern="bank(0001)", account="Assets:Literal")
            ]
        )
        literal = RulesEngine(literal_cfg, preset_rules=[_keyword_preset("bank(0001)")])
        assert literal.apply(_txn()).account == "Assets:Literal"

        wildcard_cfg = Config(
            account_mappings=[
                AccountMapping(pattern="bank-main", account="Assets:Wildcard")
            ]
        )
        wildcard = RulesEngine(wildcard_cfg, preset_rules=[_keyword_preset("b.nk")])
        assert wildcard.apply(_txn()).account is None

    def test_first_mapping_in_config_order_wins(self):
        cfg = Config(
            account_mappings=[
                AccountMapping(pattern="wallet-main", account="Assets:First"),
                AccountMapping(pattern="wallet-spare", account="Assets:Second"),
            ]
        )
        engine = RulesEngine(cfg, preset_rules=[_keyword_preset("wallet")])

        assert engine.apply(_txn()).account == "Assets:First"

    def test_direction_is_opposite_to_the_method_lookup(self):
        cfg = Config(
            account_mappings=[
                AccountMapping(pattern="wallet-main", account="Assets:Wallet")
            ]
        )
        engine = RulesEngine(cfg, preset_rules=[_keyword_preset("wallet")])

        assert engine.apply(_txn()).account == "Assets:Wallet"
        assert _resolve_target_account(_txn(metadata={"method": "wallet"}), cfg) is None


class TestKeywordLookupInApi:
    """api: keyword is regex-searched against the pattern, IGNORECASE."""

    def test_keyword_is_a_regex(self):
        literal_cfg = Config(
            account_mappings=[
                AccountMapping(pattern="bank(0001)", account="Assets:Literal")
            ]
        )
        [literal] = _set_target_accounts(
            [_txn()], literal_cfg, preset_rules=[_keyword_preset("bank(0001)")]
        )
        assert literal.account is None

        wildcard_cfg = Config(
            account_mappings=[
                AccountMapping(pattern="bank-main", account="Assets:Wildcard")
            ]
        )
        [wildcard] = _set_target_accounts(
            [_txn()], wildcard_cfg, preset_rules=[_keyword_preset("b.nk")]
        )
        assert wildcard.account == "Assets:Wildcard"

    def test_keyword_match_ignores_case(self):
        cfg = Config(
            account_mappings=[
                AccountMapping(pattern="bank-main", account="Assets:Bank")
            ]
        )

        [result] = _set_target_accounts(
            [_txn()], cfg, preset_rules=[_keyword_preset("BANK")]
        )
        assert result.account == "Assets:Bank"
        assert result.metadata["matched_preset_rule"] == "probe_keyword"

    def test_first_mapping_in_config_order_wins(self):
        cfg = Config(
            account_mappings=[
                AccountMapping(pattern="wallet-main", account="Assets:First"),
                AccountMapping(pattern="wallet-spare", account="Assets:Second"),
            ]
        )

        [result] = _set_target_accounts(
            [_txn()], cfg, preset_rules=[_keyword_preset("wallet")]
        )
        assert result.account == "Assets:First"


class TestRebateAccount:
    """Both method lookups copy rebate_account into metadata['_rebate_account']."""

    def test_api_sets_rebate_account_when_transaction_has_rebate(self):
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

    def test_api_leaves_rebate_account_unset_without_rebate(self):
        cfg = Config(
            account_mappings=[
                AccountMapping(
                    pattern="CardX",
                    account="Assets:X",
                    rebate_account="Income:Rebate:X",
                )
            ]
        )
        txn = _txn(metadata={"method": "CardX(0001)"})

        [result] = _set_target_accounts([txn], cfg)
        assert "_rebate_account" not in result.metadata

    def test_rules_sets_rebate_account_when_transaction_has_rebate(self):
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

    def test_rules_leaves_rebate_account_unset_without_rebate(self):
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

        result = engine.apply(_txn(metadata={"method": "cardx(0001)"}))
        assert "_rebate_account" not in result.metadata

    def test_rebate_account_unset_when_mapping_has_none(self):
        cfg = Config(
            account_mappings=[AccountMapping(pattern="CardX", account="Assets:X")]
        )
        txn = _txn(metadata={"method": "CardX(0001)", "rebate": "0.50"})

        [result] = _set_target_accounts([txn], cfg)
        assert "_rebate_account" not in result.metadata
