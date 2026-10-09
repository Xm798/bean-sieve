"""Extra exemption for the sibling leg of a matched ledger transaction."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from bean_sieve.core.types import MatchResult, Transaction
from tests.helpers import load_sieve, run_full_reconcile, write_alipay_csv

ALIPAY_CONFIG = """
defaults:
  currency: CNY
  date_tolerance: 2

account_mappings:
  - pattern: "余额宝"
    account: Assets:Wallet:Alipay:YuEBao
  - pattern: "余额"
    account: Assets:Wallet:Alipay:Balance
  - pattern: "花呗"
    account: Liabilities:Credit:Huabei

providers:
  alipay:
    accounts:
      "余额": Assets:Wallet:Alipay:Balance
      "余额宝": Assets:Wallet:Alipay:YuEBao
      "花呗": Liabilities:Credit:Huabei
"""

OPENS = """
2020-01-01 open Assets:Wallet:Alipay:Balance CNY
2020-01-01 open Assets:Wallet:Alipay:YuEBao CNY
2020-01-01 open Liabilities:Credit:Huabei CNY
"""


def _run_alipay(tmp_path: Path, row: str, ledger_body: str) -> MatchResult:
    statement = write_alipay_csv(
        tmp_path, [row], period=(date(2030, 1, 1), date(2030, 1, 31))
    )
    return run_full_reconcile(tmp_path, [statement], ALIPAY_CONFIG, OPENS + ledger_body)


def test_alipay_balance_to_yuebao_reports_no_extra(tmp_path: Path) -> None:
    result = _run_alipay(
        tmp_path,
        "2030-01-05 10:00:00,投资理财,payee-a,,余额宝-自动转入,不计收支,10.00,"
        "账户余额,交易成功,2030010510000001,,",
        """
2030-01-05 * "payee-a" "transfer"
    Assets:Wallet:Alipay:YuEBao  10.00 CNY
    Assets:Wallet:Alipay:Balance  -10.00 CNY
""",
    )

    assert len(result.matched) == 1
    assert result.matched[0][1].posting.account == "Assets:Wallet:Alipay:YuEBao"
    assert result.missing == []
    assert result.extra == []


def test_alipay_huabei_repaid_from_yuebao_reports_no_extra(tmp_path: Path) -> None:
    result = _run_alipay(
        tmp_path,
        "2030-01-05 10:00:00,信用借还,payee-b,,花呗自动还款,不计收支,20.00,"
        "余额宝,交易成功,2030010510000002,,",
        """
2030-01-05 * "payee-b" "repay"
    Liabilities:Credit:Huabei  20.00 CNY
    Assets:Wallet:Alipay:YuEBao  -20.00 CNY
""",
    )

    assert len(result.matched) == 1
    assert result.matched[0][1].posting.account == "Liabilities:Credit:Huabei"
    assert result.missing == []
    assert result.extra == []


TRANSFER_OPENS = """
2020-01-01 open Assets:A1 CNY
2020-01-01 open Assets:A2 CNY
2020-01-01 open Assets:B1 CNY
"""


def _row(amount: str, account: str | None = None) -> Transaction:
    return Transaction(
        date=date(2030, 1, 5),
        amount=Decimal(amount),
        currency="CNY",
        description="row",
        provider="test",
        account=account,
    )


A1_TO_B1 = """
2030-01-05 * "payee-c" "transfer"
    Assets:A1  10.00 CNY
    Assets:B1  -10.00 CNY
