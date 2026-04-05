"""Tests for Bank of Communications (交通银行) debit card statement provider."""

from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest
import xlwt

from bean_sieve.providers import get_provider
from bean_sieve.providers.banks.debit.bocom import BOCOMDebitProvider

HEADERS = [
    "记账日期",
    "交易时间",
    "交易地点",
    "交易方式",
    "支出金额",
    "收入金额",
    "余额",
    "对方户名",
    "对方账户",
    "对方开户行",
    "摘要",
]


def create_bocom_xls(
    tmp_path: Path,
    transactions: list[dict],
    card_number: str = "6222 8888 8888 8888 888",
    filename: str = "交通银行_交易明细.xls",
) -> Path:
    """Create a BOCOM debit card XLS file with mock data."""
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Sheet0")

    # Row 0: Title
    ws.write(0, 0, "交通银行银行卡交易明细查询表")

    # Row 1: Account metadata
    ws.write(0 + 1, 0, f"银行卡号：{card_number} 姓名：测试用户")
    ws.write(
        0 + 1,
        5,
        "本次查询时间段：2026/01/05 至 2026/04/05      总收入：10000.00元  总支出：5000.00元",
    )

    # Row 2: Headers
    for col, header in enumerate(HEADERS):
        ws.write(2, col, header)

    # Data rows
    for i, txn in enumerate(transactions):
        row = 3 + i
        ws.write(row, 0, txn.get("date", "2026-01-15"))
        ws.write(row, 1, txn.get("time", "2026-01-15 10:00:00"))
        ws.write(row, 2, txn.get("location", "手机银行"))
        ws.write(row, 3, txn.get("method", "其它"))
        ws.write(row, 4, txn.get("expense", "--"))
        ws.write(row, 5, txn.get("income", "--"))
        ws.write(row, 6, txn.get("balance", "10,000.00"))
        ws.write(row, 7, txn.get("counterparty_name", ""))
        ws.write(row, 8, txn.get("counterparty_account", ""))
        ws.write(row, 9, txn.get("counterparty_bank", ""))
        ws.write(row, 10, txn.get("summary", ""))

    file_path = tmp_path / filename
    wb.save(str(file_path))
    return file_path


@pytest.fixture
def bocom_xls_file(tmp_path: Path) -> Path:
    """Create a sample BOCOM debit card XLS file."""
    transactions = [
        {
            "date": "2026-03-28",
            "time": "2026-03-28 21:23:33",
            "location": "他行自助设备",
            "method": "异地跨行取现",
            "expense": "10,000.00",
            "income": "--",
            "balance": "72.90",
            "counterparty_name": "",
            "counterparty_account": "",
            "counterparty_bank": "测试银行北京分行",
            "summary": "异地跨行取现",
        },
        {
            "date": "2026-03-21",
            "time": "2026-03-21 05:14:01",
            "location": "批处理",
            "method": "存款利息",
            "expense": "--",
            "income": "0.04",
            "balance": "72.90",
            "counterparty_name": "应付个人活期储蓄存款利息",
            "summary": "",
        },
        {
            "date": "2026-02-26",
            "time": "2026-02-26 19:12:49",
            "location": "测试商户A",
            "method": "网上支付",
            "expense": "19.15",
            "income": "--",
            "balance": "186.45",
            "counterparty_name": "支付宝-消费",
            "summary": "|交易类型:快捷支付|交易说明:生活服务消费|交易商户:测试商户A|订单编号:0226b82501059190|流水号:0226b82501059190|交易渠道:支付宝|",
        },
        {
            "date": "2026-01-10",
            "time": "2026-01-10 16:00:25",
            "location": "微信支付",
            "method": "网上银行卡转入",
            "expense": "--",
            "income": "540.00",
            "balance": "594.04",
            "counterparty_name": "测试用户",
            "counterparty_account": "1234567890",
            "counterparty_bank": "财付通支付科技有限公司",
            "summary": "微信零钱提现 交易流水号20260110651659460265643S0100302",
        },
    ]
    return create_bocom_xls(tmp_path, transactions)


