"""How far ``full_reconcile`` reaches for a range-less account.

An account a provider covers without giving it a date range is bounded by the
window spanning *every* provider's periods, and the dates of rows carrying none,
so these tests state what the window is: they are the ones to rewrite if it is
ever narrowed per account. The ledger is loaded wider, back to rows traded
before their period, without widening Extra.
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
    # The window spans every provider's periods, so the dated provider's February
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


class InstallmentProvider(BaseProvider):
    """A cycle billing an installment whose purchase date precedes it."""

    provider_id = "fake_hull_installment"
    provider_name = "Fake Hull Installment"
    supported_formats = [".csv"]
    filename_keywords = ["fake-hull-installment"]
    per_card_statement = True

    cycle: tuple[date, date] | None = (date(2030, 2, 11), date(2030, 3, 10))
    # (trade date, posting date, amount, description)
    rows: tuple[tuple[date, date, Decimal, str], ...] = (
        (date(2030, 1, 5), date(2030, 2, 20), Decimal("100.00"), "installment-1-of-3"),
        (date(2030, 2, 20), date(2030, 2, 20), Decimal("30.00"), "purchase-a"),
    )

    def parse(self, file_path: Path) -> list[Transaction]:
        rows = [
            Transaction(
                date=day,
                post_date=posted,
                amount=amount,
                currency="CNY",
                description=description,
                card_last4="0001",
                provider=self.provider_id,
                source_file=file_path,
            )
            for day, posted, amount, description in self.rows
        ]
        self.assign_statement_periods(rows, self.cycle)
        return rows


INSTALLMENT_CONFIG = """
defaults:
  currency: CNY
  date_tolerance: 2

providers:
  fake_hull_installment:
    accounts:
      "0001": Liabilities:Fake:Card
"""


def test_installment_row_neither_widens_extra_nor_goes_unmatched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(PROVIDERS, InstallmentProvider.provider_id, InstallmentProvider)
    config_path = tmp_path / "bean-sieve.yaml"
    config_path.write_text(INSTALLMENT_CONFIG, encoding="utf-8")
    ledger_path = tmp_path / "main.bean"
    ledger_path.write_text(
        """
2020-01-01 open Liabilities:Fake:Card CNY
2020-01-01 open Expenses:FIXME

2030-01-05 * "payee-a" "installment purchase, matched by its row"
  Liabilities:Fake:Card  -100.00 CNY
  Expenses:FIXME

2030-01-20 * "payee-b" "not extra: billed by the cycle before"
  Liabilities:Fake:Card  -40.00 CNY
  Expenses:FIXME

2030-02-20 * "payee-c" "matched by its row"
  Liabilities:Fake:Card  -30.00 CNY
  Expenses:FIXME

2030-03-01 * "payee-d" "extra: inside the cycle, no row"
  Liabilities:Fake:Card  -50.00 CNY
  Expenses:FIXME
""",
        encoding="utf-8",
    )

    result = full_reconcile(
        [write_statement(tmp_path, "fake-hull-installment.csv")],
        ledger_path,
        config_path=config_path,
    )

    assert result.match_result.missing == []
    assert {t.description for t, _ in result.match_result.matched} == {
        "installment-1-of-3",
        "purchase-a",
    }
    assert extra_dates(result) == {(date(2030, 3, 1), "Liabilities:Fake:Card")}


class UncycledStatementProvider(InstallmentProvider):
    """An export of one billing cycle that prints no cycle."""

    provider_id = "fake_hull_uncycled"
    provider_name = "Fake Hull Uncycled"
    filename_keywords = ["fake-hull-uncycled"]
    uncycled_export_is_statement = True

    cycle = None
    rows = (
        (date(2030, 1, 5), date(2030, 3, 8), Decimal("100.00"), "installment-1-of-3"),
        (date(2030, 3, 2), date(2030, 3, 2), Decimal("10.00"), "purchase-a"),
        (date(2030, 3, 20), date(2030, 3, 21), Decimal("20.00"), "purchase-b"),
    )


def test_installment_row_on_an_uncycled_statement_export_bounds_extra_by_posting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        PROVIDERS, UncycledStatementProvider.provider_id, UncycledStatementProvider
    )
    config_path = tmp_path / "bean-sieve.yaml"
    config_path.write_text(
        INSTALLMENT_CONFIG.replace("fake_hull_installment", "fake_hull_uncycled"),
        encoding="utf-8",
    )
    ledger_path = tmp_path / "main.bean"
    ledger_path.write_text(
        """
2020-01-01 open Liabilities:Fake:Card CNY
2020-01-01 open Expenses:FIXME

