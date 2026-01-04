"""Tests for core data types."""

from datetime import date, time
from decimal import Decimal

from bean_sieve.core.types import MatchSource, Transaction


class TestTransaction:
    """Tests for Transaction dataclass."""

    def test_create_basic(self):
        """Test creating a basic transaction."""
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
        """Test income transaction (negative amount)."""
        txn = Transaction(
            date=date(2025, 1, 4),
            amount=Decimal("-100.00"),
            currency="CNY",
            description="Income",
        )
        assert txn.is_expense is False
        assert txn.is_income is True

    def test_match_key_with_order_id(self):
        """Test match key prefers order_id."""
        txn = Transaction(
            date=date(2025, 1, 4),
            amount=Decimal("99.00"),
            currency="CNY",
            description="Test",
            order_id="ORDER123",
        )
        assert txn.match_key == ("ORDER123",)

    def test_match_key_without_order_id(self):
        """Test match key fallback to date/amount/card."""
        txn = Transaction(
            date=date(2025, 1, 4),
            amount=Decimal("99.00"),
            currency="CNY",
            description="Test",
            card_suffix="1234",
        )
        assert txn.match_key == (date(2025, 1, 4), Decimal("99.00"), "1234")

    def test_tx_datetime_property(self):
        """Test tx_datetime property."""
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
        """Test tx_datetime property without time."""
        txn = Transaction(
            date=date(2025, 1, 4),
            amount=Decimal("99.00"),
            currency="CNY",
            description="Test",
        )
        assert txn.tx_datetime is None

    def test_to_dict(self):
        """Test serialization to dict."""
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
        """Test deserialization from dict."""
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
        """Test serialization roundtrip."""
        d = sample_transaction.to_dict()
        restored = Transaction.from_dict(d)

        assert restored.date == sample_transaction.date
        assert restored.amount == sample_transaction.amount
        assert restored.currency == sample_transaction.currency
        assert restored.description == sample_transaction.description
        assert restored.payee == sample_transaction.payee
