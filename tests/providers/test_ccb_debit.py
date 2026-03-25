"""Tests for China Construction Bank (CCB) debit card statement provider."""

from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest
import xlwt

from bean_sieve.providers import get_provider
from bean_sieve.providers.banks.debit.ccb import CCBDebitProvider

HEADERS = [
    "记账日",
    "交易日期",
    "交易时间",
    "支出",
    "收入",
    "账户余额",
    "币种",
    "摘要",
    "对方账号",
    "对方户名",
    "交易地点",
]

FOOTER = "以上数据仅供参考|具体内容请以柜台为准"


def create_ccb_xls(
    tmp_path: Path,
    transactions: list[dict],
    card_suffix: str = "6789",
) -> Path:
    """Create a CCB debit card XLS file."""
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Sheet1")

    # Metadata rows
    ws.write(0, 0, "China Construction Bank")
    ws.write(1, 0, "开户机构：")
    ws.write(1, 1, "北京市")
    ws.write(2, 0, "币　　种：")
    ws.write(2, 1, "人民币")
    ws.write(3, 0, "账　　号：")
    ws.write(3, 1, f"622588*********{card_suffix}")
    # Row 4: blank

    # Row 5: headers
    for col, header in enumerate(HEADERS):
        ws.write(5, col, header)

    # Data rows
    for i, txn in enumerate(transactions):
        row = 6 + i
        ws.write(row, 0, txn.get("posting_date", txn.get("date", "20250115")))
        ws.write(row, 1, txn.get("date", "20250115"))
        ws.write(row, 2, txn.get("time", "10:00:00"))
        ws.write(row, 3, txn.get("expense", 0.0))
        ws.write(row, 4, txn.get("income", 0.0))
        ws.write(row, 5, txn.get("balance", 10000.0))
        ws.write(row, 6, txn.get("currency", "人民币"))
        ws.write(row, 7, txn.get("summary", ""))
        ws.write(row, 8, txn.get("counterparty_account", ""))
        ws.write(row, 9, txn.get("counterparty_name", ""))
        ws.write(row, 10, txn.get("location", ""))

    # Footer row
    footer_row = 6 + len(transactions)
    ws.write(footer_row, 0, FOOTER)

    file_path = tmp_path / f"交易明细_{card_suffix}_20250101_20250331.xls"
    wb.save(str(file_path))
    return file_path


@pytest.fixture
def ccb_xls_file(tmp_path):
    """Create a sample CCB debit card XLS file."""
    transactions = [
        {
            "date": "20250110",
            "time": "09:30:15",
            "expense": 88.50,
            "income": 0.0,
            "balance": 9911.50,
            "summary": "无卡自助交易",
            "counterparty_name": "Test Credit Center",
            "location": "测试银行信用卡中心还款",
        },
        {
            "date": "20250115",
            "time": "14:20:00",
            "expense": 25.00,
            "income": 0.0,
            "balance": 9886.50,
            "summary": "消费",
            "counterparty_name": "测试商户",
            "location": "财付通-微信支付-测试商户",
        },
        {
            "date": "20250120",
            "time": "11:00:00",
            "expense": 0.0,
            "income": 5000.0,
            "balance": 14886.50,
            "summary": "汇兑",
            "counterparty_name": "Test Sender",
            "location": "电子汇入",
        },
        {
            "date": "20250321",
            "time": "03:00:00",
            "expense": 0.0,
            "income": 1.25,
            "balance": 14887.75,
            "summary": "利息存入",
            "counterparty_name": "",
            "location": "",
        },
    ]
    return create_ccb_xls(tmp_path, transactions)


