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
    def test_create_minimal(self, tmp_path):
        ctx = ReconcileContext(statement_paths=[tmp_path / "test.csv"])
        assert ctx.statement_paths == [tmp_path / "test.csv"]
        assert ctx.ledger_path is None
        assert ctx.config is None
        assert ctx.date_range is None
        assert ctx.extra == {}

    def test_create_full(self, tmp_path):
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
    @pytest.fixture
    def sample_transactions(self) -> list[Transaction]:
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
        return ReconcileContext(
            statement_paths=[tmp_path / "test.csv"],
            ledger_path=tmp_path / "ledger",
        )

    @pytest.fixture
    def sample_result(self, sample_transactions) -> ReconcileResult:
        return ReconcileResult(
            match_result=MatchResult(matched=[], missing=sample_transactions, extra=[]),
            processed=sample_transactions,
        )

    def test_default_pre_reconcile_returns_unchanged(
        self, sample_transactions, sample_context
    ):
        class TestProvider(BaseProvider):
            provider_id = "test"
            provider_name = "Test"
            supported_formats = [".csv"]

            def parse(self, _file_path: Path) -> list[Transaction]:
                return []

        provider = TestProvider()
        result = provider.pre_reconcile(sample_transactions, sample_context)
        assert result == sample_transactions

    def test_default_post_output_returns_unchanged(self, sample_result, sample_context):
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
    @pytest.fixture
    def sample_context(self, tmp_path) -> ReconcileContext:
        return ReconcileContext(
            statement_paths=[tmp_path / "test.csv"],
            ledger_path=tmp_path / "ledger",
        )

    def test_custom_pre_reconcile_transforms_transactions(self, sample_context):
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

    def test_custom_post_output_appends_content(self, sample_context):
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
