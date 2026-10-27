"""Tests for Config schema."""

import json
from pathlib import Path

from ruamel.yaml import YAML

from bean_sieve.config.schema import Config


def test_diagnostics_meta_check_defaults_to_true():
    cfg = Config()
    assert cfg.diagnostics.meta_check is True


def test_diagnostics_meta_check_can_be_disabled_via_dict():
    cfg = Config.from_dict({"diagnostics": {"meta_check": False}})
    assert cfg.diagnostics.meta_check is False


def test_diagnostics_default_section_when_absent():
    cfg = Config.from_dict({})
    assert cfg.diagnostics.meta_check is True


def test_diagnostics_meta_check_accounts_defaults_empty():
    assert Config().diagnostics.meta_check_accounts == []


def test_diagnostics_meta_check_accounts_loaded_from_dict():
    cfg = Config.from_dict({"diagnostics": {"meta_check_accounts": ["SPDB", "HXB"]}})
    assert cfg.diagnostics.meta_check_accounts == ["SPDB", "HXB"]


def test_psbc_example_config_and_schema_are_synchronized() -> None:
    root = Path(__file__).parents[1]
    yaml = YAML(typ="safe")
    example_data = yaml.load(
        (root / "bean-sieve.example.yaml").read_text(encoding="utf-8")
    )
    config = Config.from_dict(example_data)

    assert config.get_provider_config("psbc_credit").accounts == {
        "1234": "Liabilities:CreditCard:PSBC:1234"
    }

    schema = json.loads((root / "bean-sieve.schema.json").read_text(encoding="utf-8"))
    global_metadata = schema["properties"]["defaults"]["properties"]["output_metadata"][
        "items"
    ]["enum"]
    assert "domestic_or_overseas" in global_metadata

    provider_metadata = schema["properties"]["providers"]["properties"]["psbc_credit"][
        "allOf"
    ][1]["properties"]["output_metadata"]["items"]["enum"]
    assert provider_metadata == ["country", "domestic_or_overseas"]


def test_rebate_account_prefixes_default():
    cfg = Config()
    assert cfg.defaults.rebate_account_prefixes == ["Income:Rebate:"]
    assert cfg.rebate_account_prefixes() == ("Income:Rebate:",)


def test_rebate_account_prefixes_parsed_and_merged_with_configured_accounts():
    cfg = Config.from_dict(
        {
            "defaults": {"rebate_account_prefixes": ["Income:Cashback:"]},
            "account_mappings": [
                {"pattern": "a", "account": "Assets:A"},
                {"pattern": "b", "account": "Assets:B", "rebate_account": "Income:R:B"},
                {"pattern": "c", "account": "Assets:C", "rebate_account": "Income:R:B"},
            ],
            "providers": {"p": {"rebate_income_account": "Income:Cashback:"}},
        }
    )
    assert cfg.defaults.rebate_account_prefixes == ["Income:Cashback:"]
    assert cfg.rebate_account_prefixes() == ("Income:Cashback:", "Income:R:B")


def test_card_filter_parsed_from_dict():
    cfg = Config.from_dict(
        {
            "providers": {
                "abc_credit": {
                    "card_filter": {"include": ["1111"], "exclude": ["2222"]}
                }
            }
        }
    )
    card_filter = cfg.get_provider_config("abc_credit").card_filter
    assert card_filter is not None
    assert card_filter.include == ["1111"]
    assert card_filter.exclude == ["2222"]


def test_card_filter_defaults_to_none():
    assert Config().get_provider_config("abc_credit").card_filter is None


def test_card_filter_is_in_json_schema():
    root = Path(__file__).parents[1]
    schema = json.loads((root / "bean-sieve.schema.json").read_text(encoding="utf-8"))
    card_filter = schema["$defs"]["providerConfig"]["properties"]["card_filter"]
    assert card_filter["properties"]["include"]["items"] == {"type": "string"}
    assert card_filter["properties"]["exclude"]["items"] == {"type": "string"}
