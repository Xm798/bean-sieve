"""Tests for api.py."""

from bean_sieve.api import _build_check_scope, _infer_shared_account_metadata
from bean_sieve.config.schema import AccountMapping, Config, DiagnosticsConfig


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


def test_check_scope_matches_auto_inferred_accounts():
    cfg = Config(
        account_mappings=[
            AccountMapping(
                pattern="华夏银行信用卡(3855)", account="Liabilities:Credit:HXB"
            ),
            AccountMapping(
                pattern="华夏银行信用卡(9999)", account="Liabilities:Credit:HXB"
            ),
        ]
    )
    scope = _build_check_scope(cfg)
    assert scope("Liabilities:Credit:HXB") is True
    assert scope("Assets:Bank:ICBC:5625") is False


def test_check_scope_matches_explicit_keywords():
    cfg = Config(
        account_mappings=[
            AccountMapping(pattern="pudong", account="Liabilities:Credit:SPDB"),
        ],
        diagnostics=DiagnosticsConfig(meta_check_accounts=["SPDB", "HXB"]),
    )
    scope = _build_check_scope(cfg)
    assert scope("Liabilities:Credit:SPDB") is True
    assert scope("Liabilities:Credit:HXB") is True
    assert scope("Assets:Bank:ICBC:5625") is False


def test_check_scope_unions_auto_and_explicit():
    cfg = Config(
        account_mappings=[
            AccountMapping(
                pattern="华夏银行信用卡(3855)", account="Liabilities:Credit:HXB"
            ),
            AccountMapping(
                pattern="华夏银行信用卡(9999)", account="Liabilities:Credit:HXB"
            ),
        ],
        diagnostics=DiagnosticsConfig(meta_check_accounts=["SPDB"]),
    )
    scope = _build_check_scope(cfg)
    assert scope("Liabilities:Credit:HXB") is True  # auto-inferred
    assert scope("Liabilities:Credit:SPDB") is True  # explicit keyword
    assert scope("Assets:Bank:ICBC:5625") is False


def test_check_scope_empty_when_no_config():
    scope = _build_check_scope(Config())
    assert scope("Liabilities:Credit:HXB") is False


def test_generate_output_passes_shared_accounts_to_writer():
    from datetime import date
    from decimal import Decimal

    from bean_sieve.api import generate_output
    from bean_sieve.config.schema import AccountMapping, Config
    from bean_sieve.core.types import (
        MatchResult,
        ReconcileResult,
        Transaction,
    )

    cfg = Config(
        account_mappings=[
            AccountMapping(
                pattern="华夏银行信用卡(3855)", account="Liabilities:Credit:HXB"
            ),
            AccountMapping(
                pattern="华夏银行信用卡(9999)", account="Liabilities:Credit:HXB"
            ),
        ]
    )
    txn = Transaction(
        date=date(2025, 3, 15),
        amount=Decimal("28.00"),
        currency="CNY",
        description="拿铁",
        payee="瑞幸咖啡",
        card_last4="3855",
        account="Liabilities:Credit:HXB",
        contra_account="Expenses:Food:Coffee",
        provider="alipay",
    )
    result = ReconcileResult(
        match_result=MatchResult(),
        processed=[txn],
    )
    content = generate_output(result, config=cfg)
    assert 'card_last4: "3855"' in content


def test_reconcile_honors_diagnostics_meta_check_flag(tmp_path):
    """When diagnostics.meta_check=False, sieve uses hard filter (legacy)."""
    from datetime import date
    from decimal import Decimal

    from bean_sieve.api import load_ledger, reconcile
    from bean_sieve.config.schema import Config, DiagnosticsConfig
    from bean_sieve.core.types import Transaction

    ledger_file = tmp_path / "ledger.bean"
    ledger_file.write_text(
        """
2025-03-15 * "瑞幸咖啡" "拿铁"
    card_last4: "4192"
    Liabilities:Credit:HXB  -28.00 CNY
    Expenses:Food:Coffee  28.00 CNY

1900-01-01 open Liabilities:Credit:HXB
1900-01-01 open Expenses:Food:Coffee
""".strip(),
        encoding="utf-8",
    )
    sieve = load_ledger(ledger_file, date_tolerance=0)
    txn = Transaction(
        date=date(2025, 3, 15),
        amount=Decimal("28.00"),
        currency="CNY",
        description="拿铁",
        payee="瑞幸咖啡",
        card_last4="3855",
        account="Liabilities:Credit:HXB",
        provider="alipay",
    )
    cfg = Config(diagnostics=DiagnosticsConfig(meta_check=False))
    result = reconcile([txn], sieve, config=cfg)
    # With hard filter, conflicting meta causes no match -> txn goes to missing -> processed
    assert len(result.processed) == 1
