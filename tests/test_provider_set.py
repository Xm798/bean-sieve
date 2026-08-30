"""Which provider handles which statement path, and which transactions.

``resolve_providers`` is the one place that reconciles a caller-named provider
with per-file detection, so these tests state that contract directly.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.core.types import Transaction
from bean_sieve.providers import PROVIDERS, resolve_providers
from bean_sieve.providers.base import BaseProvider


class ProviderA(BaseProvider):
    provider_id = "fake_set_a"
    provider_name = "Fake Set A"
    supported_formats = [".csv"]
    filename_keywords = ["fake-set-a"]

    def parse(self, file_path: Path) -> list[Transaction]:  # noqa: ARG002
        return []


class ProviderB(BaseProvider):
    provider_id = "fake_set_b"
    provider_name = "Fake Set B"
    supported_formats = [".csv"]
    filename_keywords = ["fake-set-b"]

    def parse(self, file_path: Path) -> list[Transaction]:  # noqa: ARG002
        return []


@pytest.fixture
def fake_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(PROVIDERS, ProviderA.provider_id, ProviderA)
    monkeypatch.setitem(PROVIDERS, ProviderB.provider_id, ProviderB)


def statement(tmp_path: Path, name: str) -> Path:
    file_path = tmp_path / name
    file_path.write_text("date,amount\n", encoding="utf-8")
    return file_path


def transaction(provider_id: str, description: str) -> Transaction:
    return Transaction(
        date=date(2030, 1, 2),
        amount=Decimal("10.00"),
        currency="CNY",
        description=description,
        provider=provider_id,
    )


def test_keeps_input_order_and_dedups_providers(
    tmp_path: Path,
    fake_providers: None,  # noqa: ARG001
) -> None:
    paths = [
        statement(tmp_path, "fake-set-a-1.csv"),
        statement(tmp_path, "fake-set-b.csv"),
        statement(tmp_path, "fake-set-a-2.csv"),
    ]

    pset = resolve_providers(paths)

    assert [path for path, _ in pset.files] == paths
    assert [p.provider_id for _, p in pset.files] == [
        "fake_set_a",
        "fake_set_b",
        "fake_set_a",
    ]
    assert [p.provider_id for p in pset.providers] == ["fake_set_a", "fake_set_b"]
    assert pset.explicit is False


def test_named_provider_overrides_detection(
    tmp_path: Path,
    fake_providers: None,  # noqa: ARG001
) -> None:
    paths = [statement(tmp_path, "fake-set-b.csv")]

    pset = resolve_providers(paths, "fake_set_a")

    assert [p.provider_id for _, p in pset.files] == ["fake_set_a"]
    assert pset.explicit is True


def test_raises_on_the_first_undetectable_path(
    tmp_path: Path,
    fake_providers: None,  # noqa: ARG001
) -> None:
    undetectable = statement(tmp_path, "unknown.not-a-statement")
    paths = [statement(tmp_path, "fake-set-a-1.csv"), undetectable]

    with pytest.raises(
        ValueError, match=f"Cannot auto-detect provider for: {undetectable}"
    ):
        resolve_providers(paths)


def test_group_batches_by_stamped_provider_in_first_seen_order(
    tmp_path: Path,
    fake_providers: None,  # noqa: ARG001
) -> None:
    pset = resolve_providers(
        [
            statement(tmp_path, "fake-set-a-1.csv"),
            statement(tmp_path, "fake-set-b.csv"),
        ]
    )
    transactions = [
        transaction("fake_set_a", "row-a-1"),
        transaction("fake_set_b", "row-b"),
        transaction("fake_set_a", "row-a-2"),
    ]

    grouped = pset.group(transactions)

    assert [
        (provider.provider_id, [t.description for t in batch])
        for provider, batch in grouped
    ] == [
        ("fake_set_a", ["row-a-1", "row-a-2"]),
        ("fake_set_b", ["row-b"]),
    ]


def test_group_gives_every_transaction_to_a_named_provider(
    tmp_path: Path,
    fake_providers: None,  # noqa: ARG001
) -> None:
    pset = resolve_providers([statement(tmp_path, "fake-set-b.csv")], "fake_set_a")
    transactions = [
        transaction("fake_set_b", "row-b"),
        transaction("fake_set_a", "row-a"),
    ]

    grouped = pset.group(transactions)

    assert len(grouped) == 1
    provider, batch = grouped[0]
    assert provider.provider_id == "fake_set_a"
    assert [t.description for t in batch] == ["row-b", "row-a"]
