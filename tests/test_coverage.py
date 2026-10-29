"""Tests for whole-statement coverage selection (core/coverage.py)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from bean_sieve.core.coverage import Coverage, CoverageEntry, select
from bean_sieve.core.types import Transaction
from tests.helpers import create_bocom_eml

JAN = (date(2030, 1, 1), date(2030, 1, 31))
FEB = (date(2030, 2, 1), date(2030, 2, 28))


def txn(
    day: date, amount: str, card: str = "1111", post: date | None = None
) -> Transaction:
    return Transaction(
        date=day,
        post_date=post,
        amount=Decimal(amount),
        currency="CNY",
        description="payee-a",
        card_last4=card,
        provider="p",
    )


def entry(
    name: str,
    period: tuple[date, date],
    rows: list[Transaction],
    *,
    account: str = "1111",
    itemized: bool = True,
    source: str = "statement",
    clip: bool = False,
    rank: int = 0,
    provider_id: str = "p",
) -> CoverageEntry:
    return CoverageEntry(
        provider_id=provider_id,
        path=Path(name),
        rank=rank,
        coverage=Coverage(
            account=account,
            start=period[0],
            end=period[1],
            rows=rows,
            itemized=itemized,
            source=source,
            clip=clip,
        ),
    )


def kept(entries: list[CoverageEntry]) -> list[Transaction]:
    return select(entries).keep([r for e in entries for r in e.coverage.rows])


def test_no_overlap_keeps_everything() -> None:
    jan = entry("jan.eml", JAN, [txn(date(2030, 1, 5), "10.00")])
    feb = entry("feb.eml", FEB, [txn(date(2030, 2, 5), "20.00")])

    selection = select([jan, feb])

    assert selection.adopted == [jan, feb]
    assert selection.drops == []
    assert kept([jan, feb]) == jan.coverage.rows + feb.coverage.rows


def test_same_rank_overlap_keeps_first_in_order() -> None:
    first = entry("a.eml", JAN, [txn(date(2030, 1, 5), "10.00")])
    second = entry("b.eml", JAN, [txn(date(2030, 1, 5), "10.00")])

    selection = select([first, second])

    assert selection.adopted == [first]
    assert [d.entry for d in selection.drops] == [second]
    assert selection.drops[0].winners == [first]
    assert kept([first, second]) == first.coverage.rows
    assert selection.mismatches == []


def test_rank_beats_order() -> None:
    supplement = entry("sup.eml", JAN, [txn(date(2030, 1, 5), "10.00")], rank=1)
    statement = entry("stm.eml", JAN, [txn(date(2030, 1, 5), "10.00")], rank=0)

    selection = select([supplement, statement])

    assert selection.adopted == [statement]
    assert kept([supplement, statement]) == statement.coverage.rows


def test_summary_only_file_does_not_take_part() -> None:
    summary = entry("summary.eml", JAN, [], itemized=False)
    detail = entry("detail.pdf", JAN, [txn(date(2030, 1, 5), "10.00")], rank=1)

    selection = select([summary, detail])

    assert selection.adopted == [detail]
    assert selection.drops == []
    assert kept([summary, detail]) == detail.coverage.rows


def test_merged_statement_missing_card_keeps_other_card() -> None:
    a_1111 = txn(date(2030, 1, 5), "10.00", card="1111")
    a_2222 = txn(date(2030, 1, 6), "20.00", card="2222")
    b_1111 = txn(date(2030, 1, 5), "10.00", card="1111")
    # b.eml lists only card 1111; it wins that card but says nothing about 2222.
    b = entry("b.eml", JAN, [b_1111], account="1111")
    a1 = entry("a.eml", JAN, [a_1111], account="1111")
    a2 = entry("a.eml", JAN, [a_2222], account="2222")

    assert kept([b, a1, a2]) == [b_1111, a_2222]


def test_accounts_of_different_providers_never_compete() -> None:
    x = entry("x.eml", JAN, [txn(date(2030, 1, 5), "10.00")], provider_id="x")
    y = entry("y.eml", JAN, [txn(date(2030, 1, 5), "10.00")], provider_id="y")

    assert select([x, y]).drops == []


def test_whole_period_loser_drops_rows_outside_overlap_too() -> None:
    # A statement cycle that only partly overlaps the winner is still dropped
    # as a whole: monthly statements are never split by day.
    winner = entry("a.eml", JAN, [txn(date(2030, 1, 20), "10.00")])
    shifted = (date(2030, 1, 15), date(2030, 2, 14))
    loser = entry(
        "b.eml",
        shifted,
        [txn(date(2030, 1, 20), "10.00"), txn(date(2030, 2, 10), "30.00")],
    )

    assert kept([winner, loser]) == winner.coverage.rows


def test_export_is_clipped_by_post_date() -> None:
    statement = entry("stm.eml", JAN, [txn(date(2030, 1, 30), "10.00")])
    inside = txn(date(2030, 1, 30), "10.00", post=date(2030, 1, 31))
    # Traded inside the cycle but posted after it: the statement does not
    # carry it, so the export keeps it.
    posted_after = txn(date(2030, 1, 31), "15.00", post=date(2030, 2, 1))
    outside = txn(date(2030, 2, 10), "20.00")
    export = entry(
        "export.xls",
        (date(2030, 1, 15), date(2030, 2, 15)),
        [inside, posted_after, outside],
        source="export",
        clip=True,
        rank=1,
    )

    selection = select([export, statement])

    assert selection.adopted == [export, statement]
    assert selection.drops[0].rows == [inside]
    assert kept([export, statement]) == [
        posted_after,
        outside,
        *statement.coverage.rows,
    ]


def test_export_yields_to_statements_of_any_rank() -> None:
    # A monthly statement cannot be split, so an export never knocks one out:
    # the February supplement stays whole and the export fills no gap here.
    statement = entry("stm.eml", JAN, [txn(date(2030, 1, 20), "10.00")])
    in_jan = txn(date(2030, 1, 20), "10.00")
    in_feb = txn(date(2030, 2, 10), "20.00")
    export = entry(
        "export.xls",
        (date(2030, 1, 15), date(2030, 2, 15)),
        [in_jan, in_feb],
        source="export",
        clip=True,
        rank=1,
    )
    supplement = entry("sup.pdf", FEB, [txn(date(2030, 2, 10), "20.00")], rank=2)

    entries = [statement, export, supplement]

    expected = statement.coverage.rows + supplement.coverage.rows
    assert [id(r) for r in kept(entries)] == [id(r) for r in expected]


def test_mismatch_reports_count_and_total_over_overlap() -> None:
    first = entry(
        "a.eml", JAN, [txn(date(2030, 1, 5), "10.00"), txn(date(2030, 1, 6), "5.00")]
    )
    second = entry("b.eml", JAN, [txn(date(2030, 1, 5), "10.00")])

    selection = select([first, second])

    [mismatch] = selection.mismatches
    assert mismatch.loser is second
    assert mismatch.winner is first
    assert (mismatch.start, mismatch.end) == JAN
    assert (mismatch.winner_count, mismatch.winner_total) == (2, Decimal("15.00"))
    assert (mismatch.loser_count, mismatch.loser_total) == (1, Decimal("10.00"))


def bocom_eml(tmp_path: Path, name: str, cycle: str, rows: list[str]) -> Path:
    """Write a legacy-layout BOCOM eml for card 1111 with spending rows."""
    body = "".join(
        f"<tr><td></td><td>{d}</td><td>{d}</td><td>1111</td><td>{desc}</td>"
        f"<td>CNY{amt}</td><td>CNY{amt}</td></tr>"
        for d, desc, amt in (r.split(",") for r in rows)
    )
    html = (
        "<html><body><table><tr><td>交通银行信用卡电子账单 6222****1111</td></tr>"
        f"<tr><td>账单周期 {cycle}</td></tr></table>"
        "<table><tr><td>消费、取现、其他费用明细</td></tr>"
        f"<tr><td><table>{body}</table></td></tr></table></body></html>"
    )
    path = tmp_path / name
    path.write_text(create_bocom_eml(html), encoding="utf-8")
    return path


def test_bocom_coverage_uses_statement_cycle(tmp_path: Path) -> None:
    from bean_sieve.providers.banks.credit.bocom import BOCOMCreditProvider

    # The 12/28 row is a settlement delayed from before the cycle; coverage
    # still reports the printed cycle so neighbouring months do not overlap.
    path = bocom_eml(
        tmp_path,
        "jan.eml",
        "2030/01/01-2030/01/31",
        ["01/05,payee-a,10.00", "12/28,payee-b,20.00"],
    )
    provider = BOCOMCreditProvider()

    [coverage] = provider.coverage(path, provider.parse(path))

    assert (coverage.account, coverage.start, coverage.end) == ("1111", *JAN)
    assert len(coverage.rows) == 2


CONFIG = """
defaults:
  currency: CNY
  date_tolerance: 0
