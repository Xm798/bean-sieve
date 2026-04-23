"""Tests for Sieve matching engine."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from bean_sieve.core.sieve import Sieve, SieveConfig
from bean_sieve.core.types import Transaction


class TestSieveConfig:
    """Tests for SieveConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = SieveConfig()
        assert config.date_tolerance == 2
        assert config.amount_tolerance == Decimal("0.01")

    def test_custom_config(self):
        """Test custom configuration."""
        config = SieveConfig(date_tolerance=5, amount_tolerance=Decimal("0.10"))
        assert config.date_tolerance == 5
        assert config.amount_tolerance == Decimal("0.10")


class TestSieve:
    """Tests for Sieve engine."""

    def test_create_sieve(self):
        """Test creating a Sieve instance."""
        sieve = Sieve()
        assert sieve.config is not None
        assert sieve._ledger_entries == []

    def test_match_empty_ledger(self, sample_transactions):
        """Test matching against empty ledger."""
        sieve = Sieve()
        result = sieve.match(sample_transactions)

        # All transactions should be missing (no ledger entries)
        assert len(result.matched) == 0
        assert len(result.missing) == len(sample_transactions)
        assert len(result.extra) == 0

    def test_summary(self, sample_transactions):
        """Test result summary generation."""
        sieve = Sieve()
        result = sieve.match(sample_transactions)

        summary = result.summary
        assert "Matched: 0" in summary
        assert "Missing: 3" in summary
        assert "Extra: 0" in summary


