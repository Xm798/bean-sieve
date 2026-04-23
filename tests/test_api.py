"""Tests for api.py."""

from bean_sieve.api import _infer_shared_account_metadata
from bean_sieve.config.schema import AccountMapping, Config


def test_shared_accounts_includes_account_with_multiple_patterns():
    cfg = Config(
        account_mappings=[
            AccountMapping(
                pattern="华夏银行信用卡(3855)", account="Liabilities:Credit:HXB"
            ),
            AccountMapping(
                pattern="华夏银行信用卡(9999)", account="Liabilities:Credit:HXB"
            ),
            AccountMapping(
                pattern="浦发银行信用卡(4192)", account="Liabilities:Credit:SPDB"
            ),
        ]
    )
    shared = _infer_shared_account_metadata(cfg)
    assert "Liabilities:Credit:HXB" in shared
    assert "Liabilities:Credit:SPDB" not in shared


def test_shared_accounts_empty_when_all_unique():
    cfg = Config(
        account_mappings=[
            AccountMapping(pattern="a", account="Assets:A"),
            AccountMapping(pattern="b", account="Assets:B"),
        ]
    )
    assert _infer_shared_account_metadata(cfg) == set()


def test_shared_accounts_empty_config():
    assert _infer_shared_account_metadata(Config()) == set()
