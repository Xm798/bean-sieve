"""Tests for China CITIC Bank (中信银行) debit card statement provider."""

from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest
import xlwt

from bean_sieve.api import full_reconcile
from bean_sieve.providers import auto_detect_provider, get_provider
from bean_sieve.providers.banks.debit.cncb import CNCBDebitProvider

HEADERS = [
    "交易日期",
    "交易时间",
    "收入金额",
    "支出金额",
    "账户余额",
    "对方名称",
    "对方账号",
    "受理机构",
    "摘要",
    "交易流水号",
    "交易卡号",
    "状态",
]

TITLE = "账号：600000 **** ** 1111 人民币 起始日期:20300101 截止日期:20300131"

# Newest first, as the bank exports them.
ROWS = [
    ["20300120", "18:00:00", "__", "1,300.00", "710.00", "payee-c",
     "600001 **** **** * 3333", "inst-c", "转账", "REF0003", "--", "完成"],
    ["20300110", "09:30:00", "__", "30.00", "2,010.00", "payee-b",
     "", "inst-b", "银联二维码支付-shop-b", "REF0002", "90000                   01", "完成"],
    ["20300105", "12:00:00", "2,040.00", "__", "2,040.00", "payee-a",
     "600001 **** ** 2222", "", "跨行转入", "REF0001", "--", "完成"],
]  # fmt: skip


def create_xls(
    tmp_path: Path,
    rows: list[list] | None = None,
    title: str = TITLE,
    headers: list[str] = HEADERS,
    filename: str = "statement.xls",
) -> Path:
    wb = xlwt.Workbook(encoding="utf-8")
    ws = wb.add_sheet("Sheet1")
    ws.write(0, 0, title)
    for col, header in enumerate(headers):
        ws.write(1, col, header)
    for r, row in enumerate(ROWS if rows is None else rows, 2):
        for c, value in enumerate(row):
            ws.write(r, c, value)
    path = tmp_path / filename
    wb.save(str(path))
    return path


@pytest.fixture
def provider() -> CNCBDebitProvider:
    return CNCBDebitProvider()


class TestParse:
    def test_rows_in_booking_order(self, tmp_path, provider):
        txns = provider.parse(create_xls(tmp_path))
        assert [t.date for t in txns] == [
            date(2030, 1, 5),
            date(2030, 1, 10),
            date(2030, 1, 20),
        ]
        assert [t.source_line for t in txns] == [5, 4, 3]

    def test_sign_convention(self, tmp_path, provider):
        income, expense, transfer = provider.parse(create_xls(tmp_path))
        assert income.amount == Decimal("-2040.00")
        assert expense.amount == Decimal("30.00")
        assert transfer.amount == Decimal("1300.00")

    def test_fields(self, tmp_path, provider):
        _, expense, transfer = provider.parse(create_xls(tmp_path))
        assert expense.time == time(9, 30)
        assert expense.currency == "CNY"
        assert expense.card_last4 == "1111"
        assert expense.payee == "payee-b"
        assert expense.description == "银联二维码支付-shop-b"
        assert expense.order_id is None
        assert expense.metadata == {
            "summary": "银联二维码支付-shop-b",
            "balance": "2,010.00",
            "institution": "inst-b",
            "reference": "REF0002",
            "card_number": "90000 01",
            "status": "完成",
        }
        assert transfer.metadata["counterparty_account"] == "600001 **** **** * 3333"
        assert "card_number" not in transfer.metadata

    def test_statement_period_from_title(self, tmp_path, provider):
        txns = provider.parse(create_xls(tmp_path))
        assert {t.statement_period for t in txns} == {
            (date(2030, 1, 1), date(2030, 1, 31))
        }

    def test_empty_statement(self, tmp_path, provider):
        assert provider.parse(create_xls(tmp_path, rows=[])) == []

    def test_skips_rows_without_amount_or_date(self, tmp_path, provider):
        rows = [
            ["20300102", "10:00:00", "__", "__", "7.00", "", "", "", "x", "", "--", "完成"],
            ["", "", "", "", "", "", "", "", "", "", "", ""],
            ROWS[0],
        ]  # fmt: skip
        txns = provider.parse(create_xls(tmp_path, rows=rows))
        assert [t.amount for t in txns] == [Decimal("1300.00")]

    def test_numeric_cells(self, tmp_path, provider):
        rows = [
            [20300103, "08:00:00", "", 4321.0, 8642.0, "payee-d", "", "", "消费",
             "", "--", "完成"],
        ]  # fmt: skip
        (txn,) = provider.parse(create_xls(tmp_path, rows=rows))
        assert txn.date == date(2030, 1, 3)
        assert str(txn.amount) == "4321.00"
        assert txn.metadata["balance"] == "8642.00"

    def test_foreign_currency_account(self, tmp_path, provider):
        path = create_xls(tmp_path, title=TITLE.replace("人民币", "美元"))
        assert {t.currency for t in provider.parse(path)} == {"USD"}

    def test_currency_name_found_without_separator(self, tmp_path, provider):
        path = create_xls(tmp_path, title=TITLE.replace("1111 人民币", "1111人民币"))
        assert {t.currency for t in provider.parse(path)} == {"CNY"}

    def test_numeric_cells_of_whole_unit_currency(self, tmp_path, provider):
        rows = [
            ["20300103", "08:00:00", "", 4321.0, 2468.0, "payee-d", "", "",
             "消费", "", "--", "完成"],
        ]  # fmt: skip
        path = create_xls(tmp_path, rows=rows, title=TITLE.replace("人民币", "日元"))
        (txn,) = provider.parse(path)
        assert (txn.currency, str(txn.amount)) == ("JPY", "4321")
        assert txn.metadata["balance"] == "2468"

    def test_unknown_currency_warns_and_skips(self, tmp_path, provider, caplog):
        path = create_xls(tmp_path, title=TITLE.replace("人民币", "卢布"))
        assert provider.parse(path) == []
        assert "statement.xls" in caplog.text
        assert "卢布" in caplog.text

    def test_oldest_first_export_matches_newest_first(self, tmp_path, provider):
        newest_first = provider.parse(create_xls(tmp_path))
        oldest_first = provider.parse(
            create_xls(tmp_path, rows=ROWS[::-1], filename="oldest.xls")
        )

        def fields(txns):
            return [(t.date, t.time, t.amount, t.metadata) for t in txns]

        assert fields(oldest_first) == fields(newest_first)
        assert [t.source_line for t in oldest_first] == [3, 4, 5]

    def test_same_moment_rows_read_bottom_up(self, tmp_path, provider):
        rows = [
            ["20300102", "10:00:00", "__", "2468.00", "8642.00", "", "", "", "b",
             "", "--", "完成"],
            ["20300102", "10:00:00", "__", "4321.00", "1357.00", "", "", "", "a",
             "", "--", "完成"],
        ]  # fmt: skip
        txns = provider.parse(create_xls(tmp_path, rows=rows))
        assert [t.description for t in txns] == ["a", "b"]

    def test_missing_header_raises(self, tmp_path, provider):
        with pytest.raises(ValueError, match="header"):
            provider.parse(create_xls(tmp_path, headers=["a", "b", "c"]))


