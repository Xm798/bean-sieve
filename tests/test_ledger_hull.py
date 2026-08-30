"""How far the ledger hull in ``full_reconcile`` reaches for a range-less account.

An account a provider covers without giving it a date range is bounded only by
the hull spanning *every* provider's periods, so these tests state what the hull
is: they are the ones to rewrite if it is ever narrowed per account.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.api import full_reconcile
from bean_sieve.core.types import ReconcileResult, Transaction
from bean_sieve.providers import PROVIDERS
from bean_sieve.providers.base import BaseProvider

ACCOUNT_X = "Assets:Fake:X"

CONFIG = """
defaults:
  currency: CNY
  date_tolerance: 2

providers:
  fake_hull_undated:
    accounts:
      "X": Assets:Fake:X
  fake_hull_dated:
    accounts:
      "Y": Assets:Fake:Y
"""

OPENS = """
2020-01-01 open Assets:Fake:X CNY
2020-01-01 open Assets:Fake:Y CNY
2020-01-01 open Expenses:FIXME
"""


class UndatedProvider(BaseProvider):
    """Stamps no statement period, so its account never gets a range."""

    provider_id = "fake_hull_undated"
    provider_name = "Fake Hull Undated"
    supported_formats = [".csv"]
    filename_keywords = ["fake-hull-undated"]

    def parse(self, file_path: Path) -> list[Transaction]:
        return [
            Transaction(
                date=date(2030, 1, 10),
                amount=Decimal("10.00"),
                currency="CNY",
                description="row-a",
                card_last4="X",
                provider=self.provider_id,
                source_file=file_path,
            ),
            Transaction(
                date=date(2030, 1, 20),
                amount=Decimal("20.00"),
                currency="CNY",
                description="row-b",
                card_last4="X",
                provider=self.provider_id,
                source_file=file_path,
            ),
        ]


class DatedProvider(BaseProvider):
    """Stamps a period far wider than its single row."""

    provider_id = "fake_hull_dated"
    provider_name = "Fake Hull Dated"
    supported_formats = [".csv"]
    filename_keywords = ["fake-hull-dated"]

    def parse(self, file_path: Path) -> list[Transaction]:
        return [
            Transaction(
                date=date(2030, 1, 15),
                amount=Decimal("50.00"),
                currency="CNY",
                description="row-c",
                card_last4="Y",
                provider=self.provider_id,
                source_file=file_path,
                statement_period=(date(2030, 1, 1), date(2030, 2, 28)),
            )
        ]


@pytest.fixture
def fake_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(PROVIDERS, UndatedProvider.provider_id, UndatedProvider)
    monkeypatch.setitem(PROVIDERS, DatedProvider.provider_id, DatedProvider)


def write_statement(tmp_path: Path, name: str) -> Path:
    file_path = tmp_path / name
    file_path.write_text("date,amount\n", encoding="utf-8")
    return file_path


def write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "bean-sieve.yaml"
    config_path.write_text(CONFIG, encoding="utf-8")
    return config_path


def write_ledger(tmp_path: Path, entries: str) -> Path:
    ledger_path = tmp_path / "main.bean"
    ledger_path.write_text(OPENS + entries, encoding="utf-8")
    return ledger_path


def extra_dates(result: ReconcileResult) -> set[tuple[date, str]]:
    return {
        (entry.txn.date, entry.posting.account) for entry in result.match_result.extra
    }


def test_periodless_statement_bounds_extra_by_the_row_span(
    tmp_path: Path,
    fake_providers: None,  # noqa: ARG001
) -> None:
    # Rows 01-10 and 01-20 with no period: the hull is the row span widened by
    # the two-day tolerance, and nothing narrows it further.
    statement = write_statement(tmp_path, "fake-hull-undated.csv")
    ledger_path = write_ledger(
        tmp_path,
        """
2030-01-22 * "payee-x" "extra: last day of the hull"
  Assets:Fake:X  -30.00 CNY
  Expenses:FIXME

2030-01-23 * "payee-y" "not extra: one day past the hull, never loaded"
  Assets:Fake:X  -40.00 CNY
  Expenses:FIXME
""",
    )

    result = full_reconcile(
        [statement], ledger_path, config_path=write_config(tmp_path)
    )

    assert extra_dates(result) == {(date(2030, 1, 22), ACCOUNT_X)}


def test_another_providers_period_widens_a_range_less_account(
    tmp_path: Path,
    fake_providers: None,  # noqa: ARG001
) -> None:
    # The hull spans every provider's periods, so the dated provider's February
    # end date lets Extra reach March in the undated provider's account.
    statements = [
        write_statement(tmp_path, "fake-hull-undated.csv"),
        write_statement(tmp_path, "fake-hull-dated.csv"),
    ]
    ledger_path = write_ledger(
        tmp_path,
        """
2030-03-01 * "payee-x" "extra: last day of the widened hull"
  Assets:Fake:X  -30.00 CNY
  Expenses:FIXME

2030-03-03 * "payee-y" "not extra: one day past the hull, never loaded"
  Assets:Fake:X  -40.00 CNY
  Expenses:FIXME
""",
    )

    result = full_reconcile(statements, ledger_path, config_path=write_config(tmp_path))

    assert extra_dates(result) == {(date(2030, 3, 1), ACCOUNT_X)}
