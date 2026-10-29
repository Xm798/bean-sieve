"""Tests for Shanghai Pudong Development Bank (浦发银行) debit card provider.

All sample data is synthetic: future dates (2030), round amounts, and
placeholder names and accounts. Nothing is copied from real statements.
"""

from datetime import date, time
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

from bean_sieve.providers import get_provider
from bean_sieve.providers.banks.debit.spdb import SPDBDebitProvider
from tests.helpers import run_full_reconcile

# Columns of the ruled transaction table: 交易日期 | 交易时间 | 交易账号 |
# 交易名称 | 交易金额 | 账户余额 | 对手姓名 | 对手账号 | 交易摘要. They are wider
# than the real ones so the synthetic font fits each cell.
_COL_X = [20 + 62 * i for i in range(10)]
_HEADER_CELLS = [
    "交易日期\nDate",
    "交易时间\nTime",
    "交易账号\nTransaction\nAccount",
    "交易名称\nTransaction\nName",
    "交易金额\nTransaction\nAmount",
    "账户余额\nBalance",
    "对手姓名\nCounter\nParty",
    "对手账号\nOpponent\nAccount",
    "交易摘要\nSummary",
]
_LINE_HEIGHT = 9
_ACCOUNT = "6200000000001234"

# Cells wrap at the column width, so a long value spans several lines.
INCOME = [
    "20300105",
    "093000",
    "62000000000\n01234",
    "测试汇入\n30010500000\n001",
    "110.00",
    "110.00",
    "测试用户",
    "620000000\n0005678",
    "摘要甲",
]
EXPENSE = [
    "20300110",
    "120500",
    "62000000000\n01234",
    "测试支付-渠\n道",
    "-20.00",
    "90.00",
    "商户甲有限\n公司",
    "100000001",
    "",
]
REFUND = [
    "20300112",
    "081500",
    "62000000000\n01234",
    "测试支付-渠\n道退款",
    "5.00",
    "95.00",
    "商户甲有限\n公司",
    "100000001",
    "",
]
TRANSFER = [
    "20300120",
    "200000",
    "62000000000\n01234",
    "测试还款\n5678",
    "-30.00",
    "65.00",
    "测试用户",
    "400000000\n0005678",
    "",
]


def _build_statement(
    path: Path,
    pages: list[list[list[str]]],
    period: str = "20300101-20300131",
) -> Path:
    """Write a synthetic SPDB transaction-statement PDF, one row list per page."""
    import fitz

    doc = fitz.open()
    for page_num, rows in enumerate(pages):
        page = doc.new_page(width=595, height=842)

        def put(x: float, y: float, text: str, page=page) -> None:
            page.insert_text((x, y), text, fontsize=5, fontname="china-s")

        y = 60.0
        if page_num == 0:
            put(138, 50, "上海浦东发展银行个人客户交易流水专用回单")
            put(126, 70, "Transaction Statement of Shanghai Pudong Development Bank")
            put(90, 92, f"户名: 测试用户  账号: {_ACCOUNT}  起止日期: {period}")
            put(90, 112, "Name: TEST USER  Account No:  Start & End Date")
            put(90, 132, "币种: 人民币  钞汇标志: 钞户  交易类型: 全部")
            put(90, 152, "Currency: CNY  Cash/Exchange: Cash  Transaction Type: ALL")
            y = 161.0
            table_rows = [_HEADER_CELLS, *rows]
        else:
            table_rows = rows

        for cells in table_rows:
            height = _LINE_HEIGHT * max(c.count("\n") + 1 for c in cells) + 6
            for (left, right), cell in zip(pairwise(_COL_X), cells, strict=True):
                page.draw_rect(fitz.Rect(left, y, right, y + height), width=0.5)
                for i, line in enumerate(cell.split("\n")):
                    put(left + 2, y + 9 + i * _LINE_HEIGHT, line)
            y += height
        put(250, 800, f"第{page_num + 1}页/共{len(pages)}页，Page 1 of 1")

    doc.save(path)
    doc.close()
    return path


