"""Tests for China CITIC Bank (中信银行) credit card statement provider."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.providers import get_provider
from bean_sieve.providers.banks.credit.citic import CITICCreditProvider


def create_citic_xls(rows: list[list], path: Path) -> Path:
    """Create a mock CITIC credit card XLS file.

    Args:
        rows: List of data rows (without title and header).
              Each row: [交易日期, 入账日期, 交易描述, 卡末四位, 交易币种, 结算币种, 交易金额, 结算金额]
        path: Path to write the XLS file.
    """
    try:
        import xlwt

        wb = xlwt.Workbook()
        ws = wb.add_sheet("Sheet1")

        # Row 0: Title
        ws.write(0, 0, "本期账单明细(人民币)")

        # Row 1: Headers
        headers = [
            "交易日期",
            "入账日期",
            "交易描述",
            "卡末四位",
            "交易币种",
            "结算币种",
            "交易金额",
            "结算金额",
        ]
        for col, header in enumerate(headers):
            ws.write(1, col, header)

        # Data rows
        for row_idx, row_data in enumerate(rows):
            for col, value in enumerate(row_data):
                ws.write(row_idx + 2, col, value)

        wb.save(str(path))
    except ImportError:
        pytest.skip("xlwt not installed, skipping XLS creation tests")

    return path


@pytest.fixture
def sample_rows():
    """Sample CITIC credit card statement data rows."""
    return [
        [
            "2026-01-15",
            "2026-01-15",
            "财付通－测试超市",
            "8888",
            "人民币",
            "人民币",
            "56.00",
            "56.00",
        ],
        [
            "2026-01-12",
            "2026-01-12",
            "支付宝－测试餐厅",
            "8888",
            "人民币",
            "人民币",
            "128.50",
            "128.50",
        ],
        [
            "2026-01-08",
            "2026-01-08",
            "支付宝还款",
            "8888",
            "人民币",
            "人民币",
            "-5000.00",
            "-5000.00",
        ],
        [
            "2026-01-05",
            "2026-01-06",
            "年费",
            "8888",
            "人民币",
            "人民币",
            "200.00",
            "200.00",
        ],
    ]


@pytest.fixture
def citic_xls_file(tmp_path, sample_rows):
    """Create a temporary CITIC XLS file."""
    return create_citic_xls(sample_rows, tmp_path / "已出账单明细.xls")


class TestCITICCreditProvider:
    """Tests for CITICCreditProvider."""

    def test_provider_registration(self):
        """Test that CITIC provider is properly registered."""
        provider = get_provider("citic_credit")
        assert isinstance(provider, CITICCreditProvider)
        assert provider.provider_id == "citic_credit"
        assert provider.provider_name == "中信银行信用卡"
        assert ".xls" in provider.supported_formats

    def test_can_handle(self):
        """Test file format detection."""
        assert CITICCreditProvider.can_handle(Path("已出账单明细.xls"))
        assert CITICCreditProvider.can_handle(Path("已出账单明细 (1).xls"))
        assert CITICCreditProvider.can_handle(Path("中信信用卡账单.xls"))
        assert not CITICCreditProvider.can_handle(Path("已出账单明细.csv"))
        assert not CITICCreditProvider.can_handle(Path("statement.xls"))

    def test_per_card_statement(self):
        """Test that per_card_statement is True for CITIC."""
        provider = CITICCreditProvider()
        assert provider.per_card_statement is True

    def test_parse_transactions(self, citic_xls_file):
        """Test parsing transactions from XLS file."""
        provider = CITICCreditProvider()
        transactions = provider.parse(citic_xls_file)

        assert len(transactions) == 4

        # Expense transaction
        txn1 = transactions[0]
        assert txn1.date == date(2026, 1, 15)
        assert txn1.post_date == date(2026, 1, 15)
        assert txn1.amount == Decimal("56.00")
        assert txn1.currency == "CNY"
        assert txn1.card_last4 == "8888"
        assert "财付通" in txn1.description
        assert "测试超市" in txn1.description
        assert txn1.provider == "citic_credit"
        assert txn1.is_expense

        # Another expense
        txn2 = transactions[1]
        assert txn2.date == date(2026, 1, 12)
        assert txn2.amount == Decimal("128.50")
        assert "支付宝" in txn2.description

        # Payment (negative amount = income)
        txn3 = transactions[2]
        assert txn3.date == date(2026, 1, 8)
        assert txn3.amount == Decimal("-5000.00")
        assert "还款" in txn3.description
        assert txn3.is_income

        # Fee with different post date
        txn4 = transactions[3]
        assert txn4.date == date(2026, 1, 5)
        assert txn4.post_date == date(2026, 1, 6)
        assert txn4.amount == Decimal("200.00")
        assert "年费" in txn4.description

    def test_statement_period_inferred(self, citic_xls_file):
        """Test that statement_period is inferred from transaction date range."""
        provider = CITICCreditProvider()
        transactions = provider.parse(citic_xls_file)

        assert len(transactions) == 4
        for txn in transactions:
            assert txn.statement_period is not None
            assert txn.statement_period == (date(2026, 1, 5), date(2026, 1, 15))

    def test_parse_empty_statement(self, tmp_path):
        """Test handling of statement with no data rows."""
        file_path = create_citic_xls([], tmp_path / "已出账单明细.xls")
        provider = CITICCreditProvider()
        transactions = provider.parse(file_path)
        assert transactions == []

    def test_parse_amount_with_commas(self, tmp_path):
        """Test parsing amounts with thousand separators."""
        rows = [
            [
                "2026-01-10",
                "2026-01-10",
                "大额消费",
                "8888",
                "人民币",
                "人民币",
                "12,345.67",
                "12,345.67",
            ],
        ]
        file_path = create_citic_xls(rows, tmp_path / "已出账单明细.xls")

        provider = CITICCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("12345.67")

    def test_currency_mapping(self):
        """Test Chinese currency name to ISO code mapping."""
        assert CITICCreditProvider._map_currency("人民币") == "CNY"
        assert CITICCreditProvider._map_currency("美元") == "USD"
        assert CITICCreditProvider._map_currency("EUR") == "EUR"

    def test_single_card_transactions(self, tmp_path):
        """Test parsing file with transactions for a single card."""
        rows = [
            [
                "2026-01-15",
                "2026-01-15",
                "消费A",
                "1234",
                "人民币",
                "人民币",
                "100.00",
                "100.00",
            ],
            [
                "2026-01-14",
                "2026-01-14",
                "消费B",
                "1234",
                "人民币",
                "人民币",
                "200.00",
                "200.00",
            ],
        ]
        file_path = create_citic_xls(rows, tmp_path / "已出账单明细.xls")

        provider = CITICCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 2
        assert all(t.card_last4 == "1234" for t in transactions)


class TestCITICEdgeCases:
    """Tests for edge cases in CITIC statement parsing."""

    def test_skip_invalid_rows(self, tmp_path):
        """Test that rows with invalid data are skipped."""
        rows = [
            [
                "2026-01-15",
                "2026-01-15",
                "正常消费",
                "8888",
                "人民币",
                "人民币",
                "100.00",
                "100.00",
            ],
            ["", "", "", "", "", "", "", ""],  # Empty row
            [
                "2026-01-10",
                "2026-01-10",
                "另一笔消费",
                "8888",
                "人民币",
                "人民币",
                "50.00",
                "50.00",
            ],
        ]
        file_path = create_citic_xls(rows, tmp_path / "已出账单明细.xls")

        provider = CITICCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 2
        assert transactions[0].amount == Decimal("100.00")
        assert transactions[1].amount == Decimal("50.00")

    def test_numeric_cell_values(self, tmp_path):
        """Test parsing when xlrd returns floats instead of strings."""
        import xlwt

        wb = xlwt.Workbook()
        ws = wb.add_sheet("Sheet1")
        ws.write(0, 0, "本期账单明细(人民币)")
        headers = [
            "交易日期",
            "入账日期",
            "交易描述",
            "卡末四位",
            "交易币种",
            "结算币种",
            "交易金额",
            "结算金额",
        ]
        for col, h in enumerate(headers):
            ws.write(1, col, h)
        # Write card_last4 and amounts as numbers to simulate numeric cells
        ws.write(2, 0, "2026-01-15")
        ws.write(2, 1, "2026-01-15")
        ws.write(2, 2, "Test merchant")
        ws.write(2, 3, 8888)  # numeric card_last4
        ws.write(2, 4, "人民币")
        ws.write(2, 5, "人民币")
        ws.write(2, 6, 56.0)  # numeric amount
        ws.write(2, 7, 56.0)  # numeric amount
        path = tmp_path / "已出账单明细.xls"
        wb.save(str(path))

        provider = CITICCreditProvider()
        txns = provider.parse(path)

        assert len(txns) == 1
        assert txns[0].card_last4 == "8888"  # not "8888.0"
        assert txns[0].amount == Decimal("56.0")

    def test_foreign_currency_transaction(self, tmp_path):
        """Test parsing foreign currency transaction with CNY settlement."""
        rows = [
            [
                "2026-01-20",
                "2026-01-21",
                "AMAZON US",
                "8888",
                "美元",
                "人民币",
                "49.99",
                "362.50",
            ],
        ]
        file_path = create_citic_xls(rows, tmp_path / "已出账单明细.xls")

        provider = CITICCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].currency == "CNY"  # settlement currency
        assert transactions[0].amount == Decimal("362.50")  # settlement amount

    def test_fewer_columns(self, tmp_path):
        """Test handling of file with fewer than expected columns."""
        import xlwt

        wb = xlwt.Workbook()
        ws = wb.add_sheet("Sheet1")
        ws.write(0, 0, "本期账单明细(人民币)")
        ws.write(1, 0, "交易日期")
        ws.write(1, 1, "入账日期")
        ws.write(1, 2, "交易描述")
        # Only 3 columns
        wb.save(str(tmp_path / "已出账单明细.xls"))

        provider = CITICCreditProvider()
        transactions = provider.parse(tmp_path / "已出账单明细.xls")
        assert transactions == []
