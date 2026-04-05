"""Tests for Industrial Bank (兴业银行) debit card statement provider."""

from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest
import xlwt

from bean_sieve.providers import get_provider
from bean_sieve.providers.banks.debit.cib import CIBDebitProvider

HEADERS = [
    "交易时间",
    "记账日",
    "支出",
    "收入",
    "账户余额",
    "摘要",
    "对方户名",
    "对方银行",
    "对方账号",
    "用途",
    "交易渠道",
    "备注",
]

FOOTER_LABEL = "说明"
FOOTER_TEXT = "交易明细涉及您的个人隐私，请妥善处理，避免信息篡改或泄露，交易明细内容仅供个人参考。"


def create_cib_xls(
    tmp_path: Path,
    transactions: list[dict],
    account_no: str = "622900000000001234",
    filename: str | None = None,
) -> Path:
    """Create a CIB debit card XLS file with mock data."""
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Sheet0")

    # Metadata rows (matching real file structure)
    ws.write(0, 0, "兴业银行交易明细")
    # Row 1: blank
    ws.write(2, 0, "账户别名")
    ws.write(3, 0, "账户户名")
    ws.write(3, 1, "测试用户")
    ws.write(4, 0, "账户账号")
    ws.write(4, 1, account_no)
    ws.write(5, 0, "卡内账户")
    ws.write(5, 1, "001 活期储蓄存款 人民币")
    ws.write(6, 0, "起始日期")
    ws.write(6, 1, "2026-03-05")
    ws.write(7, 0, "截止日期")
    ws.write(7, 1, "2026-04-05")
    ws.write(8, 0, "下载日期")
    ws.write(8, 1, "2026-04-05 23:04:57")
    # Row 9: blank

    # Row 10: headers
    for col, header in enumerate(HEADERS):
        ws.write(10, col, header)

    # Data rows
    for i, txn in enumerate(transactions):
        row = 11 + i
        ws.write(row, 0, txn.get("tx_time", "2026-03-15 10:00:00"))
        ws.write(row, 1, txn.get("post_date", "2026-03-15"))
        ws.write(row, 2, txn.get("expense", ""))
        ws.write(row, 3, txn.get("income", ""))
        ws.write(row, 4, txn.get("balance", "50,000.00"))
        ws.write(row, 5, txn.get("summary", ""))
        ws.write(row, 6, txn.get("counterparty_name", ""))
        ws.write(row, 7, txn.get("counterparty_bank", ""))
        ws.write(row, 8, txn.get("counterparty_account", ""))
        ws.write(row, 9, txn.get("purpose", ""))
        ws.write(row, 10, txn.get("channel", ""))
        ws.write(row, 11, txn.get("remark", ""))

    # Footer row
    footer_row = 11 + len(transactions)
    ws.write(footer_row, 0, FOOTER_LABEL)
    ws.write(footer_row, 1, FOOTER_TEXT)

    if filename is None:
        filename = "测试用户的交易明细 20260305-20260405.xls"
    file_path = tmp_path / filename
    wb.save(str(file_path))
    return file_path


@pytest.fixture
def cib_xls_file(tmp_path: Path) -> Path:
    """Create a sample CIB debit card XLS file."""
    transactions = [
        {
            "tx_time": "2026-04-01 14:33:17",
            "post_date": "2026-04-01",
            "expense": "10,000.00",
            "income": "",
            "balance": "20,771.43",
            "summary": "转账转出",
            "counterparty_name": "测试收款人A",
            "counterparty_bank": "测试银行",
            "counterparty_account": "8880001234560000",
            "channel": "手机银行",
        },
        {
            "tx_time": "2026-04-01 08:04:17",
            "post_date": "2026-04-01",
            "expense": "30,000.00",
            "income": "",
            "balance": "30,771.43",
            "summary": "汇款汇出",
            "counterparty_name": "测试收款人B",
            "counterparty_bank": "招商银行",
            "counterparty_account": "6214860000005555",
            "purpose": "转账",
            "channel": "手机银行",
        },
        {
            "tx_time": "2026-03-31 14:26:18",
            "post_date": "2026-03-31",
            "expense": "",
            "income": "9,000.00",
            "balance": "80,771.43",
            "summary": "汇款汇入",
            "counterparty_name": "测试汇款人C",
            "counterparty_bank": "中国工商银行",
            "counterparty_account": "6222030000007865",
            "channel": "其他",
        },
        {
            "tx_time": "2026-03-21 00:57:56",
            "post_date": "2026-03-21",
            "expense": "",
            "income": "8.36",
            "balance": "52,354.87",
            "summary": "存款利息",
            "channel": "其他",
        },
    ]
    return create_cib_xls(tmp_path, transactions)