2030-01-05 * "payee-a" "installment purchase, matched by its row"
  Liabilities:Fake:Card  -100.00 CNY
  Expenses:FIXME

2030-01-20 * "payee-b" "not extra: billed by an earlier cycle"
  Liabilities:Fake:Card  -40.00 CNY
  Expenses:FIXME

2030-03-02 * "payee-c" "matched by its row"
  Liabilities:Fake:Card  -10.00 CNY
  Expenses:FIXME

2030-03-10 * "payee-d" "extra: inside the export, no row"
  Liabilities:Fake:Card  -50.00 CNY
  Expenses:FIXME

2030-03-20 * "payee-e" "matched by its row"
  Liabilities:Fake:Card  -20.00 CNY
  Expenses:FIXME
""",
        encoding="utf-8",
    )

    result = full_reconcile(
        [write_statement(tmp_path, "fake-hull-uncycled.csv")],
        ledger_path,
        config_path=config_path,
    )

    assert result.match_result.missing == []
    assert {t.description for t, _ in result.match_result.matched} == {
        "installment-1-of-3",
        "purchase-a",
        "purchase-b",
    }
    assert extra_dates(result) == {(date(2030, 3, 10), "Liabilities:Fake:Card")}


def test_a_row_traded_before_its_cycle_leaves_a_range_less_account_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The ledger is loaded back to the installment's purchase date, but the
    # undated account's Extra stays within the rows and periods of the run.
    monkeypatch.setitem(PROVIDERS, UndatedProvider.provider_id, UndatedProvider)
    monkeypatch.setitem(PROVIDERS, InstallmentProvider.provider_id, InstallmentProvider)
    config_path = tmp_path / "bean-sieve.yaml"
    config_path.write_text(
        CONFIG.replace(
            '  fake_hull_dated:\n    accounts:\n      "Y": Assets:Fake:Y\n',
            "  fake_hull_installment:\n    accounts:\n"
            '      "0001": Liabilities:Fake:Card\n',
        ),
        encoding="utf-8",
    )
    ledger_path = write_ledger(
        tmp_path,
        """
2020-01-01 open Liabilities:Fake:Card CNY

2030-01-04 * "payee-x" "not extra: loaded, but before the undated rows"
  Assets:Fake:X  -30.00 CNY
  Expenses:FIXME

2030-01-15 * "payee-y" "extra: between the undated rows"
  Assets:Fake:X  -40.00 CNY
  Expenses:FIXME
""",
    )

    result = full_reconcile(
        [
            write_statement(tmp_path, "fake-hull-undated.csv"),
            write_statement(tmp_path, "fake-hull-installment.csv"),
        ],
        ledger_path,
        config_path=config_path,
    )

    assert extra_dates(result) == {(date(2030, 1, 15), ACCOUNT_X)}


class LateUndatedProvider(UndatedProvider):
    """A period-less statement whose only row falls after the card's period."""

    def parse(self, file_path: Path) -> list[Transaction]:
        return [
            Transaction(
                date=date(2030, 4, 20),
                amount=Decimal("60.00"),
                currency="CNY",
                description="row-late",
                card_last4="X",
                provider=self.provider_id,
                source_file=file_path,
            )
        ]


def test_a_period_less_row_outside_the_card_period_is_kept_and_bounds_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The card period ends 02-28; the period-less row on 04-20 is matched, and
    # its own date stretches the window Extra reaches on its account.
    monkeypatch.setitem(PROVIDERS, UndatedProvider.provider_id, LateUndatedProvider)
    monkeypatch.setitem(PROVIDERS, DatedProvider.provider_id, DatedProvider)
    ledger_path = write_ledger(
        tmp_path,
        """
2030-03-25 * "payee-x" "extra: inside the window"
  Assets:Fake:X  -30.00 CNY
  Expenses:FIXME

2030-04-20 * "payee-y" "matched by the period-less row"
  Assets:Fake:X  -60.00 CNY
  Expenses:FIXME

2030-04-25 * "payee-z" "not extra: past the window"
  Assets:Fake:X  -40.00 CNY
  Expenses:FIXME
""",
    )

    result = full_reconcile(
        [
            write_statement(tmp_path, "fake-hull-undated.csv"),
            write_statement(tmp_path, "fake-hull-dated.csv"),
        ],
        ledger_path,
        config_path=write_config(tmp_path),
    )

    assert "row-late" in {t.description for t, _ in result.match_result.matched}
    assert extra_dates(result) == {(date(2030, 3, 25), ACCOUNT_X)}
