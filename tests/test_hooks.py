"""Tests for provider lifecycle hooks."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.core.types import (
    MatchResult,
    ReconcileContext,
    ReconcileResult,
    Transaction,
)
from bean_sieve.providers.base import BaseProvider


class TestReconcileContext:
    """Tests for ReconcileContext dataclass."""

    def test_create_minimal(self, tmp_path):
        """Test creating context with minimal required fields."""
        ctx = ReconcileContext(statement_paths=[tmp_path / "test.csv"])
        assert ctx.statement_paths == [tmp_path / "test.csv"]
        assert ctx.ledger_path is None
        assert ctx.config is None
        assert ctx.date_range is None
        assert ctx.extra == {}

    def test_create_full(self, tmp_path):
        """Test creating context with all fields."""
        ctx = ReconcileContext(
            statement_paths=[tmp_path / "test.csv"],
            ledger_path=tmp_path / "ledger",
            config=None,
            date_range=(date(2025, 1, 1), date(2025, 1, 31)),
            account_filter="Assets:",
            output_path=tmp_path / "output.bean",
            extra={"custom_key": "custom_value"},
        )
        assert ctx.date_range == (date(2025, 1, 1), date(2025, 1, 31))
        assert ctx.account_filter == "Assets:"
        assert ctx.extra["custom_key"] == "custom_value"


class TestBaseProviderHooks:
    """Tests for BaseProvider lifecycle hooks."""

    @pytest.fixture
    def sample_transactions(self) -> list[Transaction]:
        """Create sample transactions for testing."""
        return [
            Transaction(
                date=date(2025, 1, 15),
                amount=Decimal("100.00"),
                currency="CNY",
                description="Test transaction 1",
            ),
            Transaction(
                date=date(2025, 1, 16),
                amount=Decimal("200.00"),
                currency="CNY",
                description="Test transaction 2",
            ),
        ]

    @pytest.fixture
    def sample_context(self, tmp_path) -> ReconcileContext:
        """Create sample context for testing."""
        return ReconcileContext(
            statement_paths=[tmp_path / "test.csv"],
            ledger_path=tmp_path / "ledger",
        )

    @pytest.fixture
    def sample_result(self, sample_transactions) -> ReconcileResult:
        """Create sample reconcile result for testing."""
        return ReconcileResult(
            match_result=MatchResult(matched=[], missing=sample_transactions, extra=[]),
            processed=sample_transactions,
        )

    def test_default_pre_reconcile_returns_unchanged(
        self, sample_transactions, sample_context
    ):
        """Test that default pre_reconcile returns transactions unchanged."""

        class TestProvider(BaseProvider):
            provider_id = "test"
            provider_name = "Test"
            supported_formats = [".csv"]

            def parse(self, _file_path: Path) -> list[Transaction]:
                return []

        provider = TestProvider()
        result = provider.pre_reconcile(sample_transactions, sample_context)
        assert result == sample_transactions

    def test_default_post_reconcile_returns_unchanged(
        self, sample_result, sample_context
    ):
        """Test that default post_reconcile returns result unchanged."""

        class TestProvider(BaseProvider):
            provider_id = "test"
            provider_name = "Test"
            supported_formats = [".csv"]

            def parse(self, _file_path: Path) -> list[Transaction]:
                return []

        provider = TestProvider()
        result = provider.post_reconcile(sample_result, sample_context)
        assert result == sample_result

    def test_default_post_output_returns_unchanged(self, sample_result, sample_context):
        """Test that default post_output returns content unchanged."""

        class TestProvider(BaseProvider):
            provider_id = "test"
            provider_name = "Test"
            supported_formats = [".csv"]

            def parse(self, _file_path: Path) -> list[Transaction]:
                return []

        provider = TestProvider()
        content = "2025-01-15 * Test\n  Assets:Bank  100 CNY"
        result = provider.post_output(content, sample_result, sample_context)
        assert result == content


class TestCustomHooks:
    """Tests for custom provider hook implementations."""

    @pytest.fixture
    def sample_context(self, tmp_path) -> ReconcileContext:
        """Create sample context for testing."""
        return ReconcileContext(
            statement_paths=[tmp_path / "test.csv"],
            ledger_path=tmp_path / "ledger",
        )

    def test_custom_pre_reconcile_transforms_transactions(self, sample_context):
        """Test that custom pre_reconcile can transform transactions."""

        class TransformingProvider(BaseProvider):
            provider_id = "transforming"
            provider_name = "Transforming"
            supported_formats = [".csv"]

            def parse(self, _file_path: Path) -> list[Transaction]:
                return []

            def pre_reconcile(
                self,
                transactions: list[Transaction],
                _context: ReconcileContext,
            ) -> list[Transaction]:
                # Add a tag to all transactions
                for txn in transactions:
                    txn.tags.append("transformed")
                return transactions

        provider = TransformingProvider()
        transactions = [
            Transaction(
                date=date(2025, 1, 15),
                amount=Decimal("100.00"),
                currency="CNY",
                description="Test",
            ),
        ]

        result = provider.pre_reconcile(transactions, sample_context)
        assert "transformed" in result[0].tags

    def test_custom_post_reconcile_enriches_result(self, sample_context):
        """Test that custom post_reconcile can enrich result."""

        class EnrichingProvider(BaseProvider):
            provider_id = "enriching"
            provider_name = "Enriching"
            supported_formats = [".csv"]

            def parse(self, _file_path: Path) -> list[Transaction]:
                return []

            def post_reconcile(
                self,
                result: ReconcileResult,
                _context: ReconcileContext,
            ) -> ReconcileResult:
                # Set a specific account for all processed transactions
                for txn in result.processed:
                    if not txn.account:
                        txn.account = "Assets:Bank:Default"
                return result

        provider = EnrichingProvider()
        transactions = [
            Transaction(
                date=date(2025, 1, 15),
                amount=Decimal("100.00"),
                currency="CNY",
                description="Test",
            ),
        ]
        result = ReconcileResult(
            match_result=MatchResult(matched=[], missing=transactions, extra=[]),
            processed=transactions,
        )

        enriched = provider.post_reconcile(result, sample_context)
        assert enriched.processed[0].account == "Assets:Bank:Default"

    def test_custom_post_output_appends_content(self, sample_context):
        """Test that custom post_output can append content."""

        class AppendingProvider(BaseProvider):
            provider_id = "appending"
            provider_name = "Appending"
            supported_formats = [".csv"]

            def parse(self, _file_path: Path) -> list[Transaction]:
                return []

            def post_output(
                self,
                content: str,
                _result: ReconcileResult,
                _context: ReconcileContext,
            ) -> str:
                # Append settlement entry
                settlement = "\n\n; Settlement entry\n2025-01-31 * Settlement"
                return content + settlement

        provider = AppendingProvider()
        result = ReconcileResult(
            match_result=MatchResult(matched=[], missing=[], extra=[]),
            processed=[],
        )

        content = "; Original content"
        output = provider.post_output(content, result, sample_context)

        assert "; Original content" in output
        assert "; Settlement entry" in output
        assert "2025-01-31 * Settlement" in output