"""


def test_sibling_leg_in_other_group_stays_extra(tmp_path: Path) -> None:
    sieve = load_sieve(tmp_path, TRANSFER_OPENS + A1_TO_B1)

    result = sieve.match(
        [_row("-10.00", "Assets:A1")],
        covered_accounts=["Assets:A1", "Assets:A2", "Assets:B1"],
        covered_groups=[{"Assets:A1", "Assets:A2"}, {"Assets:B1"}],
    )

    assert len(result.matched) == 1
    assert [e.posting.account for e in result.extra] == ["Assets:B1"]


def test_covered_accounts_form_one_group_without_groups(tmp_path: Path) -> None:
    sieve = load_sieve(tmp_path, TRANSFER_OPENS + A1_TO_B1)

    result = sieve.match(
        [_row("-10.00", "Assets:A1")],
        covered_accounts=["Assets:A1", "Assets:B1"],
    )

    assert len(result.matched) == 1
    assert result.extra == []


def test_each_leg_matches_its_own_row(tmp_path: Path) -> None:
    sieve = load_sieve(tmp_path, TRANSFER_OPENS + A1_TO_B1)

    result = sieve.match(
        [_row("-10.00", "Assets:A1"), _row("10.00", "Assets:B1")],
        covered_accounts=["Assets:A1", "Assets:B1"],
        covered_groups=[{"Assets:A1", "Assets:B1"}],
    )

    assert sorted(m.posting.account for _, m in result.matched) == [
        "Assets:A1",
        "Assets:B1",
    ]
    assert result.missing == []
    assert result.extra == []


def test_unmatched_transaction_reports_every_leg(tmp_path: Path) -> None:
    sieve = load_sieve(tmp_path, TRANSFER_OPENS + A1_TO_B1)

    result = sieve.match(
        [],
        covered_accounts=["Assets:A1", "Assets:B1"],
        covered_groups=[{"Assets:A1", "Assets:B1"}],
    )

    assert sorted(e.posting.account for e in result.extra) == [
        "Assets:A1",
        "Assets:B1",
    ]


SPLIT_OPENS = """
2020-01-01 open Assets:Bal CNY
2020-01-01 open Assets:Bal2 CNY
2020-01-01 open Liabilities:Hb CNY
2020-01-01 open Expenses:Shop CNY
"""


def test_split_payment_leg_of_different_amount_stays_extra(tmp_path: Path) -> None:
    sieve = load_sieve(
        tmp_path,
        SPLIT_OPENS
        + """
2030-01-05 * "payee-d" "split"
    Assets:Bal       -10.00 CNY
    Liabilities:Hb    -5.00 CNY
    Expenses:Shop     15.00 CNY
""",
    )

    result = sieve.match(
        [_row("10.00")],
        covered_accounts=["Assets:Bal", "Liabilities:Hb"],
        covered_groups=[{"Assets:Bal", "Liabilities:Hb"}],
    )

    assert result.matched[0][1].posting.account == "Assets:Bal"
    assert [e.posting.account for e in result.extra] == ["Liabilities:Hb"]


def test_same_account_sibling_stays_extra(tmp_path: Path) -> None:
    sieve = load_sieve(
        tmp_path,
        SPLIT_OPENS
        + """
2030-01-05 * "payee-e" "two charges"
    Assets:Bal       -10.00 CNY
    Assets:Bal        -1.00 CNY
    Expenses:Shop     11.00 CNY
""",
    )

    result = sieve.match(
        [_row("10.00")],
        covered_accounts=["Assets:Bal"],
        covered_groups=[{"Assets:Bal"}],
    )

    assert len(result.matched) == 1
    assert [e.posting.units.number for e in result.extra] == [Decimal("-1.00")]


def test_matched_leg_exempts_at_most_one_sibling(tmp_path: Path) -> None:
    sieve = load_sieve(
        tmp_path,
        SPLIT_OPENS
        + """
2030-01-05 * "payee-f" "fan-out"
    Assets:Bal        10.00 CNY
    Assets:Bal2      -10.00 CNY
    Liabilities:Hb   -10.00 CNY
    Expenses:Shop     10.00 CNY
""",
    )

    result = sieve.match(
        [_row("-10.00")],
        covered_accounts=["Assets:Bal", "Assets:Bal2", "Liabilities:Hb"],
        covered_groups=[{"Assets:Bal", "Assets:Bal2", "Liabilities:Hb"}],
    )

    assert result.matched[0][1].posting.account == "Assets:Bal"
    assert [e.posting.account for e in result.extra] == ["Liabilities:Hb"]