class TestReconcile:
    def test_unknown_currency_file_leaves_other_files_parsed(self, tmp_path):
        skipped = create_xls(
            tmp_path, title=TITLE.replace("人民币", "卢布"), filename="rub.xls"
        )
        parsed = create_xls(tmp_path, filename="cny.xls")
        ledger = tmp_path / "ledger.bean"
        ledger.write_text("2030-01-01 open Assets:Bank:CNCB\n", encoding="utf-8")
        config = tmp_path / "bean-sieve.yaml"
        config.write_text(
            'providers:\n  cncb_debit:\n    accounts:\n      "1111": Assets:Bank:CNCB\n',
            encoding="utf-8",
        )

        result = full_reconcile([skipped, parsed], ledger, config_path=config)

        assert {t.source_file for t in result.match_result.missing} == {parsed}
        assert len(result.match_result.missing) == len(ROWS)


class TestDetection:
    def test_other_xls_is_not_claimed(self, tmp_path):
        path = create_xls(tmp_path, headers=["交易日期", "收入金额", "支出金额"])
        assert not CNCBDebitProvider.can_handle(path)

    def test_detected_by_content(self, tmp_path):
        path = create_xls(tmp_path, filename="export.xls")
        assert auto_detect_provider(path).provider_id == "cncb_debit"

    def test_bank_named_file_detected_as_debit(self, tmp_path):
        path = create_xls(tmp_path, filename="中信银行交易明细.xls")
        assert auto_detect_provider(path).provider_id == "cncb_debit"

    def test_credit_export_is_not_debit(self, tmp_path):
        credit_headers = ["交易日期", "入账日期", "交易描述", "卡末四位",
                          "交易币种", "结算币种", "交易金额", "结算金额"]  # fmt: skip
        path = create_xls(
            tmp_path,
            rows=[],
            title="本期账单明细(人民币)",
            headers=credit_headers,
            filename="export.xls",
        )
        assert not CNCBDebitProvider.can_handle(path)
        assert auto_detect_provider(path).provider_id == "cncb_credit"

    def test_registered(self):
        assert isinstance(get_provider("cncb_debit"), CNCBDebitProvider)


def test_balance_takes_the_newest_of_same_moment_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The export lists the later of two same-moment rows on top.

    The hook reverses the batch, so the balance has to follow the statement
    order recorded at parse time rather than the list it receives.
    """
    monkeypatch.setattr(
        CNCBDebitProvider, "pre_reconcile", lambda _self, txns, _ctx: txns[::-1]
    )
    rows = [
        ["20300115", "10:00:00", "__", "20.00", "960.00", "payee-f", "", "",
         "消费", "REF0012", "--", "完成"],
        ["20300115", "10:00:00", "__", "20.00", "980.00", "payee-e", "", "",
         "消费", "REF0011", "--", "完成"],
    ]  # fmt: skip
    statement = create_xls(tmp_path, rows=rows)
    (tmp_path / "bean-sieve.yaml").write_text(
        "providers:\n"
        "  cncb_debit:\n"
        "    balance: true\n"
        "    accounts:\n"
        '      "1111": Assets:Bank:CNCB:1111\n',
        encoding="utf-8",
    )
    (tmp_path / "main.bean").write_text(
        "2029-01-01 open Assets:Bank:CNCB:1111\n", encoding="utf-8"
    )
    out = tmp_path / "pending.bean"
    full_reconcile(
        statement_paths=[statement],
        ledger_path=tmp_path / "main.bean",
        config_path=tmp_path / "bean-sieve.yaml",
        output_path=out,
    )

    assert "2030-02-01 balance Assets:Bank:CNCB:1111  960.00 CNY" in out.read_text(
        encoding="utf-8"
    )
