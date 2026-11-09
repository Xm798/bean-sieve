from datetime import date, time
from decimal import Decimal

from bean_sieve.core.types import MatchSource, Transaction


class TestTransaction:
    def test_create_basic(self):
        txn = Transaction(
            date=date(2025, 1, 4),
            amount=Decimal("99.00"),
            currency="CNY",
            description="Test transaction",
        )
        assert txn.date == date(2025, 1, 4)
        assert txn.amount == Decimal("99.00")
        assert txn.currency == "CNY"
        assert txn.is_expense is True
        assert txn.is_income is False

    def test_income_transaction(self):
        txn = Transaction(
            date=date(2025, 1, 4),
            amount=Decimal("-100.00"),
            currency="CNY",
            description="Income",
        )
        assert txn.is_expense is False
        assert txn.is_income is True

    def test_match_key_with_order_id(self):
        txn = Transaction(
            date=date(2025, 1, 4),
            amount=Decimal("99.00"),
            currency="CNY",
            description="Test",
            order_id="ORDER123",
        )
        assert txn.match_key == ("ORDER123",)

    def test_match_key_without_order_id(self):
        txn = Transaction(
            date=date(2025, 1, 4),
            amount=Decimal("99.00"),
            currency="CNY",
            description="Test",
            card_last4="1234",
        )
        assert txn.match_key == (date(2025, 1, 4), Decimal("99.00"), "1234")

    def test_tx_datetime_property(self):
        txn = Transaction(
            date=date(2025, 1, 4),
            time=time(14, 30, 0),
            amount=Decimal("99.00"),
            currency="CNY",
            description="Test",
        )
        dt = txn.tx_datetime
        assert dt is not None
        assert dt.date() == date(2025, 1, 4)
        assert dt.time() == time(14, 30, 0)

    def test_tx_datetime_property_no_time(self):
        txn = Transaction(
            date=date(2025, 1, 4),
            amount=Decimal("99.00"),
            currency="CNY",
            description="Test",
        )
        assert txn.tx_datetime is None

    def test_to_dict(self):
        txn = Transaction(
            date=date(2025, 1, 4),
            amount=Decimal("99.00"),
            currency="CNY",
            description="Test",
            payee="Test Payee",
            match_source=MatchSource.RULE,
        )
        d = txn.to_dict()
        assert d["date"] == "2025-01-04"
        assert d["amount"] == "99.00"
        assert d["payee"] == "Test Payee"
        assert d["match_source"] == "rule"

    def test_from_dict(self):
        d = {
            "date": "2025-01-04",
            "amount": "99.00",
            "currency": "CNY",
            "description": "Test",
            "payee": "Test Payee",
            "match_source": "rule",
        }
        txn = Transaction.from_dict(d)
        assert txn.date == date(2025, 1, 4)
        assert txn.amount == Decimal("99.00")
        assert txn.payee == "Test Payee"
        assert txn.match_source == MatchSource.RULE

    def test_roundtrip(self, sample_transaction):
        d = sample_transaction.to_dict()
        restored = Transaction.from_dict(d)

        assert restored.date == sample_transaction.date
        assert restored.amount == sample_transaction.amount
        assert restored.currency == sample_transaction.currency
        assert restored.description == sample_transaction.description
        assert restored.payee == sample_transaction.payee


def test_meta_diagnostic_construction():
    from bean_sieve.core.types import MetaDiagnostic

    d = MetaDiagnostic(
        severity="hint",
        file="books/2025/q1.bean",
        line=1234,
        account="Liabilities:Credit:HXB",
        key="card_last4",
        expected="1234",
        actual=None,
        message='books/2025/q1.bean:1234  hint  missing posting meta `card_last4: "1234"` on Liabilities:Credit:HXB',
    )
    assert d.severity == "hint"
    assert d.actual is None


def test_match_result_meta_diagnostics_default_empty():
    from bean_sieve.core.types import MatchResult

    mr = MatchResult()
    assert mr.meta_diagnostics == []


def test_roundtrip_keeps_exchange_links_and_decimal_metadata():
    import json

    txn = Transaction(
        date=date(2030, 1, 2),
        amount=Decimal("700.00"),
        currency="CNY",
        description="exchange",
        price_amount=Decimal("100.00"),
        price_currency="USD",
        links=["order-a"],
        statement_period=(date(2030, 1, 1), date(2030, 1, 31)),
        metadata={"_original_amount": Decimal("10.50"), "posted": date(2030, 1, 3)},
    )

    restored = Transaction.from_dict(json.loads(json.dumps(txn.to_dict())))

    assert restored.price_amount == Decimal("100.00")
    assert restored.price_currency == "USD"
    assert restored.links == ["order-a"]
    assert restored.statement_period == txn.statement_period
    assert restored.metadata == {"_original_amount": "10.50", "posted": "2030-01-03"}


def test_cli_parse_json_serializes_decimal_metadata(tmp_path):
    import json
    from pathlib import Path

    from click.testing import CliRunner

    from bean_sieve.cli import main

    statement = (
        Path(__file__).parent.parent / "examples" / "cmb_credit" / "reissue-2030-02.pdf"
    )

    out = tmp_path / "rows.json"

    result = CliRunner().invoke(
        main, ["parse", str(statement), "-f", "json", "-o", str(out)]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text(encoding="utf-8"))


def test_cli_parse_json_on_stdout_is_valid_json():
    import json
    from pathlib import Path

    from click.testing import CliRunner

    from bean_sieve.cli import main

    statement = Path(__file__).parent.parent / "examples" / "alipay"
    statement = next(p for p in statement.iterdir() if p.suffix == ".csv")

    result = CliRunner().invoke(
        main, ["parse", str(statement), "-f", "json"], terminal_width=40
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)
    assert "Total:" in result.stderr
