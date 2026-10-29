"""Tests for China Minsheng Bank (民生银行) debit card provider.

All sample data is synthetic: future dates (2030), round amounts, and
placeholder names and accounts. Nothing is copied from real statements.
"""

import logging
from datetime import date, time
from decimal import Decimal
from itertools import accumulate, pairwise
from pathlib import Path

import fitz
import pytest

from bean_sieve.api import full_reconcile
from bean_sieve.providers import auto_detect_provider, get_provider
from bean_sieve.providers.banks.debit.cmbc import CMBCDebitProvider
from tests.helpers import run_full_reconcile

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

# 凭证类型 | 凭证号码 | 交易时间 | 摘要 | 交易金额 | 账户余额 | 现转标志 |
# 交易渠道 | 交易机构 | 对方户名/账号 | 对方行名, wider than the bank's so the
# built-in font fits each cell.
_WIDTHS = [30, 50, 100, 80, 60, 60, 30, 50, 30, 150, 130]
_COL_X = list(accumulate(_WIDTHS, initial=20.0))
_HEADER = [
    "凭证类型",
    "凭证号码",
    "交易时间",
    "摘要",
    "交易金额",
    "账户余额",
    "现转标志",
    "交易渠道",
    "交易机构",
    "对方户名/账号",
    "对方行名",
]
_LINE_HEIGHT = 7
_CARD = "62000000000\n01111"

# Cells wrap at the column width, so a long value spans several lines.
INCOME = [
    "卡",
    _CARD,
    "2030/01/05 09:30:00",
    "代发工资",
    "2,350.00",
    "2,450.00",
    "转账",
    "网络银行",
    "8101",
    "雇主甲有限\n公司/100000001",
    "测试银行甲\n支行",
]
EXPENSE = [
    "卡",
    _CARD,
    "2030/01/10 12:05:00",
    "快捷支付",
    "-20.00",
    "2,430.00",
    "转账",
    "网络银行",
    "8101",
    "商户乙/100000002",
    "",
]
REFUND = [
    "卡",
    _CARD,
    "2030/01/12 08:15:00",
    "快捷支付退款",
    "5.00",
    "2,435.00",
    "转账",
    "网络银行",
    "8101",
    "商户乙/100000002",
    "",
]
TRANSFER = [
    "卡",
    _CARD,
    "2030/01/20 20:00:00",
    "跨行转出",
    "-300.00",
    "2,135.00",
    "转账",
    "手机银行",
    "8102",
    "测试用户/620000000\n0002222",
    "测试银行乙",
]
INTEREST = [
    "",
    "",
    "2030/01/21 02:00:00",
    "结息",
    "0.30",
    "2,135.30",
    "",
    "",
    "",
    "",
    "",
]


def _build_statement(
    path: Path,
    pages: list[list[list[str]]],
    *,
    period: str = "2030/01/01-2030/01/31",
    currency: str = "人民币",
    count: int | None = None,
    ruled_pages: frozenset[int] = frozenset(),
    rule: bool = True,
    stamp: bool = True,
) -> Path:
    """Write a synthetic CMBC statement PDF, one row list per page.

    Pages numbered in ``ruled_pages`` lose their last header cell; ``rule``
    and ``stamp`` drop the underscore and totals lines and the 打印渠道 line.
    """
    doc = fitz.open()
    rows_total = sum(len(rows) for rows in pages)
    for number, rows in enumerate(pages, start=1):
        page = doc.new_page(width=842, height=595)

        def put(x: float, y: float, text: str, page=page) -> None:
            page.insert_text((x, y), text, fontsize=5, fontname="china-s")

        put(380, 30, "个人账户对账单")
        put(22, 60, "客户姓名:测试用户")
        put(300, 60, "客户账号:6200000000001111")
        put(500, 60, f"币  种:{currency}")
        put(22, 75, "开户机构:中国民生银行股份有限公司测试支行")
        put(300, 75, "账户账号:0000000000000000001")
        put(500, 75, f"起止日期:{period}")
        for i, ((left, right), label) in enumerate(
            zip(pairwise(_COL_X), _HEADER, strict=True)
        ):
            if not (number in ruled_pages and i == len(_HEADER) - 1):
                page.draw_rect(fitz.Rect(left, 88, right, 103), width=0.5)
            put(left + 2, 97, label)

        y = 112.0
        for row in rows:
            for left, cell in zip(_COL_X, row, strict=False):
                for i, line in enumerate(cell.split("\n")):
                    put(left + 2, y + i * _LINE_HEIGHT, line)
            y += 20
        if rule:
            put(20, y, "_" * 60)
        if rule and number == len(pages):
            put(
                22,
                y + 10,
                f"支出交易总额:-   收入交易总额:-   合计笔数:"
                f"{rows_total if count is None else count}",
            )
        if stamp:
            put(22, 570, "打印渠道:手机银行")
        put(400, 580, f"第 {number} 页 / 共 {len(pages)} 页")
    doc.save(path)
    doc.close()
    return path