def _write_ledger(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "ledger.bean"
    p.write_text(content, encoding="utf-8")
    return p


def test_soft_check_emits_hint_when_ledger_missing_card_last4(tmp_path):
    ledger = _write_ledger(
        tmp_path,
        """
2025-03-15 * "瑞幸咖啡" "拿铁"
    Liabilities:Credit:HXB  -28.00 CNY
    Expenses:Food:Coffee  28.00 CNY

1900-01-01 open Liabilities:Credit:HXB
1900-01-01 open Expenses:Food:Coffee
""".strip(),
    )
    sieve = Sieve(SieveConfig(date_tolerance=0))
    sieve.load_ledger(ledger)

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
    result = sieve.match([txn], meta_check=True)

    assert len(result.matched) == 1
    assert len(result.missing) == 0
    assert len(result.meta_diagnostics) == 1
    d = result.meta_diagnostics[0]
    assert d.severity == "hint"
    assert d.key == "card_last4"
    assert d.expected == "3855"
    assert d.actual is None
    assert d.account == "Liabilities:Credit:HXB"


def test_soft_check_emits_warn_when_ledger_card_last4_differs(tmp_path):
    ledger = _write_ledger(
        tmp_path,
        """
2025-03-15 * "瑞幸咖啡" "拿铁"
    card_last4: "4192"
    Liabilities:Credit:HXB  -28.00 CNY
    Expenses:Food:Coffee  28.00 CNY

1900-01-01 open Liabilities:Credit:HXB
1900-01-01 open Expenses:Food:Coffee
""".strip(),
    )
    sieve = Sieve(SieveConfig(date_tolerance=0))
    sieve.load_ledger(ledger)

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
    result = sieve.match([txn], meta_check=True)

    assert len(result.matched) == 1
    assert len(result.missing) == 0
    assert len(result.meta_diagnostics) == 1
    d = result.meta_diagnostics[0]
    assert d.severity == "warn"
    assert d.actual == "4192"
    assert d.expected == "3855"


def test_hard_filter_retained_when_meta_check_disabled(tmp_path):
    ledger = _write_ledger(
        tmp_path,
        """
2025-03-15 * "瑞幸咖啡" "拿铁"
    card_last4: "4192"
    Liabilities:Credit:HXB  -28.00 CNY
    Expenses:Food:Coffee  28.00 CNY

1900-01-01 open Liabilities:Credit:HXB
1900-01-01 open Expenses:Food:Coffee
""".strip(),
    )
    sieve = Sieve(SieveConfig(date_tolerance=0))
    sieve.load_ledger(ledger)

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
    result = sieve.match([txn], meta_check=False)

    assert len(result.matched) == 0
    assert len(result.missing) == 1
    assert result.meta_diagnostics == []


def test_matched_ledger_with_identical_card_last4_no_diagnostic(tmp_path):
    ledger = _write_ledger(
        tmp_path,
        """
2025-03-15 * "瑞幸咖啡" "拿铁"
    card_last4: "3855"
    Liabilities:Credit:HXB  -28.00 CNY
    Expenses:Food:Coffee  28.00 CNY

1900-01-01 open Liabilities:Credit:HXB
1900-01-01 open Expenses:Food:Coffee
""".strip(),
    )
    sieve = Sieve(SieveConfig(date_tolerance=0))
    sieve.load_ledger(ledger)

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
    result = sieve.match([txn], meta_check=True)

    assert len(result.matched) == 1
    assert result.meta_diagnostics == []


def test_soft_check_recognizes_posting_level_card_last4(tmp_path):
    """card_last4 emitted at posting level (writer's format) should clear the diagnostic."""
    ledger = _write_ledger(
        tmp_path,
        """
2025-03-15 * "瑞幸咖啡" "拿铁"
    Liabilities:Credit:HXB  -28.00 CNY
        card_last4: "3855"
    Expenses:Food:Coffee  28.00 CNY

1900-01-01 open Liabilities:Credit:HXB
1900-01-01 open Expenses:Food:Coffee
""".strip(),
    )
    sieve = Sieve(SieveConfig(date_tolerance=0))
    sieve.load_ledger(ledger)

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
    result = sieve.match([txn], meta_check=True)

    assert len(result.matched) == 1
    assert result.meta_diagnostics == []


def test_soft_check_warn_for_posting_level_conflict(tmp_path):
    """Conflicting card_last4 at posting level should produce warn, not hint."""
    ledger = _write_ledger(
        tmp_path,
        """
2025-03-15 * "瑞幸咖啡" "拿铁"
    Liabilities:Credit:HXB  -28.00 CNY
        card_last4: "4192"
    Expenses:Food:Coffee  28.00 CNY

1900-01-01 open Liabilities:Credit:HXB
1900-01-01 open Expenses:Food:Coffee
""".strip(),
    )
    sieve = Sieve(SieveConfig(date_tolerance=0))
    sieve.load_ledger(ledger)

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
    result = sieve.match([txn], meta_check=True)

    assert len(result.matched) == 1
    assert len(result.meta_diagnostics) == 1
    d = result.meta_diagnostics[0]
    assert d.severity == "warn"
    assert d.actual == "4192"


def test_check_scope_filters_out_accounts_not_in_scope(tmp_path):
    """Diagnostics must not fire on accounts outside the check scope.

    Accounts like Assets:Bank:ICBC:5625 already encode the card number in the
    account name; they should not produce hint/warn diagnostics even when the
    statement carries card_last4.
    """
    ledger = _write_ledger(
        tmp_path,
        """
2025-03-15 * "某商户" "消费"
    Assets:Bank:ICBC:5625  -28.00 CNY
    Expenses:FIXME  28.00 CNY

1900-01-01 open Assets:Bank:ICBC:5625
1900-01-01 open Expenses:FIXME
""".strip(),
    )
    sieve = Sieve(SieveConfig(date_tolerance=0))
    sieve.load_ledger(ledger)

    txn = Transaction(
        date=date(2025, 3, 15),
        amount=Decimal("28.00"),
        currency="CNY",
        description="消费",
        payee="某商户",
        card_last4="5625",
        account="Assets:Bank:ICBC:5625",
        provider="alipay",
    )
    result = sieve.match(
        [txn],
        meta_check=True,
        check_scope=lambda _a: False,
    )

    assert len(result.matched) == 1
    assert result.meta_diagnostics == []


def test_check_scope_admits_only_scoped_accounts(tmp_path):
    """Only accounts for which check_scope returns True produce diagnostics."""
    ledger = _write_ledger(
        tmp_path,
        """
2025-03-15 * "A" "a"
    Liabilities:Credit:HXB  -28.00 CNY
    Expenses:FIXME  28.00 CNY

2025-03-16 * "B" "b"
    Assets:Bank:ICBC:5625  -10.00 CNY
    Expenses:FIXME  10.00 CNY

1900-01-01 open Liabilities:Credit:HXB
1900-01-01 open Assets:Bank:ICBC:5625
1900-01-01 open Expenses:FIXME
""".strip(),
    )
    sieve = Sieve(SieveConfig(date_tolerance=0))
    sieve.load_ledger(ledger)

    txns = [
        Transaction(
            date=date(2025, 3, 15),
            amount=Decimal("28.00"),
            currency="CNY",
            description="a",
            payee="A",
            card_last4="3855",
            account="Liabilities:Credit:HXB",
            provider="alipay",
        ),
        Transaction(
            date=date(2025, 3, 16),
            amount=Decimal("10.00"),
            currency="CNY",
            description="b",
            payee="B",
            card_last4="5625",
            account="Assets:Bank:ICBC:5625",
            provider="alipay",
        ),
    ]
    result = sieve.match(
        txns,
        meta_check=True,
        check_scope=lambda a: "HXB" in a,
    )

    assert len(result.matched) == 2
    assert len(result.meta_diagnostics) == 1
    assert result.meta_diagnostics[0].account == "Liabilities:Credit:HXB"
