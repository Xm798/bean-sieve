"""Gross-amount Alipay repayments matched against a fund leg plus rebates."""

from datetime import date, time
from decimal import Decimal
from pathlib import Path

import yaml

from bean_sieve.api import _deduplicate_cross_statements
from bean_sieve.config.schema import Config
from bean_sieve.core.metadata_keys import REBATE_TOPUP_MATCH
from bean_sieve.core.sieve import Sieve, SieveConfig
from bean_sieve.core.types import MatchResult, Transaction
from tests.helpers import load_sieve, run_full_reconcile, write_alipay_csv

CONFIG = """
defaults:
  currency: CNY
  date_tolerance: 2

account_mappings:
  - pattern: "bank-a储蓄卡(1111)"
    account: Assets:Bank:X:1111

providers:
  bank_a:
    accounts:
      "1111": Assets:Bank:X:1111
"""

LEDGER = """
2020-01-01 open Assets:Bank:X:1111 CNY
2020-01-01 open Income:Rebate:Alipay CNY
2020-01-01 open Liabilities:Credit:Y CNY

2030-01-06 * "Credit card repayment"
    Assets:Bank:X:1111       -27.00 CNY
    Income:Rebate:Alipay      -3.00 CNY
    Liabilities:Credit:Y      30.00 CNY
"""


def _run_alipay(tmp_path: Path, row: str) -> MatchResult:
    statement = write_alipay_csv(tmp_path, [row])
    return run_full_reconcile(tmp_path, [statement], CONFIG, LEDGER)


def test_repayment_with_rebate_matches_fund_leg(tmp_path: Path) -> None:
    result = _run_alipay(
        tmp_path,
        "2030-01-06 10:00:00,信用借还,bank-a,,信用卡还款,不计收支,30.00,"
        "bank-a储蓄卡(1111)&红包,还款成功,ORDER0002,,",
    )

    assert len(result.matched) == 1
    assert result.matched[0][1].posting.account == "Assets:Bank:X:1111"
    assert result.missing == []


def test_purchase_with_rebate_keeps_exact_amount(tmp_path: Path) -> None:
    result = _run_alipay(
        tmp_path,
        "2030-01-06 11:00:00,日用百货,merchant-a,,item-a,支出,30.00,"
        "bank-a储蓄卡(1111)&红包,交易成功,ORDER0003,,",
    )

    assert result.matched == []
    assert len(result.missing) == 1


def _load_sieve(tmp_path: Path, rebate: str) -> Sieve:
    return load_sieve(
        tmp_path,
        f"""
2020-01-01 open Assets:A1 CNY
2020-01-01 open Income:Rebate:R CNY
2020-01-01 open Liabilities:L1 CNY

2030-01-06 * "repay"
    Assets:A1          -27.00 CNY
    Income:Rebate:R    -{rebate} CNY
    Liabilities:L1
""",
    )


def _gross_row(flagged: bool) -> Transaction:
    return Transaction(
        date=date(2030, 1, 6),
        amount=Decimal("30.00"),
        currency="CNY",
        description="row",
        provider="test",
        account="Assets:A1",
        metadata={REBATE_TOPUP_MATCH: True} if flagged else {},
    )


def test_sieve_matches_flagged_row_to_fund_leg_plus_rebate(tmp_path: Path) -> None:
    result = _load_sieve(tmp_path, "3.00").match([_gross_row(flagged=True)])

    assert len(result.matched) == 1
    assert result.matched[0][1].posting.account == "Assets:A1"


def test_sieve_keeps_exact_amount_for_unflagged_row(tmp_path: Path) -> None:
    result = _load_sieve(tmp_path, "3.00").match([_gross_row(flagged=False)])

    assert result.matched == []


def test_sieve_rejects_flagged_row_when_rebate_sum_differs(tmp_path: Path) -> None:
    result = _load_sieve(tmp_path, "2.00").match([_gross_row(flagged=True)])

    assert result.matched == []