def _write_text_pdf(path: Path, *lines: str) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    for i, line in enumerate(lines):
        page.insert_text((50, 50 + 20 * i), line, fontname="china-s")
    doc.save(path)
    doc.close()
    return path


class TestDetection:
    def test_provider_registration(self):
        provider = get_provider("cmbc_debit")
        assert isinstance(provider, CMBCDebitProvider)
        assert provider.provider_name == "民生银行借记卡"
        assert provider.supported_formats == [".pdf"]
        assert provider.per_card_statement is True

    def test_detects_statement_by_content(self, tmp_path: Path):
        path = _build_statement(tmp_path / "statement.pdf", [[INCOME]])
        detected = auto_detect_provider(path)
        assert detected is not None
        assert detected.provider_id == "cmbc_debit"

    def test_detects_empty_statement(self, tmp_path: Path):
        path = _build_statement(tmp_path / "statement.pdf", [[]])
        assert CMBCDebitProvider.can_handle(path)

    @pytest.mark.parametrize(
        "lines",
        [("个人账户对账单", "开户机构:测试银行"), ("对方行名:中国民生银行测试支行",)],
    )
    def test_needs_title_and_bank(self, tmp_path: Path, lines: tuple[str, ...]):
        path = _write_text_pdf(tmp_path / "statement.pdf", *lines)
        assert not CMBCDebitProvider.can_handle(path)

    def test_example_statements_detect_as_their_own_provider(self):
        """No other example PDF is claimed by cmbc_debit, nor the reverse."""
        paths = sorted(EXAMPLES.glob("*/*.pdf"))
        assert any(p.parent.name == "cmbc_debit" for p in paths)
        for path in paths:
            detected = auto_detect_provider(path)
            assert detected is not None, path
            assert detected.provider_id == path.parent.name, path