providers:
  bocom_credit:
    accounts:
      "1111": Liabilities:CreditCard:BOCOM:1111
"""

LEDGER = """
2030-01-01 open Liabilities:CreditCard:BOCOM:1111 CNY
2030-01-01 open Expenses:Food CNY

2030-01-05 * "payee-a"
  Liabilities:CreditCard:BOCOM:1111  -10.00 CNY
  Expenses:Food
"""


def write_original_and_reissue(tmp_path: Path) -> tuple[Path, Path]:
    """Write config, ledger and two January statements; the reissue adds a row."""
    original = bocom_eml(
        tmp_path, "original.eml", "2030/01/01-2030/01/31", ["01/05,payee-a,10.00"]
    )
    reissue = bocom_eml(
        tmp_path,
        "reissue.eml",
        "2030/01/01-2030/01/31",
        ["01/05,payee-a,10.00", "01/06,payee-c,5.00"],
    )
    (tmp_path / "bean-sieve.yaml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "main.bean").write_text(LEDGER, encoding="utf-8")
    return original, reissue


def _reconcile(tmp_path: Path, keep_overlap: bool):
    from bean_sieve.api import full_reconcile

    original, reissue = write_original_and_reissue(tmp_path)
    return full_reconcile(
        statement_paths=[original, reissue],
        ledger_path=tmp_path / "main.bean",
        config_path=tmp_path / "bean-sieve.yaml",
        keep_overlap=keep_overlap,
    )


def test_full_reconcile_adopts_one_statement_per_period(tmp_path: Path) -> None:
    result = _reconcile(tmp_path, keep_overlap=False)

    assert result.match_result.missing == []
    assert len(result.match_result.matched) == 1
    selection = result.coverage
    assert selection is not None
    assert [e.path.name for e in selection.adopted] == ["original.eml"]
    assert [d.entry.path.name for d in selection.drops] == ["reissue.eml"]
    [mismatch] = selection.mismatches
    assert (mismatch.winner_count, mismatch.loser_count) == (1, 2)


def test_keep_overlap_skips_selection(tmp_path: Path) -> None:
    result = _reconcile(tmp_path, keep_overlap=True)

    assert result.coverage is None
    assert len(result.match_result.missing) == 2


def test_cli_reports_adopted_dropped_and_mismatch(tmp_path: Path) -> None:
    from click.testing import CliRunner

    from bean_sieve.cli import main

    original, reissue = write_original_and_reissue(tmp_path)

    out = (
        CliRunner()
        .invoke(
            main,
            [
                "reconcile",
                str(original),
                str(reissue),
                "-l",
                str(tmp_path / "main.bean"),
                "-c",
                str(tmp_path / "bean-sieve.yaml"),
                "--dry-run",
            ],
            terminal_width=200,
        )
        .output
    )

    assert "Adopted:" in out and "original.eml" in out
    assert "Dropped:" in out and "reissue.eml: 2 row(s)" in out
    assert "Mismatch:" in out