class TestBOCOMDebitProvider:
    """Tests for BOCOMDebitProvider."""

    def test_provider_registration(self) -> None:
        """Test that BOCOM debit provider is properly registered."""
        provider = get_provider("bocom_debit")
        assert isinstance(provider, BOCOMDebitProvider)
        assert provider.provider_id == "bocom_debit"
        assert provider.provider_name == "交通银行借记卡"
        assert ".xls" in provider.supported_formats

    def test_can_handle(self) -> None:
        """Test file format detection."""
        assert BOCOMDebitProvider.can_handle(Path("交通银行_交易明细.xls"))
        assert not BOCOMDebitProvider.can_handle(Path("交易明细列表.xls"))
        assert not BOCOMDebitProvider.can_handle(Path("交通银行.csv"))
        assert not BOCOMDebitProvider.can_handle(Path("建设银行交易明细.xls"))

    def test_parse_basic(self, bocom_xls_file: Path) -> None:
        """Test basic parsing functionality."""
        provider = BOCOMDebitProvider()
        transactions = provider.parse(bocom_xls_file)

        assert len(transactions) == 4

        # Expense: ATM withdrawal
        txn1 = transactions[0]
        assert txn1.date == date(2026, 3, 28)
        assert txn1.time == time(21, 23, 33)
        assert txn1.amount == Decimal("10000")
        assert txn1.currency == "CNY"
        assert txn1.card_last4 == "8888"
        assert txn1.provider == "bocom_debit"
        assert txn1.is_expense

        # Income: interest
        txn2 = transactions[1]
        assert txn2.date == date(2026, 3, 21)
        assert txn2.amount == Decimal("-0.04")
        assert txn2.is_income
        assert txn2.payee == "应付个人活期储蓄存款利息"

        # Expense with order_id from structured summary
        txn3 = transactions[2]
        assert txn3.amount == Decimal("19.15")
        assert txn3.order_id == "0226b82501059190"
        assert "生活服务消费" in txn3.description

        # Income: WeChat withdrawal with order_id
        txn4 = transactions[3]
        assert txn4.amount == Decimal("-540")
        assert txn4.order_id == "20260110651659460265643S0100302"
        assert txn4.is_income

    def test_card_last4_extraction(self, bocom_xls_file: Path) -> None:
        """Test card_last4 extracted from metadata row."""
        provider = BOCOMDebitProvider()
        transactions = provider.parse(bocom_xls_file)
        for txn in transactions:
            assert txn.card_last4 == "8888"

    def test_empty_statement(self, tmp_path: Path) -> None:
        """Test handling of statement with no transactions."""
        file_path = create_bocom_xls(tmp_path, [])
        provider = BOCOMDebitProvider()
        transactions = provider.parse(file_path)
        assert transactions == []

    def test_amounts_with_thousand_separators(self, tmp_path: Path) -> None:
        """Test parsing amounts with comma separators."""
        transactions = [
            {
                "date": "2026-01-15",
                "time": "2026-01-15 10:00:00",
                "expense": "12,345.67",
                "income": "--",
            },
        ]
        file_path = create_bocom_xls(tmp_path, transactions)
        provider = BOCOMDebitProvider()
        parsed = provider.parse(file_path)
        assert len(parsed) == 1
        assert parsed[0].amount == Decimal("12345.67")

    def test_dash_amounts_skipped(self, tmp_path: Path) -> None:
        """Test that rows with '--' in both expense and income are skipped."""
        transactions = [
            {
                "date": "2026-01-15",
                "time": "2026-01-15 10:00:00",
                "expense": "--",
                "income": "--",
            },
        ]
        file_path = create_bocom_xls(tmp_path, transactions)
        provider = BOCOMDebitProvider()
        parsed = provider.parse(file_path)
        assert len(parsed) == 0

    def test_non_bocom_statement_rejected(self, tmp_path: Path) -> None:
        """Test that XLS files without BOCOM title are rejected."""
        wb = xlwt.Workbook()
        ws = wb.add_sheet("Sheet0")
        ws.write(0, 0, "其他银行交易明细")
        for col, header in enumerate(HEADERS):
            ws.write(2, col, header)
        ws.write(3, 0, "2026-01-15")
        ws.write(3, 1, "2026-01-15 10:00:00")
        ws.write(3, 4, "100.00")
        file_path = tmp_path / "交通银行_test.xls"
        wb.save(str(file_path))

        provider = BOCOMDebitProvider()
        transactions = provider.parse(file_path)
        assert transactions == []

    def test_fewer_columns_rejected(self, tmp_path: Path) -> None:
        """Test that files with too few columns return empty."""
        wb = xlwt.Workbook()
        ws = wb.add_sheet("Sheet0")
        ws.write(0, 0, "交通银行银行卡交易明细查询表")
        ws.write(2, 0, "记账日期")
        ws.write(2, 1, "交易时间")
        file_path = tmp_path / "交通银行_short.xls"
        wb.save(str(file_path))

        provider = BOCOMDebitProvider()
        transactions = provider.parse(file_path)
        assert transactions == []

    def test_description_building(self, bocom_xls_file: Path) -> None:
        """Test description is built from method, location, and summary."""
        provider = BOCOMDebitProvider()
        transactions = provider.parse(bocom_xls_file)

        # ATM withdrawal: method + location + summary
        txn1 = transactions[0]
        assert "异地跨行取现" in txn1.description
        assert "他行自助设备" in txn1.description

        # Interest: method + location (no summary)
        txn2 = transactions[1]
        assert "存款利息" in txn2.description
        assert "批处理" in txn2.description

    def test_structured_summary_description(self, tmp_path: Path) -> None:
        """Test that structured pipe-delimited summaries extract 交易说明."""
        transactions = [
            {
                "date": "2026-01-15",
                "time": "2026-01-15 10:00:00",
                "method": "网上支付",
                "location": "测试商户B",
                "expense": "50.00",
                "summary": "|交易类型:快捷支付|交易说明:餐饮消费|交易商户:测试商户B|订单编号:test123|",
            },
        ]
        file_path = create_bocom_xls(tmp_path, transactions)
        provider = BOCOMDebitProvider()
        parsed = provider.parse(file_path)
        assert len(parsed) == 1
        assert "餐饮消费" in parsed[0].description
        assert parsed[0].order_id == "test123"

    def test_order_id_from_liushui(self, tmp_path: Path) -> None:
        """Test order_id extraction from 流水号: pattern (without 订单编号)."""
        transactions = [
            {
                "date": "2026-01-15",
                "time": "2026-01-15 10:00:00",
                "expense": "100.00",
                "summary": "|交易类型:转账|流水号:ABC123456|",
            },
        ]
        file_path = create_bocom_xls(tmp_path, transactions)
        provider = BOCOMDebitProvider()
        parsed = provider.parse(file_path)
        assert len(parsed) == 1
        assert parsed[0].order_id == "ABC123456"

    def test_zero_amount_skipped(self, tmp_path: Path) -> None:
        """Test that rows with zero expense amount are skipped."""
        transactions = [
            {
                "date": "2026-01-15",
                "time": "2026-01-15 10:00:00",
                "expense": "0.00",
                "income": "--",
            },
        ]
        file_path = create_bocom_xls(tmp_path, transactions)
        provider = BOCOMDebitProvider()
        parsed = provider.parse(file_path)
        assert len(parsed) == 0

    def test_card_last4_no_match(self, tmp_path: Path) -> None:
        """Test card_last4 is None when metadata doesn't contain card number."""
        wb = xlwt.Workbook()
        ws = wb.add_sheet("Sheet0")
        ws.write(0, 0, "交通银行银行卡交易明细查询表")
        ws.write(1, 0, "查询信息不完整")
        for col, header in enumerate(HEADERS):
            ws.write(2, col, header)
        ws.write(3, 0, "2026-01-15")
        ws.write(3, 1, "2026-01-15 10:00:00")
        ws.write(3, 4, "100.00")
        file_path = tmp_path / "交通银行_nocard.xls"
        wb.save(str(file_path))

        provider = BOCOMDebitProvider()
        transactions = provider.parse(file_path)
        assert len(transactions) == 1
        assert transactions[0].card_last4 is None

    def test_invalid_date_rows_skipped(self, tmp_path: Path) -> None:
        """Test that rows with invalid dates are skipped."""
        transactions = [
            {"date": "invalid-date", "expense": "100.00"},
            {"date": "", "expense": "200.00"},
            {"date": "2026-01-15", "time": "2026-01-15 10:00:00", "expense": "300.00"},
        ]
        file_path = create_bocom_xls(tmp_path, transactions)
        provider = BOCOMDebitProvider()
        parsed = provider.parse(file_path)
        assert len(parsed) == 1
        assert parsed[0].amount == Decimal("300")