DEDUP_CONFIG = Config.from_dict(yaml.safe_load(CONFIG))


def _alipay_row(amount: str, flagged: bool) -> Transaction:
    metadata: dict[str, object] = {"method": "bank-a储蓄卡(1111)"}
    if flagged:
        metadata[REBATE_TOPUP_MATCH] = True
    return Transaction(
        date=date(2030, 1, 6),
        time=time(10, 0),
        amount=Decimal(amount),
        currency="CNY",
        description="alipay",
        provider="alipay",
        metadata=metadata,
    )


def _bank_row(amount: str) -> Transaction:
    return Transaction(
        date=date(2030, 1, 6),
        time=time(10, 2),
        amount=Decimal(amount),
        currency="CNY",
        description="bank",
        card_last4="1111",
        provider="bank_a",
    )


def test_dedup_drops_flagged_gross_row_against_net_bank_row() -> None:
    alipay, bank = _alipay_row("30.00", flagged=True), _bank_row("27.00")

    result = _deduplicate_cross_statements([alipay, bank], DEDUP_CONFIG)

    assert result == [bank]


def test_dedup_keeps_unflagged_rows_with_unequal_amounts() -> None:
    alipay, bank = _alipay_row("30.00", flagged=False), _bank_row("27.00")

    result = _deduplicate_cross_statements([alipay, bank], DEDUP_CONFIG)

    assert result == [alipay, bank]


def test_dedup_pairs_exact_rows_before_gross_rows() -> None:
    gross = _alipay_row("30.00", flagged=True)
    small = _alipay_row("5.00", flagged=False).model_copy(update={"time": time(10, 1)})
    bank_small = _bank_row("5.00").model_copy(update={"time": time(10, 1)})
    bank_net = _bank_row("27.00").model_copy(update={"time": time(10, 0)})

    result = _deduplicate_cross_statements(
        [gross, small, bank_small, bank_net], DEDUP_CONFIG
    )

    assert {id(t) for t in result} == {id(bank_small), id(bank_net)}


def test_dedup_gross_row_takes_largest_smaller_direct_row() -> None:
    gross_big, gross_small = (
        _alipay_row("30.00", flagged=True),
        _alipay_row("2.00", flagged=True),
    )
    bank_tiny, bank_net = _bank_row("1.00"), _bank_row("27.00")

    result = _deduplicate_cross_statements(
        [gross_big, gross_small, bank_tiny, bank_net], DEDUP_CONFIG
    )

    assert result == [bank_tiny, bank_net]


def test_dedup_keeps_gross_row_against_smaller_refund_row() -> None:
    gross, refund = _alipay_row("30.00", flagged=True), _bank_row("-5.00")

    result = _deduplicate_cross_statements([gross, refund], DEDUP_CONFIG)

    assert result == [gross, refund]


CARD_LEDGER = """
2020-01-01 open Assets:Card CNY
2020-01-01 open Expenses:Shop CNY
2020-01-01 open Income:Rebate:X CNY
2020-01-01 open Liabilities:L1 CNY

2030-01-06 * "purchase"
    Assets:Card      -30.00 CNY
    Expenses:Shop     30.00 CNY

2030-01-06 * "repay"
    Assets:Card      -27.00 CNY
    Income:Rebate:X   -3.00 CNY
    Liabilities:L1    30.00 CNY
"""


def test_flagged_row_leaves_exact_posting_to_unflagged_row(tmp_path: Path) -> None:
    flagged = _gross_row(flagged=True).model_copy(update={"account": "Assets:Card"})
    purchase = _gross_row(flagged=False).model_copy(update={"account": "Assets:Card"})

    result = load_sieve(tmp_path, CARD_LEDGER).match([flagged, purchase])

    by_row = {id(txn): entry.posting.units.number for txn, entry in result.matched}
    assert by_row == {id(flagged): Decimal("-27.00"), id(purchase): Decimal("-30.00")}
    assert result.missing == []