class TestDetection:
    def test_provider_registration(self):
        provider = get_provider("spdb_debit")
        assert isinstance(provider, SPDBDebitProvider)
        assert provider.provider_name == "浦发银行借记卡"
        assert provider.supported_formats == [".pdf"]

    def test_detects_statement_by_content(self, tmp_path: Path):
        path = _build_statement(tmp_path / "statement.pdf", [[INCOME]])
        assert SPDBDebitProvider.can_handle(path)

    def test_rejects_other_pdf(self, tmp_path: Path):
        import fitz

        path = tmp_path / "statement.pdf"
        doc = fitz.open()
        doc.new_page().insert_text((50, 50), "Monthly Statement")
        doc.save(path)
        doc.close()
        assert not SPDBDebitProvider.can_handle(path)

    def test_rejects_other_formats(self, tmp_path: Path):
        path = tmp_path / "statement.xls"
        path.write_bytes(b"")
        assert not SPDBDebitProvider.can_handle(path)


class TestParse:
    def test_amount_signs(self, tmp_path: Path):
        path = _build_statement(
            tmp_path / "statement.pdf", [[INCOME, EXPENSE, REFUND, TRANSFER]]
        )
        txns = SPDBDebitProvider().parse(path)
        assert [t.amount for t in txns] == [
            Decimal("-110.00"),
            Decimal("20.00"),
            Decimal("-5.00"),
            Decimal("30.00"),
        ]
        assert all(t.currency == "CNY" for t in txns)
        assert all(t.card_last4 == "1234" for t in txns)

    def test_row_fields(self, tmp_path: Path):
        path = _build_statement(tmp_path / "statement.pdf", [[INCOME, EXPENSE]])
        income, expense = SPDBDebitProvider().parse(path)

        assert income.date == date(2030, 1, 5)
        assert income.time == time(9, 30, 0)
        assert income.description == "测试汇入"
        assert income.payee == "测试用户"
        assert income.metadata == {
            "balance": "110.00",
            "counterparty_account": "6200000000005678",
            "reference": "30010500000001",
            "summary": "摘要甲",
        }

        assert expense.description == "测试支付-渠道"
        assert expense.payee == "商户甲有限公司"
        assert expense.metadata == {
            "balance": "90.00",
            "counterparty_account": "100000001",
        }

    def test_card_suffix_in_name_is_reference(self, tmp_path: Path):
        path = _build_statement(tmp_path / "statement.pdf", [[TRANSFER]])
        (txn,) = SPDBDebitProvider().parse(path)
        assert txn.description == "测试还款"
        assert txn.metadata["reference"] == "5678"

    def test_rows_continue_on_later_pages(self, tmp_path: Path):
        path = _build_statement(
            tmp_path / "statement.pdf", [[INCOME, EXPENSE], [REFUND, TRANSFER]]
        )
        txns = SPDBDebitProvider().parse(path)
        assert [t.date.day for t in txns] == [5, 10, 12, 20]
        assert len({t.source_line for t in txns}) == 4

    def test_statement_period_from_header(self, tmp_path: Path):
        path = _build_statement(
            tmp_path / "statement.pdf", [[INCOME, EXPENSE]], period="20300101-20300131"
        )
        txns = SPDBDebitProvider().parse(path)
        assert {t.statement_period for t in txns} == {
            (date(2030, 1, 1), date(2030, 1, 31))
        }


CONFIG = """
defaults:
  currency: CNY
  date_tolerance: 0

providers:
  spdb_debit:
    accounts:
      "1234": Assets:Bank:SPDB:1234
"""

LEDGER = """
2029-01-01 open Assets:Bank:SPDB:1234
2029-01-01 open Expenses:Food
2029-01-01 open Expenses:Misc

2029-12-28 * "payee-before-period" "previous period"
  Assets:Bank:SPDB:1234  -6.00 CNY
  Expenses:Misc

2030-01-10 * "商户甲有限公司" "测试支付-渠道"
  Assets:Bank:SPDB:1234  -20.00 CNY
  Expenses:Food

2030-01-25 * "payee-in-period" "not on statement"
  Assets:Bank:SPDB:1234  -7.00 CNY
  Expenses:Misc

2030-02-03 * "payee-after-period" "next period"
  Assets:Bank:SPDB:1234  -8.00 CNY
  Expenses:Misc
"""


def test_reconcile_follows_statement_period(tmp_path: Path):
    statement = _build_statement(tmp_path / "statement.pdf", [[INCOME, EXPENSE]])
    result = run_full_reconcile(tmp_path, [statement], CONFIG, LEDGER)

    assert [txn.amount for txn, _ in result.matched] == [Decimal("20.00")]
    assert [t.amount for t in result.missing] == [Decimal("-110.00")]
    # Inside the printed period but past the last row: still Extra.
    assert [e.txn.date for e in result.extra] == [date(2030, 1, 25)]
