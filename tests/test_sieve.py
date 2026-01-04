"""Tests for Sieve matching engine."""

from decimal import Decimal

from bean_sieve.core.sieve import Sieve, SieveConfig


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