class TestCIBDebitProvider:
    """Tests for CIBDebitProvider."""

    def test_provider_registration(self) -> None:
        """Test that CIB debit provider is properly registered."""
        provider = get_provider("cib_debit")
        assert isinstance(provider, CIBDebitProvider)
        assert provider.provider_id == "cib_debit"
        assert provider.provider_name == "兴业银行借记卡"
        assert ".xls" in provider.supported_formats

    def test_can_handle(self) -> None:
        """Test file format detection."""
        assert CIBDebitProvider.can_handle(Path("张三的交易明细 20260305-20260405.xls"))
        assert CIBDebitProvider.can_handle(Path("测试的交易明细 20260101-20260201.xls"))
        assert not CIBDebitProvider.can_handle(Path("交易明细.csv"))
        assert not CIBDebitProvider.can_handle(Path("random.xls"))
        # Should not match CCB's pattern
        assert not CIBDebitProvider.can_handle(
            Path("交易明细_6789_20250101_20250331.xls")
        )

    def test_parse_transactions(self, cib_xls_file: Path) -> None:
        """Test parsing transactions from XLS file."""
        provider = CIBDebitProvider()
        transactions = provider.parse(cib_xls_file)

        assert len(transactions) == 4

        # Expense transaction
        txn1 = transactions[0]
        assert txn1.date == date(2026, 4, 1)
        assert txn1.time == time(14, 33, 17)
        assert txn1.amount == Decimal("10000")
        assert txn1.currency == "CNY"
        assert txn1.card_last4 == "1234"
        assert txn1.provider == "cib_debit"
        assert txn1.is_expense
        assert txn1.payee == "测试收款人A"

        # Expense with purpose
        txn2 = transactions[1]
        assert txn2.amount == Decimal("30000")
        assert "汇款汇出" in txn2.description
        assert "转账" in txn2.description

        # Income transaction (negative)
        txn3 = transactions[2]
        assert txn3.amount == Decimal("-9000")
        assert txn3.is_income
        assert txn3.payee == "测试汇款人C"

        # Interest income
        txn4 = transactions[3]
        assert txn4.amount == Decimal("-8.36")
        assert txn4.is_income
        assert txn4.payee is None  # no counterparty for interest

    def test_card_last4_extraction(self, cib_xls_file: Path) -> None:
        """Test card_last4 extraction from account number row."""
        provider = CIBDebitProvider()
        transactions = provider.parse(cib_xls_file)
        for txn in transactions:
            assert txn.card_last4 == "1234"

    def test_card_last4_different_account(self, tmp_path: Path) -> None:
        """Test card_last4 with different account number."""
        transactions = [
            {
                "tx_time": "2026-03-15 10:00:00",
                "post_date": "2026-03-15",
                "expense": "100.00",
                "summary": "消费",
            },
        ]
        file_path = create_cib_xls(
            tmp_path, transactions, account_no="622900000000008888"
        )
        provider = CIBDebitProvider()
        parsed = provider.parse(file_path)
        assert len(parsed) == 1
        assert parsed[0].card_last4 == "8888"

    def test_empty_statement(self, tmp_path: Path) -> None:
        """Test handling of statement with no transactions."""
        file_path = create_cib_xls(tmp_path, [])
        provider = CIBDebitProvider()
        transactions = provider.parse(file_path)
        assert transactions == []

    def test_description_building(self, cib_xls_file: Path) -> None:
        """Test description combines summary and purpose."""
        provider = CIBDebitProvider()
        transactions = provider.parse(cib_xls_file)

        # No purpose → summary only
        assert transactions[0].description == "转账转出"

        # With purpose → "summary | purpose"
        assert transactions[1].description == "汇款汇出 | 转账"

        # Interest with no purpose
        assert transactions[3].description == "存款利息"

    def test_metadata_fields(self, cib_xls_file: Path) -> None:
        """Test that metadata includes summary, counterparty_bank, counterparty_account."""
        provider = CIBDebitProvider()
        transactions = provider.parse(cib_xls_file)

        assert transactions[0].metadata["summary"] == "转账转出"
        assert transactions[0].metadata["counterparty_bank"] == "测试银行"
        assert transactions[0].metadata["counterparty_account"] == "8880001234560000"

        # Interest has no counterparty info
        assert transactions[3].metadata["summary"] == "存款利息"
        assert "counterparty_bank" not in transactions[3].metadata
        assert "counterparty_account" not in transactions[3].metadata

    def test_footer_row_excluded(self, cib_xls_file: Path) -> None:
        """Test that footer disclaimer row is not parsed."""
        provider = CIBDebitProvider()
        transactions = provider.parse(cib_xls_file)
        # Should only have 4 transactions, footer should not be parsed
        assert len(transactions) == 4

    def test_amounts_with_thousand_separators(self, tmp_path: Path) -> None:
        """Test parsing amounts with comma thousand separators."""
        transactions = [
            {
                "tx_time": "2026-03-15 10:00:00",
                "post_date": "2026-03-15",
                "expense": "12,345.67",
                "summary": "大额消费",
            },
        ]
        file_path = create_cib_xls(tmp_path, transactions)
        provider = CIBDebitProvider()
        parsed = provider.parse(file_path)
        assert len(parsed) == 1
        assert parsed[0].amount == Decimal("12345.67")

    def test_empty_payee_is_none(self, tmp_path: Path) -> None:
        """Test that empty counterparty name results in None payee."""
        transactions = [
            {
                "tx_time": "2026-03-21 00:57:56",
                "post_date": "2026-03-21",
                "income": "8.36",
                "summary": "存款利息",
            },
        ]
        file_path = create_cib_xls(tmp_path, transactions)
        provider = CIBDebitProvider()
        parsed = provider.parse(file_path)
        assert len(parsed) == 1
        assert parsed[0].payee is None

    def test_fewer_columns_returns_empty(self, tmp_path: Path) -> None:
        """Test that XLS with fewer columns than expected returns empty."""
        wb = xlwt.Workbook()
        ws = wb.add_sheet("Sheet0")
        ws.write(0, 0, "兴业银行交易明细")
        # Only write a few columns (less than 12)
        ws.write(10, 0, "交易时间")
        ws.write(10, 1, "记账日")
        ws.write(11, 0, "2026-03-15 10:00:00")
        ws.write(11, 1, "2026-03-15")

        file_path = tmp_path / "测试的交易明细 20260305-20260405.xls"
        wb.save(str(file_path))

        provider = CIBDebitProvider()
        parsed = provider.parse(file_path)
        assert parsed == []

    def test_invalid_datetime_rows_skipped(self, tmp_path: Path) -> None:
        """Test that rows with invalid datetime are skipped."""
        transactions = [
            {
                "tx_time": "invalid-date",
                "post_date": "2026-03-15",
                "expense": "100.00",
                "summary": "消费",
            },
            {
                "tx_time": "2026-03-15 10:00:00",
                "post_date": "2026-03-15",
                "expense": "200.00",
                "summary": "消费B",
            },
        ]
        file_path = create_cib_xls(tmp_path, transactions)
        provider = CIBDebitProvider()
        parsed = provider.parse(file_path)
        # First row skipped due to invalid date
        assert len(parsed) == 1
        assert parsed[0].amount == Decimal("200")

    def test_zero_amount_rows_skipped(self, tmp_path: Path) -> None:
        """Test that rows with zero expense and zero income are skipped."""
        transactions = [
            {
                "tx_time": "2026-03-15 10:00:00",
                "post_date": "2026-03-15",
                "expense": "",
                "income": "",
                "summary": "查询",
            },
        ]
        file_path = create_cib_xls(tmp_path, transactions)
        provider = CIBDebitProvider()
        parsed = provider.parse(file_path)
        assert len(parsed) == 0

    def test_source_line_tracking(self, cib_xls_file: Path) -> None:
        """Test that source_line is correctly tracked."""
        provider = CIBDebitProvider()
        transactions = provider.parse(cib_xls_file)
        # Data starts at row 11 (0-indexed), source_line is 1-indexed
        assert transactions[0].source_line == 12
        assert transactions[1].source_line == 13

    def test_dynamic_header_detection(self, tmp_path: Path) -> None:
        """Test that header row is found dynamically, not by fixed position."""
        wb = xlwt.Workbook()
        ws = wb.add_sheet("Sheet0")
        ws.write(0, 0, "兴业银行交易明细")
        ws.write(4, 0, "账户账号")
        ws.write(4, 1, "622900000000001234")
        # Extra metadata rows, header at row 12 instead of 10
        ws.write(10, 0, "额外信息行1")
        ws.write(11, 0, "额外信息行2")
        for col, header in enumerate(HEADERS):
            ws.write(12, col, header)
        ws.write(13, 0, "2026-03-15 10:00:00")
        ws.write(13, 1, "2026-03-15")
        ws.write(13, 2, "150.00")
        ws.write(13, 5, "消费")
        ws.write(13, 6, "测试商户")
        ws.write(14, 0, FOOTER_LABEL)
        ws.write(14, 1, FOOTER_TEXT)

        file_path = tmp_path / "测试的交易明细 20260305-20260405.xls"
        wb.save(str(file_path))

        provider = CIBDebitProvider()
        transactions = provider.parse(file_path)
        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("150")


