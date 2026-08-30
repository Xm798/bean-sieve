"""How ``full_reconcile`` fans lifecycle hooks out over the providers in a run.

A run may mix statements from several banks, so these tests state which
provider each hook sees and in what order the hooked transactions come back.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.api import full_reconcile
from bean_sieve.core.types import ReconcileContext, ReconcileResult, Transaction
from bean_sieve.providers import PROVIDERS
from bean_sieve.providers.base import BaseProvider

CONFIG = """
defaults:
  currency: CNY
  date_tolerance: 2
  sort_by_time: null

providers:
  fake_dispatch_a:
    accounts:
      "A": Assets:Fake:A
  fake_dispatch_b:
    accounts:
      "B": Assets:Fake:B
"""

LEDGER = """
2020-01-01 open Assets:Fake:A CNY
2020-01-01 open Assets:Fake:B CNY
2020-01-01 open Expenses:FIXME
"""

# (provider_id, descriptions) of every pre_reconcile call in the run.
PRE_RECONCILE_CALLS: list[tuple[str, list[str]]] = []


class FakeDispatchProvider(BaseProvider):
    """One transaction per file, named after the file so order stays visible."""

    card = ""
    marker = ""

    def parse(self, file_path: Path) -> list[Transaction]:
        return [
            Transaction(
                date=date(2030, 1, 2),
                amount=Decimal("10.00"),
                currency="CNY",
                description=f"row-{file_path.stem}",
                card_last4=self.card,
                provider=self.provider_id,
                source_file=file_path,
            )
        ]

    def pre_reconcile(
        self,
        transactions: list[Transaction],
        context: ReconcileContext,  # noqa: ARG002
    ) -> list[Transaction]:
        PRE_RECONCILE_CALLS.append(
            (self.provider_id, [t.description for t in transactions])
        )
        return transactions

    def post_output(
        self,
        content: str,
        result: ReconcileResult,  # noqa: ARG002
        context: ReconcileContext,  # noqa: ARG002
    ) -> str:
        return content + f"; {self.marker}\n"


class ProviderA(FakeDispatchProvider):
    provider_id = "fake_dispatch_a"
    provider_name = "Fake Dispatch A"
    supported_formats = [".csv"]
    filename_keywords = ["fake-dispatch-a"]
    card = "A"
    marker = "marker-a"


class ProviderB(FakeDispatchProvider):
    provider_id = "fake_dispatch_b"
    provider_name = "Fake Dispatch B"
    supported_formats = [".csv"]
    filename_keywords = ["fake-dispatch-b"]
    card = "B"
    marker = "marker-b"


@pytest.fixture
def fake_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(PROVIDERS, ProviderA.provider_id, ProviderA)
    monkeypatch.setitem(PROVIDERS, ProviderB.provider_id, ProviderB)
    PRE_RECONCILE_CALLS.clear()


def write_statements(tmp_path: Path, *names: str) -> list[Path]:
    paths = []
    for name in names:
        file_path = tmp_path / name
        file_path.write_text("date,amount\n", encoding="utf-8")
        paths.append(file_path)
    return paths


def write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "bean-sieve.yaml"
    config_path.write_text(CONFIG, encoding="utf-8")
    return config_path


def write_ledger(tmp_path: Path) -> Path:
    ledger_path = tmp_path / "main.bean"
    ledger_path.write_text(LEDGER, encoding="utf-8")
    return ledger_path


def run(tmp_path: Path, statements: list[Path]) -> tuple[ReconcileResult, str]:
    output_path = tmp_path / "pending.bean"
    result = full_reconcile(
        statements,
        write_ledger(tmp_path),
        config_path=write_config(tmp_path),
        output_path=output_path,
    )
    return result, output_path.read_text(encoding="utf-8")


def test_pre_reconcile_sees_one_batch_per_provider_in_first_seen_order(
    tmp_path: Path,
    fake_providers: None,  # noqa: ARG001
) -> None:
    statements = write_statements(
        tmp_path,
        "fake-dispatch-a-1.csv",
        "fake-dispatch-b.csv",
        "fake-dispatch-a-2.csv",
    )

    result, _ = run(tmp_path, statements)

    assert PRE_RECONCILE_CALLS == [
        ("fake_dispatch_a", ["row-fake-dispatch-a-1", "row-fake-dispatch-a-2"]),
        ("fake_dispatch_b", ["row-fake-dispatch-b"]),
    ]
    # The hooked transactions come back grouped by provider, not interleaved
    # back into file order.
    assert [t.description for t in result.processed] == [
        "row-fake-dispatch-a-1",
        "row-fake-dispatch-a-2",
        "row-fake-dispatch-b",
    ]


def test_post_output_runs_once_for_a_single_provider_run(
    tmp_path: Path,
    fake_providers: None,  # noqa: ARG001
) -> None:
    statements = write_statements(
        tmp_path, "fake-dispatch-a-1.csv", "fake-dispatch-a-2.csv"
    )

    _, content = run(tmp_path, statements)

    assert content.count("; marker-a") == 1


def test_post_output_runs_for_every_provider_in_a_mixed_run(
    tmp_path: Path,
    fake_providers: None,  # noqa: ARG001
) -> None:
    statements = write_statements(
        tmp_path, "fake-dispatch-a-1.csv", "fake-dispatch-b.csv"
    )

    _, content = run(tmp_path, statements)

    assert content.count("; marker-a") == 1
    assert content.count("; marker-b") == 1
    assert content.index("; marker-a") < content.index("; marker-b")