class TestParse:
    def test_amount_signs(self, tmp_path: Path):
        path = _build_statement(
            tmp_path / "statement.pdf", [[INCOME, EXPENSE, REFUND, TRANSFER]]
        )
        txns = CMBCDebitProvider().parse(path)
        assert [t.amount for t in txns] == [
            Decimal("-2350.00"),
            Decimal("20.00"),
            Decimal("-5.00"),
            Decimal("300.00"),
        ]
        assert all(t.currency == "CNY" for t in txns)
        assert all(t.card_last4 == "1111" for t in txns)

    def test_row_fields(self, tmp_path: Path):
        path = _build_statement(tmp_path / "statement.pdf", [[INCOME, TRANSFER]])
        income, transfer = CMBCDebitProvider().parse(path)

        assert income.date == date(2030, 1, 5)
        assert income.time == time(9, 30, 0)
        assert income.description == "代发工资"
        assert income.payee == "雇主甲有限公司"
        assert income.metadata == {
            "balance": "2,450.00",
            "cash_transfer": "转账",
            "channel": "网络银行",
            "branch": "8101",
            "counterparty_account": "100000001",
            "counterparty_bank": "测试银行甲支行",
        }
        assert transfer.payee == "测试用户"
        assert transfer.metadata["counterparty_account"] == "6200000000002222"

    def test_row_without_voucher_or_counterparty(self, tmp_path: Path):
        path = _build_statement(tmp_path / "statement.pdf", [[INTEREST]])
        (txn,) = CMBCDebitProvider().parse(path)
        assert txn.description == "结息"
        assert txn.amount == Decimal("-0.30")
        assert txn.payee is None
        assert txn.card_last4 == "1111"
        assert txn.metadata == {"balance": "2,135.30"}

    def test_rows_continue_on_later_pages(self, tmp_path: Path):
        path = _build_statement(
            tmp_path / "statement.pdf", [[INCOME, EXPENSE], [REFUND, TRANSFER]]
        )
        txns = CMBCDebitProvider().parse(path)
        assert [t.date.day for t in txns] == [5, 10, 12, 20]
        assert [t.source_line for t in txns] == [1, 2, 3, 4]

    def test_statement_period_from_header(self, tmp_path: Path):
        path = _build_statement(tmp_path / "statement.pdf", [[EXPENSE, REFUND]])
        txns = CMBCDebitProvider().parse(path)
        assert {t.statement_period for t in txns} == {
            (date(2030, 1, 1), date(2030, 1, 31))
        }

    @pytest.mark.parametrize("stamp", [True, False])
    def test_footer_without_rule_stays_out_of_last_row(
        self, tmp_path: Path, stamp: bool
    ):
        path = _build_statement(
            tmp_path / "statement.pdf", [[INCOME], [EXPENSE]], rule=False, stamp=stamp
        )
        income, expense = CMBCDebitProvider().parse(path)
        assert income.metadata["cash_transfer"] == "转账"
        assert expense.metadata["cash_transfer"] == "转账"
        assert expense.description == "快捷支付"
        assert expense.metadata.get("counterparty_bank") is None

    def test_skips_page_with_unexpected_header(self, tmp_path: Path, caplog):
        path = _build_statement(
            tmp_path / "statement.pdf",
            [[INCOME], [EXPENSE]],
            ruled_pages=frozenset({1}),
        )
        with caplog.at_level(logging.WARNING):
            txns = CMBCDebitProvider().parse(path)
        assert [t.description for t in txns] == ["快捷支付"]
        assert f"Skipping page 1 of {path}: unexpected header layout" in caplog.text

    def test_empty_statement(self, tmp_path: Path):
        path = _build_statement(tmp_path / "statement.pdf", [[]])
        assert CMBCDebitProvider().parse(path) == []

    def test_warns_when_count_differs(self, tmp_path: Path, caplog):
        path = _build_statement(tmp_path / "statement.pdf", [[INCOME]], count=2)
        with caplog.at_level(logging.WARNING):
            assert len(CMBCDebitProvider().parse(path)) == 1
        assert "lists 2 transactions, parsed 1" in caplog.text

    def test_unsupported_currency(self, tmp_path: Path, caplog):
        path = _build_statement(tmp_path / "statement.pdf", [[INCOME]], currency="?")
        with caplog.at_level(logging.WARNING):
            assert CMBCDebitProvider().parse(path) == []
        assert "Unsupported currency" in caplog.text


CONFIG = """
defaults:
  currency: CNY
  date_tolerance: 0

providers:
  cmbc_debit:
    balance: true
    accounts:
      "1111": Assets:Bank:CMBC:1111
"""

LEDGER = """
2029-01-01 open Assets:Bank:CMBC:1111
2029-01-01 open Expenses:Food
2029-01-01 open Expenses:Misc

2029-12-28 * "payee-before-period" "previous period"
  Assets:Bank:CMBC:1111  -6.00 CNY
  Expenses:Misc

2030-01-10 * "商户乙" "快捷支付"
  Assets:Bank:CMBC:1111  -20.00 CNY
  Expenses:Food

2030-01-25 * "payee-in-period" "not on statement"
  Assets:Bank:CMBC:1111  -7.00 CNY
  Expenses:Misc

2030-02-03 * "payee-after-period" "next period"
  Assets:Bank:CMBC:1111  -8.00 CNY
  Expenses:Misc
"""


def test_reconcile_follows_statement_period(tmp_path: Path):
    statement = _build_statement(tmp_path / "statement.pdf", [[INCOME, EXPENSE]])
    result = run_full_reconcile(tmp_path, [statement], CONFIG, LEDGER)

    assert [txn.amount for txn, _ in result.matched] == [Decimal("20.00")]
    assert [t.amount for t in result.missing] == [Decimal("-2350.00")]
    # Inside the printed period but past the last row: still Extra.
    assert [e.txn.date for e in result.extra] == [date(2030, 1, 25)]


def test_balance_assertion_after_period(tmp_path: Path):
    statement = _build_statement(tmp_path / "statement.pdf", [[INCOME, EXPENSE]])
    (tmp_path / "bean-sieve.yaml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "main.bean").write_text(LEDGER, encoding="utf-8")
    out = tmp_path / "pending.bean"
    full_reconcile(
        statement_paths=[statement],
        ledger_path=tmp_path / "main.bean",
        config_path=tmp_path / "bean-sieve.yaml",
        output_path=out,
    )
    content = out.read_text(encoding="utf-8")
    assert "2030-02-01 balance Assets:Bank:CMBC:1111  2430.00 CNY" in content
