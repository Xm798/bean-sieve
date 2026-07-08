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
        card_last4="1234",
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
    assert d.expected == "1234"
    assert d.actual is None
    assert d.account == "Liabilities:Credit:HXB"


def test_soft_check_emits_warn_when_ledger_card_last4_differs(tmp_path):
    ledger = _write_ledger(
        tmp_path,
        """
2025-03-15 * "瑞幸咖啡" "拿铁"
    card_last4: "5678"
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
        card_last4="1234",
        account="Liabilities:Credit:HXB",
        provider="alipay",
    )
    result = sieve.match([txn], meta_check=True)

    assert len(result.matched) == 1
    assert len(result.missing) == 0
    assert len(result.meta_diagnostics) == 1
    d = result.meta_diagnostics[0]
    assert d.severity == "warn"
    assert d.actual == "5678"
    assert d.expected == "1234"


def test_hard_filter_retained_when_meta_check_disabled(tmp_path):
    ledger = _write_ledger(
        tmp_path,
        """
2025-03-15 * "瑞幸咖啡" "拿铁"
    card_last4: "5678"
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
        card_last4="1234",
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
    card_last4: "1234"
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
        card_last4="1234",
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
        card_last4: "1234"
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
        card_last4="1234",
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
        card_last4: "5678"
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
        card_last4="1234",
        account="Liabilities:Credit:HXB",
        provider="alipay",
    )
    result = sieve.match([txn], meta_check=True)

    assert len(result.matched) == 1
    assert len(result.meta_diagnostics) == 1
    d = result.meta_diagnostics[0]
    assert d.severity == "warn"
    assert d.actual == "5678"


# Two same-date, same-amount transactions whose asset/clearing legs share the
# same amount, so the card-swipe posting is not the only viable candidate.
_AMBIGUOUS_LEDGER = """
2030-01-02 * "loc-a" "clearing settlement"
    Assets:Clearing:Settlement  -200.00 CNY
    Assets:Clearing:Acquirer  200.00 CNY

2030-01-02 * "loc-b" "card swipe"
    Liabilities:Credit:HXB  -200.00 CNY
    Assets:Clearing:Settlement  200.00 CNY

1900-01-01 open Liabilities:Credit:HXB
1900-01-01 open Assets:Clearing:Settlement
1900-01-01 open Assets:Clearing:Acquirer
""".strip()


def _stmt_txn(account: str | None) -> Transaction:
    return Transaction(
        date=date(2030, 1, 2),
        amount=Decimal("200.00"),
        currency="CNY",
        description="card swipe",
        account=account,
        provider="hxb_credit",
    )


def test_ambiguous_match_without_account_constraint(tmp_path):
    """No target account -> the greedy pick grabs the clearing leg and warns."""
    sieve = Sieve(SieveConfig(date_tolerance=0))
    sieve.load_ledger(_write_ledger(tmp_path, _AMBIGUOUS_LEDGER))

    result = sieve.match([_stmt_txn(None)])

    assert len(result.matched) == 1
    assert len(result.match_diagnostics) == 1
    d = result.match_diagnostics[0]
    assert d.alternatives == 1
    assert d.chosen_account == "Assets:Clearing:Settlement"
    assert "Ambiguous match" in d.message


def test_account_constraint_disambiguates(tmp_path):
    """A target account constrains matching, so there is no ambiguity."""
    sieve = Sieve(SieveConfig(date_tolerance=0))
    sieve.load_ledger(_write_ledger(tmp_path, _AMBIGUOUS_LEDGER))

    result = sieve.match([_stmt_txn("Liabilities:Credit:HXB")])

    assert len(result.matched) == 1
    assert result.matched[0][1].posting.account == "Liabilities:Credit:HXB"
    assert result.match_diagnostics == []


def test_constrained_same_account_collision_not_ambiguous(tmp_path):
    """Multiple same-amount postings on the constrained account pair up 1:1
    and must not warn (benign interchangeable duplicates)."""
    ledger = _write_ledger(
        tmp_path,
        """
2030-01-02 * "loc-a" "charge one"
    Liabilities:Credit:HXB  -1.00 CNY
    Expenses:Misc  1.00 CNY

2030-01-02 * "loc-b" "charge two"
    Liabilities:Credit:HXB  -1.00 CNY
    Expenses:Misc  1.00 CNY

1900-01-01 open Liabilities:Credit:HXB
1900-01-01 open Expenses:Misc
""".strip(),
    )
    sieve = Sieve(SieveConfig(date_tolerance=0))
    sieve.load_ledger(ledger)

    def one_cny():
        return Transaction(
            date=date(2030, 1, 2),
            amount=Decimal("1.00"),
            currency="CNY",
            description="charge",
            account="Liabilities:Credit:HXB",
            provider="hxb_credit",
        )

    result = sieve.match([one_cny(), one_cny()])

    assert len(result.matched) == 2
    assert result.match_diagnostics == []


def test_ambiguous_check_can_be_disabled(tmp_path):
    sieve = Sieve(SieveConfig(date_tolerance=0))
    sieve.load_ledger(_write_ledger(tmp_path, _AMBIGUOUS_LEDGER))

    result = sieve.match([_stmt_txn(None)], ambiguous_check=False)

    assert len(result.matched) == 1
    assert result.match_diagnostics == []


def test_same_entry_multiple_legs_not_ambiguous(tmp_path):
    """Multiple same-amount legs within one ledger entry are not a conflict."""
    ledger = _write_ledger(
        tmp_path,
        """
2030-01-02 * "loc-c" "split charge"
    Liabilities:Credit:HXB  -200.00 CNY
    Liabilities:Credit:CCB  -200.00 CNY
    Expenses:Misc  400.00 CNY

1900-01-01 open Liabilities:Credit:HXB
1900-01-01 open Liabilities:Credit:CCB
1900-01-01 open Expenses:Misc
""".strip(),
    )
    sieve = Sieve(SieveConfig(date_tolerance=0))
    sieve.load_ledger(ledger)

    result = sieve.match([_stmt_txn(None)])

    assert len(result.matched) == 1
    assert result.match_diagnostics == []