class TestCCBDebitProvider:
    """Tests for CCBDebitProvider."""

    def test_provider_registration(self):
        """Test that CCB debit provider is properly registered."""
        provider = get_provider("ccb_debit")
        assert isinstance(provider, CCBDebitProvider)
        assert provider.provider_id == "ccb_debit"
        assert provider.provider_name == "建设银行借记卡"
        assert ".xls" in provider.supported_formats

    def test_can_handle(self):
        """Test file format detection."""
        assert CCBDebitProvider.can_handle(Path("交易明细_6789_20250101_20250331.xls"))
        assert not CCBDebitProvider.can_handle(Path("交易明细.csv"))
        assert not CCBDebitProvider.can_handle(Path("random.xls"))
        # Should not match files without the CCB naming pattern
        assert not CCBDebitProvider.can_handle(Path("平安银行交易明细.xls"))

    def test_parse_transactions(self, ccb_xls_file):
        """Test parsing transactions from XLS file."""
        provider = CCBDebitProvider()
        transactions = provider.parse(ccb_xls_file)

        assert len(transactions) == 4

        # Expense transaction
        txn1 = transactions[0]
        assert txn1.date == date(2025, 1, 10)
        assert txn1.time == time(9, 30, 15)
        assert txn1.amount == Decimal("88.5")
        assert txn1.currency == "CNY"
        assert txn1.card_last4 == "6789"
        assert txn1.provider == "ccb_debit"
        assert txn1.is_expense

        # Small expense
        txn2 = transactions[1]
        assert txn2.amount == Decimal("25")
        assert "测试商户" in (txn2.payee or "")

        # Income transaction (negative)
        txn3 = transactions[2]
        assert txn3.amount == Decimal("-5000")
        assert txn3.is_income

        # Interest income
        txn4 = transactions[3]
        assert txn4.amount == Decimal("-1.25")
        assert txn4.is_income

    def test_card_last4_from_account_row(self, ccb_xls_file):
        """Test card_last4 extraction from account metadata."""
        provider = CCBDebitProvider()
        transactions = provider.parse(ccb_xls_file)
        for txn in transactions:
            assert txn.card_last4 == "6789"

    def test_card_last4_from_filename(self, tmp_path):
        """Test card_last4 extraction fallback to filename."""
        wb = xlwt.Workbook()
        ws = wb.add_sheet("Sheet1")
        # No account row with card number
        ws.write(0, 0, "China Construction Bank")
        for col, header in enumerate(HEADERS):
            ws.write(5, col, header)
        ws.write(6, 0, "20250101")
        ws.write(6, 1, "20250101")
        ws.write(6, 2, "10:00:00")
        ws.write(6, 3, 100.0)
        ws.write(6, 4, 0.0)
        ws.write(6, 5, 9900.0)
        ws.write(6, 6, "人民币")
        ws.write(6, 7, "消费")
        ws.write(7, 0, FOOTER)

        file_path = tmp_path / "交易明细_5678_20250101_20250201.xls"
        wb.save(str(file_path))

        provider = CCBDebitProvider()
        transactions = provider.parse(file_path)
        assert len(transactions) == 1
        assert transactions[0].card_last4 == "5678"

    def test_empty_statement(self, tmp_path):
        """Test handling of statement with no transactions."""
        file_path = create_ccb_xls(tmp_path, [])
        provider = CCBDebitProvider()
        transactions = provider.parse(file_path)
        assert transactions == []

    def test_description_building(self, ccb_xls_file):
        """Test description combines summary and location."""
        provider = CCBDebitProvider()
        transactions = provider.parse(ccb_xls_file)

        txn1 = transactions[0]
        assert "无卡自助交易" in txn1.description
        assert "测试银行信用卡中心还款" in txn1.description

        # Interest with no location
        txn4 = transactions[3]
        assert "利息存入" in txn4.description

    def test_metadata_includes_summary(self, ccb_xls_file):
        """Test that metadata includes summary field."""
        provider = CCBDebitProvider()
        transactions = provider.parse(ccb_xls_file)

        assert transactions[0].metadata["summary"] == "无卡自助交易"
        assert transactions[1].metadata["summary"] == "消费"

    def test_footer_row_excluded(self, tmp_path):
        """Test that footer disclaimer row is not parsed."""
        transactions = [
            {
                "date": "20250101",
                "time": "10:00:00",
                "expense": 50.0,
                "summary": "消费",
                "counterparty_name": "Test",
                "location": "测试地点",
            },
        ]
        file_path = create_ccb_xls(tmp_path, transactions)
        provider = CCBDebitProvider()
        parsed = provider.parse(file_path)
        assert len(parsed) == 1

    def test_zero_amount_rows_skipped(self, tmp_path):
        """Test that rows with zero expense and zero income are skipped."""
        transactions = [
            {
                "date": "20250101",
                "time": "10:00:00",
                "expense": 0.0,
                "income": 0.0,
                "summary": "查询",
            },
        ]
        file_path = create_ccb_xls(tmp_path, transactions)
        provider = CCBDebitProvider()
        parsed = provider.parse(file_path)
        assert len(parsed) == 0