def test_rebate_reversal_reduces_gross(tmp_path: Path) -> None:
    sieve = load_sieve(
        tmp_path,
        """
2020-01-01 open Assets:A1 CNY
2020-01-01 open Income:Rebate:R CNY
2020-01-01 open Liabilities:L1 CNY

2030-01-06 * "repay"
    Assets:A1          -27.00 CNY
    Income:Rebate:R     -4.00 CNY
    Income:Rebate:R      1.00 CNY
    Liabilities:L1
""",
    )

    result = sieve.match([_gross_row(flagged=True)])

    assert len(result.matched) == 1
    assert result.matched[0][1].posting.account == "Assets:A1"


CASHBACK_LEDGER = """
2020-01-01 open Assets:A1 CNY
2020-01-01 open Income:Cashback:X CNY
2020-01-01 open Liabilities:L1 CNY

2030-01-06 * "repay"
    Assets:A1           -27.00 CNY
    Income:Cashback:X    -3.00 CNY
    Liabilities:L1
"""


def _load_cashback_sieve(tmp_path: Path, config: SieveConfig) -> Sieve:
    ledger_path = tmp_path / "ledger.bean"
    ledger_path.write_text(CASHBACK_LEDGER, encoding="utf-8")
    sieve = Sieve(config)
    sieve.load_ledger(ledger_path)
    return sieve


def test_sieve_counts_configured_rebate_prefix(tmp_path: Path) -> None:
    config = SieveConfig(
        date_tolerance=0, rebate_account_prefixes=("Income:Cashback:",)
    )

    result = _load_cashback_sieve(tmp_path, config).match([_gross_row(flagged=True)])

    assert len(result.matched) == 1
    assert result.matched[0][1].posting.account == "Assets:A1"


def test_sieve_default_prefix_ignores_other_income_accounts(tmp_path: Path) -> None:
    config = SieveConfig(date_tolerance=0)

    result = _load_cashback_sieve(tmp_path, config).match([_gross_row(flagged=True)])

    assert result.matched == []


def test_sieve_prefix_without_colon_covers_account_itself(tmp_path: Path) -> None:
    config = SieveConfig(
        date_tolerance=0, rebate_account_prefixes=("Income:Cashback:X",)
    )

    result = _load_cashback_sieve(tmp_path, config).match([_gross_row(flagged=True)])

    assert len(result.matched) == 1


CASHBACK_FULL_LEDGER = LEDGER.replace("Income:Rebate:Alipay", "Income:Cashback:Alipay")
REPAYMENT_ROW = (
    "2030-01-06 10:00:00,信用借还,bank-a,,信用卡还款,不计收支,30.00,"
    "bank-a储蓄卡(1111)&红包,还款成功,ORDER0002,,"
)


def test_full_reconcile_reads_rebate_prefixes_from_defaults(tmp_path: Path) -> None:
    config = CONFIG.replace(
        "  date_tolerance: 2\n",
        '  date_tolerance: 2\n  rebate_account_prefixes: ["Income:Cashback:"]\n',
    )
    statement = write_alipay_csv(tmp_path, [REPAYMENT_ROW])

    result = run_full_reconcile(tmp_path, [statement], config, CASHBACK_FULL_LEDGER)

    assert len(result.matched) == 1
    assert result.missing == []


def test_full_reconcile_folds_in_mapping_rebate_account(tmp_path: Path) -> None:
    config = CONFIG.replace(
        "    account: Assets:Bank:X:1111\n",
        "    account: Assets:Bank:X:1111\n    rebate_account: Income:Cashback:Alipay\n",
        1,
    )
    statement = write_alipay_csv(tmp_path, [REPAYMENT_ROW])

    result = run_full_reconcile(tmp_path, [statement], config, CASHBACK_FULL_LEDGER)

    assert len(result.matched) == 1
    assert result.missing == []