class TestCIBDebitEdgeCases:
    """Additional edge case tests from code review."""

    def test_numeric_cell_normalize(self, tmp_path: Path) -> None:
        """Test _normalize_cell_str handles xlrd float-as-int values.

        Note: 18-digit account numbers exceed float precision (IEEE 754),
        so banks always store them as text. This test verifies that shorter
        numeric values (like amounts written as numbers) are normalized correctly.
        """
        wb = xlwt.Workbook()
        ws = wb.add_sheet("Sheet0")
        ws.write(0, 0, "兴业银行交易明细")
        ws.write(4, 0, "账户账号")
        ws.write(4, 1, "622900000000008888")  # text, as banks always do
        for col, header in enumerate(HEADERS):
            ws.write(10, col, header)
        ws.write(11, 0, "2026-03-15 10:00:00")
        ws.write(11, 1, "2026-03-15")
        ws.write(11, 2, 100.0)  # numeric amount (xlrd returns float)
        ws.write(11, 5, "消费")
        ws.write(12, 0, FOOTER_LABEL)

        file_path = tmp_path / "测试的交易明细 20260305-20260405.xls"
        wb.save(str(file_path))

        provider = CIBDebitProvider()
        transactions = provider.parse(file_path)
        assert len(transactions) == 1
        assert transactions[0].card_last4 == "8888"
        assert transactions[0].amount == Decimal("100")

    def test_missing_account_number_row(self, tmp_path: Path) -> None:
        """Test parsing when the account number metadata row is absent."""
        wb = xlwt.Workbook()
        ws = wb.add_sheet("Sheet0")
        ws.write(0, 0, "兴业银行交易明细")
        # No "账户账号" row
        for col, header in enumerate(HEADERS):
            ws.write(10, col, header)
        ws.write(11, 0, "2026-03-15 10:00:00")
        ws.write(11, 1, "2026-03-15")
        ws.write(11, 2, "100.00")
        ws.write(11, 5, "消费")
        ws.write(12, 0, FOOTER_LABEL)

        file_path = tmp_path / "测试的交易明细 20260305-20260405.xls"
        wb.save(str(file_path))

        provider = CIBDebitProvider()
        transactions = provider.parse(file_path)
        assert len(transactions) == 1
        assert transactions[0].card_last4 is None

    def test_footer_with_colon_variant(self, tmp_path: Path) -> None:
        """Test footer detection with '说明：' variant (startswith match)."""
        wb = xlwt.Workbook()
        ws = wb.add_sheet("Sheet0")
        ws.write(0, 0, "兴业银行交易明细")
        ws.write(4, 0, "账户账号")
        ws.write(4, 1, "622900000000001234")
        for col, header in enumerate(HEADERS):
            ws.write(10, col, header)
        ws.write(11, 0, "2026-03-15 10:00:00")
        ws.write(11, 1, "2026-03-15")
        ws.write(11, 2, "100.00")
        ws.write(11, 5, "消费")
        # Footer uses "说明：" instead of "说明"
        ws.write(12, 0, "说明：交易明细仅供参考。")

        file_path = tmp_path / "测试的交易明细 20260305-20260405.xls"
        wb.save(str(file_path))

        provider = CIBDebitProvider()
        transactions = provider.parse(file_path)
        assert len(transactions) == 1  # footer row should not be parsed

    def test_time_without_seconds(self, tmp_path: Path) -> None:
        """Test parsing datetime with HH:MM format (no seconds)."""
        transactions = [
            {
                "tx_time": "2026-03-15 10:30",
                "post_date": "2026-03-15",
                "expense": "100.00",
                "summary": "消费",
            },
        ]
        file_path = create_cib_xls(tmp_path, transactions)
        provider = CIBDebitProvider()
        parsed = provider.parse(file_path)
        assert len(parsed) == 1
        assert parsed[0].date == date(2026, 3, 15)
        assert parsed[0].time == time(10, 30, 0)  # seconds default to 0

    def test_non_cib_file_rejected(self, tmp_path: Path) -> None:
        """Test that XLS without '兴业银行' in row 0 is rejected."""
        wb = xlwt.Workbook()
        ws = wb.add_sheet("Sheet0")
        ws.write(0, 0, "其他银行交易明细")  # not CIB
        ws.write(4, 0, "账户账号")
        ws.write(4, 1, "622900000000001234")
        for col, header in enumerate(HEADERS):
            ws.write(10, col, header)
        ws.write(11, 0, "2026-03-15 10:00:00")
        ws.write(11, 1, "2026-03-15")
        ws.write(11, 2, "100.00")
        ws.write(11, 5, "消费")
        ws.write(12, 0, FOOTER_LABEL)

        file_path = tmp_path / "测试的交易明细 20260305-20260405.xls"
        wb.save(str(file_path))

        provider = CIBDebitProvider()
        transactions = provider.parse(file_path)
        assert transactions == []
