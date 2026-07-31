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