class TestCCBNumericCellHandling:
    """Tests for xlrd numeric cell value handling."""

    def test_numeric_date_cells(self, tmp_path):
        """Test parsing when dates are stored as numbers (xlrd returns float)."""
        wb = xlwt.Workbook()
        ws = wb.add_sheet("Sheet1")
        ws.write(0, 0, "China Construction Bank")
        ws.write(3, 1, "622588*********6789")
        for col, header in enumerate(HEADERS):
            ws.write(5, col, header)
        # Write date as integer (xlrd returns as float)
        ws.write(6, 0, 20250115)
        ws.write(6, 1, 20250115)
        ws.write(6, 2, "12:30:00")
        ws.write(6, 3, 200.0)
        ws.write(6, 4, 0.0)
        ws.write(6, 5, 9800.0)
        ws.write(6, 6, "人民币")
        ws.write(6, 7, "消费")
        ws.write(6, 9, "测试商户")
        ws.write(6, 10, "测试地点")
        ws.write(7, 0, FOOTER)

        file_path = tmp_path / "交易明细_6789_20250101_20250331.xls"
        wb.save(str(file_path))

        provider = CCBDebitProvider()
        transactions = provider.parse(file_path)
        assert len(transactions) == 1
        assert transactions[0].date == date(2025, 1, 15)
        assert transactions[0].amount == Decimal("200")

    def test_zero_counterparty_not_leaked_as_payee(self, tmp_path):
        """Test that numeric zero counterparty does not become '0.0' payee."""
        wb = xlwt.Workbook()
        ws = wb.add_sheet("Sheet1")
        ws.write(0, 0, "China Construction Bank")
        ws.write(3, 1, "622588*********6789")
        for col, header in enumerate(HEADERS):
            ws.write(5, col, header)
        ws.write(6, 0, "20250101")
        ws.write(6, 1, "20250101")
        ws.write(6, 2, "10:00:00")
        ws.write(6, 3, 0.0)
        ws.write(6, 4, 50.0)
        ws.write(6, 5, 10050.0)
        ws.write(6, 6, "人民币")
        ws.write(6, 7, "利息存入")
        # Write counterparty as numeric 0 (xlrd returns 0.0)
        ws.write(6, 9, 0)
        ws.write(6, 10, "")
        ws.write(7, 0, FOOTER)

        file_path = tmp_path / "交易明细_6789_20250101_20250331.xls"
        wb.save(str(file_path))

        provider = CCBDebitProvider()
        transactions = provider.parse(file_path)
        assert len(transactions) == 1
        assert transactions[0].payee is None

    def test_malformed_date_skipped(self, tmp_path):
        """Test that rows with malformed dates are skipped."""
        wb = xlwt.Workbook()
        ws = wb.add_sheet("Sheet1")
        ws.write(0, 0, "China Construction Bank")
        ws.write(3, 1, "622588*********6789")
        for col, header in enumerate(HEADERS):
            ws.write(5, col, header)
        # 7-digit date
        ws.write(6, 1, "2025011")
        ws.write(6, 3, 100.0)
        # Non-numeric date
        ws.write(7, 1, "abcdefgh")
        ws.write(7, 3, 200.0)
        # Valid row after bad rows
        ws.write(8, 1, "20250115")
        ws.write(8, 2, "10:00:00")
        ws.write(8, 3, 300.0)
        ws.write(8, 7, "消费")
        ws.write(9, 0, FOOTER)

        file_path = tmp_path / "交易明细_6789_20250101_20250331.xls"
        wb.save(str(file_path))

        provider = CCBDebitProvider()
        transactions = provider.parse(file_path)
        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("300")

    def test_dynamic_header_detection(self, tmp_path):
        """Test that header row is found dynamically, not by fixed position."""
        wb = xlwt.Workbook()
        ws = wb.add_sheet("Sheet1")
        ws.write(0, 0, "China Construction Bank")
        ws.write(1, 0, "Extra row")
        ws.write(2, 0, "Another extra row")
        ws.write(3, 0, "开户机构：")
        ws.write(4, 0, "币　　种：")
        ws.write(5, 0, "账　　号：")
        ws.write(5, 1, "622588*********6789")
        # Header at row 7 instead of the default row 5
        for col, header in enumerate(HEADERS):
            ws.write(7, col, header)
        ws.write(8, 0, "20250101")
        ws.write(8, 1, "20250101")
        ws.write(8, 2, "10:00:00")
        ws.write(8, 3, 150.0)
        ws.write(8, 7, "消费")
        ws.write(8, 9, "测试商户")
        ws.write(9, 0, FOOTER)

        file_path = tmp_path / "交易明细_6789_20250101_20250331.xls"
        wb.save(str(file_path))

        provider = CCBDebitProvider()
        transactions = provider.parse(file_path)
        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("150")
