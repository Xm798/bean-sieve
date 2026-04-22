"""Tests for Config schema."""

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
