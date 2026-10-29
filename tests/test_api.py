"""Tests for api.py."""

import base64
from datetime import date
from decimal import Decimal

from bean_sieve.api import (
    _apply_provider_output_config,
    _build_check_scope,
    generate_output,
)
from bean_sieve.config.schema import AccountMapping, Config, DiagnosticsConfig
from bean_sieve.core.types import MatchResult, ReconcileResult, Transaction


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
    pattern='云闪付-交通银行(6420)' (method ⊂ pattern) and wrongly resolve to a
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
                pattern="云闪付-交通银行(6420)",
                account="Liabilities:Credit:BOCOM:6420",
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
    assert _resolve_target_account(txn, cfg, {}) is None
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
                account="Liabilities:Credit:BOCOM:6420",
            ),
        ]
    )
    txn = Transaction(
        date=date(2026, 6, 3),
        amount=Decimal("42.50"),
        currency="CNY",
        description="测试消费",
        provider="alipay",
        metadata={"method": "交通银行信用卡(6420)"},
    )
    assert _resolve_target_account(txn, cfg, {}) == "Liabilities:Credit:BOCOM:6420"
    [result] = _set_target_accounts([txn], cfg)
    assert result.account == "Liabilities:Credit:BOCOM:6420"


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


def _balance_row(
    trade_day: int,
    balance: str,
    post_day: int | None = None,
    currency: str = "HKD",
    metadata: dict[str, str] | None = None,
):
    from datetime import date
    from decimal import Decimal

    from bean_sieve.core.types import Transaction

    return Transaction(
        date=date(2030, 9, trade_day),
        post_date=date(2030, 9, post_day) if post_day else None,
        amount=Decimal("10.00"),
        currency=currency,
        description=f"row-{balance}",
        provider="fake_debit",
        account="Assets:Bank:A",
        metadata={"balance": balance, **(metadata or {})},
    )


def test_balance_follows_the_last_posted_row():
    """The balance column runs in posting order, so a late-posted row is last."""
    from bean_sieve.api import _generate_balance_directives
    from bean_sieve.config.schema import Config, ProviderConfig

    cfg = Config(providers={"fake_debit": ProviderConfig(balance=True)})
    transactions = [
        _balance_row(trade_day=15, post_day=15, balance="100.00"),
        _balance_row(trade_day=14, post_day=16, balance="90.00"),
    ]

    assert _generate_balance_directives(transactions, cfg) == (
        "\n2030-09-17 balance Assets:Bank:A  90.00 HKD\n"
    )


def test_balance_uses_the_balance_currency():
    """A row billed in another currency still reports the account's balance."""
    from bean_sieve.api import _generate_balance_directives
    from bean_sieve.config.schema import Config, ProviderConfig

    cfg = Config(providers={"fake_debit": ProviderConfig(balance=True)})
    transactions = [
        _balance_row(trade_day=1, balance="100.00"),
        _balance_row(
            trade_day=2,
            balance="80.00",
            currency="USD",
            metadata={"balance_currency": "HKD"},
        ),
    ]

    assert _generate_balance_directives(transactions, cfg) == (
        "\n2030-09-03 balance Assets:Bank:A  80.00 HKD\n"
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


def _abc_eml(html: str) -> str:
    encoded = base64.b64encode(html.encode("utf-8")).decode("ascii")
    return (
        "From: creditcard@example.com\n"
        "Subject: statement\n"
        "To: test@example.com\n"
        "MIME-Version: 1.0\n"
        'Content-Type: text/html; charset="utf-8"\n'
        "Content-Transfer-Encoding: base64\n"
        "\n"
        f"{encoded}\n"
    )


_ABC_HTML = """<html>
<head><title>金穗信用卡电子对账单</title></head>
<body>
<table>
    <tr><td><span>620000******1234</span></td></tr>
    <tr><td><span>2030/01/01-2030/01/31</span></td></tr>
    <tr><td><span>本期应还款额</span></td></tr>
    <tr><td>-30.00</td></tr>
</table>
<table>
    <tr><td><span>本期账单金额</span></td></tr>
</table>
<table>
    <tr><td>30.00</td></tr>
</table>
<table>
    <tr>
        <td>交易日期</td><td>入账日期</td><td>卡号后四位</td>
        <td>交易描述</td><td>交易金额/币种</td><td>入账金额/币种</td>
    </tr>
    <tr>
        <td>300102</td>
        <td>300102</td>
        <td>1234</td>
        <td>payee-a</td>
        <td>-10.00/CNY</td>
        <td>-10.00/CNY</td>
    </tr>
    <tr>
        <td>300120</td>
        <td>300120</td>
        <td>1234</td>
        <td>payee-b</td>
        <td>-20.00/CNY</td>
        <td>-20.00/CNY</td>
    </tr>
</table>
</body>
</html>"""


def test_full_reconcile_post_output_reuses_parsed_snapshot(tmp_path, monkeypatch):
    """post_output reports the whole statement off context.parsed, parsing once.

    date_range keeps only the first row, yet 解析消费 must still cover both, and
    the snapshot spares the provider a second parse of the same file.
    """
    from pathlib import Path

    from bean_sieve.api import full_reconcile
    from bean_sieve.providers.banks.credit.abc import ABCCreditProvider

    statement = tmp_path / "农业银行金穗信用卡2030年1月.eml"
    statement.write_text(_abc_eml(_ABC_HTML), encoding="utf-8")
    ledger = tmp_path / "ledger.bean"
    ledger.write_text(
        "1900-01-01 open Liabilities:Credit:ABC:1234 CNY\n", encoding="utf-8"
    )
    config_file = tmp_path / "bean-sieve.yaml"
    config_file.write_text(
        'providers:\n  abc_credit:\n    accounts:\n      "1234": '
        "Liabilities:Credit:ABC:1234\n",
        encoding="utf-8",
    )
    output = tmp_path / "pending.bean"

    parse_calls: list[Path] = []
    original_parse = ABCCreditProvider.parse

    def counting_parse(self, file_path: Path):
        parse_calls.append(file_path)
        return original_parse(self, file_path)

    monkeypatch.setattr(ABCCreditProvider, "parse", counting_parse)

    result = full_reconcile(
        [statement],
        ledger,
        config_path=config_file,
        output_path=output,
        provider_id="abc_credit",
        date_range=(date(2030, 1, 1), date(2030, 1, 5)),
    )

    assert [t.date for t in result.processed] == [date(2030, 1, 2)]
    assert ";   解析消费:              30.00 CNY" in output.read_text(encoding="utf-8")
    assert parse_calls == [statement]


def _fee_output(config: Config, provider: str) -> str:
    txn = Transaction(
        date=date(2030, 1, 2),
        amount=Decimal("100.00"),
        currency="CNY",
        description="desc-a",
        account="Assets:Wallet:Test",
        contra_account="Assets:Bank:Test",
        provider=provider,
        fee=Decimal("0.10"),
    )
    processed = _apply_provider_output_config([txn], config)
    result = ReconcileResult(match_result=MatchResult(), processed=processed)
    return generate_output(result, config=config)


def test_fee_account_prefers_provider_config_over_defaults():
    config = Config.from_dict(
        {
            "defaults": {"fee_account": "Expenses:Fee"},
            "providers": {"prov-a": {"fee_account": "Expenses:Fee:ProvA"}},
        }
    )
    assert "    Expenses:Fee:ProvA  0.10 CNY" in _fee_output(config, "prov-a")
    assert "    Expenses:Fee  0.10 CNY" in _fee_output(config, "prov-b")
