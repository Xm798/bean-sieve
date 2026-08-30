"""Tests for api.py."""

from bean_sieve.api import _build_check_scope
from bean_sieve.config.schema import AccountMapping, Config, DiagnosticsConfig


def test_check_scope_empty_without_explicit_config():
    """No auto-detection: empty scope unless meta_check_accounts is set."""
    cfg = Config(
        account_mappings=[
            AccountMapping(
                pattern="华夏银行信用卡(1234)", account="Liabilities:Credit:HXB"
            ),
            AccountMapping(
                pattern="华夏银行信用卡(9999)", account="Liabilities:Credit:HXB"
            ),
        ]
    )
    scope = _build_check_scope(cfg)
    assert scope("Liabilities:Credit:HXB") is False


def test_check_scope_matches_explicit_keywords():
    cfg = Config(
        diagnostics=DiagnosticsConfig(meta_check_accounts=["SPDB", "HXB"]),
    )
    scope = _build_check_scope(cfg)
    assert scope("Liabilities:Credit:SPDB") is True
    assert scope("Liabilities:Credit:HXB") is True
    assert scope("Assets:Bank:ICBC:9999") is False


def test_check_scope_empty_when_no_config():
    scope = _build_check_scope(Config())
    assert scope("Liabilities:Credit:HXB") is False


def test_generate_output_passes_check_scope_to_writer():
    from datetime import date
    from decimal import Decimal

    from bean_sieve.api import generate_output
    from bean_sieve.core.types import (
        MatchResult,
        ReconcileResult,
        Transaction,
    )

    cfg = Config(
        diagnostics=DiagnosticsConfig(meta_check_accounts=["HXB"]),
    )
    txn = Transaction(
        date=date(2025, 3, 15),
        amount=Decimal("28.00"),
        currency="CNY",
        description="拿铁",
        payee="瑞幸咖啡",
        card_last4="1234",
        account="Liabilities:Credit:HXB",
        contra_account="Expenses:Food:Coffee",
        provider="alipay",
    )
    result = ReconcileResult(
        match_result=MatchResult(),
        processed=[txn],
    )
    content = generate_output(result, config=cfg)
    assert 'card_last4: "1234"' in content


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
    card_last4: "5678"
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
        card_last4="1234",
        account="Liabilities:Credit:HXB",
        provider="alipay",
    )
    cfg = Config(diagnostics=DiagnosticsConfig(meta_check=False))
    result = reconcile([txn], sieve, config=cfg)
    # With hard filter, conflicting meta causes no match -> txn goes to missing -> processed
    assert len(result.processed) == 1


def test_account_mapping_generic_method_not_matched_to_specific_pattern():
    """A generic payment channel must NOT match a card-specific pattern.

    Regression: bidirectional substring matching let method='云闪付' match
    pattern='云闪付-交通银行(5871)' (method ⊂ pattern) and wrongly resolve to a
    specific card account. Generic channels with no card info must stay
    unresolved (-> FIXME) rather than guessing a card.
    """
    from datetime import date
    from decimal import Decimal

    from bean_sieve.api import _resolve_target_account, _set_target_accounts
    from bean_sieve.config.schema import AccountMapping, Config
    from bean_sieve.core.types import Transaction

    cfg = Config(
        account_mappings=[
            AccountMapping(
                pattern="云闪付-交通银行(5871)",
                account="Liabilities:Credit:BOCOM:5871",
            ),
        ]
    )
    txn = Transaction(
        date=date(2026, 6, 3),
        amount=Decimal("200.00"),
        currency="CNY",
        description="江苏联通200元",
        provider="meituan",
        metadata={"method": "云闪付"},
    )
    assert _resolve_target_account(txn, cfg) is None
    [result] = _set_target_accounts([txn], cfg)
    assert result.account is None


