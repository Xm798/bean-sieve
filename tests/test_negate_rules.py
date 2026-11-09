"""Sign of rows that Alipay's ``negate`` preset rules make inflows, end to end."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.api import full_reconcile
from tests.helpers import run_full_reconcile, write_alipay_csv

CONFIG = """
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
"""

OPENS = """
2020-01-01 open Assets:Wallet:Alipay:Balance CNY
2020-01-01 open Assets:Wallet:Alipay:YuEBao CNY
2020-01-01 open Liabilities:Credit:Huabei CNY
2020-01-01 open Income:Other CNY
2020-01-01 open Expenses:Other CNY
"""

PERIOD = (date(2030, 1, 1), date(2030, 1, 31))


def _row(category: str, item: str, tx_type: str, method: str, status: str) -> str:
    return (
        f"2030-01-05 10:00:00,{category},payee-a,,{item},{tx_type},10.00,"
        f"{method},{status},2030010510000001,,"
    )


# (rule, category, item, method, status, account the ledger books the inflow on)
INFLOW_ROWS = [
    ("yuebao_in", "投资理财", "余额宝-转入", "账户余额", "交易成功", "YuEBao"),
    ("yuebao_income", "投资理财", "余额宝-收益", "", "交易成功", "YuEBao"),
    ("fund_redeem", "投资理财", "基金赎回", "余额宝", "交易成功", "YuEBao"),
    ("fund_sell", "投资理财", "基金卖出至余额宝", "", "交易成功", "YuEBao"),
    ("refund", "退款", "退款-item-a", "余额", "退款成功", "Balance"),
    ("interest", "投资理财", "收益发放", "余额", "交易成功", "Balance"),
    ("huabei_repay", "信用借还", "花呗主动还款", "余额", "交易成功", "Huabei"),
]

ACCOUNTS = {
    "YuEBao": "Assets:Wallet:Alipay:YuEBao",
    "Balance": "Assets:Wallet:Alipay:Balance",
    "Huabei": "Liabilities:Credit:Huabei",
}


@pytest.mark.parametrize("tx_type", ["收入", "不计收支"])
@pytest.mark.parametrize(
    ("category", "item", "method", "status", "account"),
    [row[1:] for row in INFLOW_ROWS],
    ids=[row[0] for row in INFLOW_ROWS],
)
def test_inflow_row_matches_ledger_inflow(
    tmp_path: Path,
    category: str,
    item: str,
    method: str,
    status: str,
    account: str,
    tx_type: str,
) -> None:
    statement = write_alipay_csv(
        tmp_path, [_row(category, item, tx_type, method, status)], period=PERIOD
    )
    ledger = f"""
2030-01-05 * "payee-a" "inflow"
    {ACCOUNTS[account]}  10.00 CNY
    Income:Other  -10.00 CNY
"""

    result = run_full_reconcile(tmp_path, [statement], CONFIG, OPENS + ledger)

    assert len(result.matched) == 1
    assert result.matched[0][0].amount == Decimal("-10.00")
    assert result.missing == []
    assert result.extra == []


def test_user_initiated_refund_matches_ledger_outflow(tmp_path: Path) -> None:
    statement = write_alipay_csv(
        tmp_path,
        [_row("退款", "退款", "支出", "余额", "交易成功")],
        period=PERIOD,
    )
    ledger = """
2030-01-05 * "payee-a" "refund paid back"
    Assets:Wallet:Alipay:Balance  -10.00 CNY
    Expenses:Other  10.00 CNY
"""

    result = run_full_reconcile(tmp_path, [statement], CONFIG, OPENS + ledger)

    assert len(result.matched) == 1
    assert result.matched[0][0].amount == Decimal("10.00")
    assert result.extra == []


@pytest.mark.parametrize("tx_type", ["收入", "不计收支"])
def test_missing_refund_is_written_as_inflow(tmp_path: Path, tx_type: str) -> None:
    statement = write_alipay_csv(
        tmp_path,
        [_row("退款", "退款-item-a", tx_type, "余额", "退款成功")],
        period=PERIOD,
    )
    (tmp_path / "bean-sieve.yaml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "main.bean").write_text(OPENS, encoding="utf-8")

    result = full_reconcile(
        statement_paths=[statement],
        ledger_path=tmp_path / "main.bean",
        config_path=tmp_path / "bean-sieve.yaml",
    )

    assert [t.amount for t in result.processed] == [Decimal("-10.00")]