def test_account_mapping_pattern_contained_in_method_still_matches():
    """Forward direction still works: config pattern ⊂ actual method string."""
    from datetime import date
    from decimal import Decimal

    from bean_sieve.api import _resolve_target_account, _set_target_accounts
    from bean_sieve.config.schema import AccountMapping, Config
    from bean_sieve.core.types import Transaction

    cfg = Config(
        account_mappings=[
            AccountMapping(
                pattern="交通银行信用卡",
                account="Liabilities:Credit:BOCOM:5871",
            ),
        ]
    )
    txn = Transaction(
        date=date(2026, 6, 3),
        amount=Decimal("42.50"),
        currency="CNY",
        description="测试消费",
        provider="alipay",
        metadata={"method": "交通银行信用卡(5871)"},
    )
    assert _resolve_target_account(txn, cfg) == "Liabilities:Credit:BOCOM:5871"
    [result] = _set_target_accounts([txn], cfg)
    assert result.account == "Liabilities:Credit:BOCOM:5871"


def test_generate_balance_directives_golden():
    """Pins ordering, the +1-day rule, decimal scale and the unparsable skip."""
    from datetime import date
    from decimal import Decimal

    from bean_sieve.api import _generate_balance_directives
    from bean_sieve.config.schema import Config, ProviderConfig
    from bean_sieve.core.types import Transaction

    cfg = Config(providers={"fake_debit": ProviderConfig(balance=True)})
    transactions = [
        Transaction(
            date=date(2030, 1, 20),
            amount=Decimal("10.00"),
            currency="CNY",
            description="desc-a",
            provider="fake_debit",
            account="Assets:Bank:A",
            statement_period=(date(2030, 1, 1), date(2030, 1, 31)),
            metadata={"balance": "1,000.00"},
        ),
        Transaction(
            date=date(2030, 1, 2),
            amount=Decimal("20.00"),
            currency="CNY",
            description="desc-b",
            provider="fake_debit",
            account="Assets:Bank:B",
            metadata={"balance": "20"},
        ),
        Transaction(
            date=date(2030, 1, 3),
            amount=Decimal("30.00"),
            currency="CNY",
            description="desc-c",
            provider="fake_debit",
            account="Assets:Bank:C",
            metadata={"balance": "not-a-number"},
        ),
    ]

    assert _generate_balance_directives(transactions, cfg) == (
        "\n"
        "2030-02-01 balance Assets:Bank:A  1000.00 CNY\n"
        "2030-01-03 balance Assets:Bank:B  20 CNY\n"
    )


def test_full_reconcile_post_output_precedes_balance_block(tmp_path, monkeypatch):
    """Balance directives follow the post_output text, never precede it."""
    from datetime import date
    from decimal import Decimal
    from pathlib import Path

    from bean_sieve.api import full_reconcile
    from bean_sieve.core.types import ReconcileContext, ReconcileResult, Transaction
    from bean_sieve.providers import PROVIDERS
    from bean_sieve.providers.base import BaseProvider

    class MarkerProvider(BaseProvider):
        provider_id = "fake_debit"
        provider_name = "Fake Debit"
        supported_formats = [".csv"]

        def parse(self, _file_path: Path) -> list[Transaction]:
            return [
                Transaction(
                    date=date(2030, 1, 2),
                    amount=Decimal("10.00"),
                    currency="CNY",
                    description="desc-a",
                    provider="fake_debit",
                    account="Assets:Bank:A",
                    metadata={"balance": "10.00"},
                )
            ]

        def post_output(
            self,
            content: str,
            _result: ReconcileResult,
            _context: ReconcileContext,
        ) -> str:
            return content + "\n; marker\n"

    monkeypatch.setitem(PROVIDERS, "fake_debit", MarkerProvider)

    statement = tmp_path / "statement.csv"
    statement.write_text("", encoding="utf-8")
    ledger = tmp_path / "ledger.bean"
    ledger.write_text("1900-01-01 open Assets:Bank:A\n", encoding="utf-8")
    config_file = tmp_path / "bean-sieve.yaml"
    config_file.write_text(
        "providers:\n  fake_debit:\n    balance: true\n", encoding="utf-8"
    )
    output = tmp_path / "pending.bean"

    full_reconcile(
        [statement],
        ledger,
        config_path=config_file,
        output_path=output,
        provider_id="fake_debit",
    )

    content = output.read_text(encoding="utf-8")
    assert content.index("; marker") < content.index(" balance ")
