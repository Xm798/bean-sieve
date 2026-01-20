"""Tests for Bank of China (BOC) credit card statement provider."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from bean_sieve.providers import get_provider
from bean_sieve.providers.banks.credit.boc import BOCCreditProvider


class TestBOCCreditProvider:
    """Tests for BOCCreditProvider."""

    def test_provider_registration(self):
        """Test that BOC provider is properly registered."""
        provider = get_provider("boc_credit")
        assert isinstance(provider, BOCCreditProvider)
        assert provider.provider_id == "boc_credit"
        assert provider.provider_name == "中国银行信用卡"
        assert ".pdf" in provider.supported_formats

    def test_can_handle(self):
        """Test file format detection."""
        assert BOCCreditProvider.can_handle(
            Path("中国银行信用卡电子合并账单2025年12月账单.PDF")
        )
        assert BOCCreditProvider.can_handle(Path("中国银行信用卡账单.pdf"))
        assert not BOCCreditProvider.can_handle(Path("boc_statement.csv"))
        assert not BOCCreditProvider.can_handle(Path("statement.pdf"))

    def test_per_card_statement_flag(self):
        """Test that per_card_statement is set correctly."""
        provider = BOCCreditProvider()
        assert provider.per_card_statement is True


class TestBOCDateParsing:
    """Tests for BOC date parsing methods."""

    def test_parse_date_valid(self):
        """Test parsing valid date string."""
        provider = BOCCreditProvider()
        result = provider._parse_date("2025-12-15")
        assert result == date(2025, 12, 15)

    def test_parse_date_invalid(self):
        """Test parsing invalid date string."""
        provider = BOCCreditProvider()
        assert provider._parse_date("invalid") is None
        assert provider._parse_date("") is None
        assert provider._parse_date(None) is None

    def test_parse_date_malformed(self):
        """Test parsing malformed date string."""
        provider = BOCCreditProvider()
        assert provider._parse_date("2025-13-01") is None  # Invalid month
        assert provider._parse_date("2025-12-32") is None  # Invalid day


class TestBOCStatementPeriodExtraction:
    """Tests for statement period extraction from filename."""

    def test_extract_period_from_filename(self, tmp_path):
        """Test extracting statement period from filename pattern."""
        # Create mock document
        mock_page = MagicMock()
        mock_page.get_text.return_value = ""

        mock_doc = MagicMock()
        mock_doc.page_count = 1
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)

        file_path = tmp_path / "中国银行信用卡电子合并账单2025年12月账单.PDF"
        file_path.write_bytes(b"%PDF-1.4")

        provider = BOCCreditProvider()
        with patch("fitz.open", return_value=mock_doc):
            period = provider._extract_statement_period(mock_doc, file_path)

        assert period is not None
        start, end = period
        assert end.month == 12
        assert end.year == 2025
        # Period should be previous month's 5th to this month's 4th
        assert end.day == 4
        assert start.month == 11
        assert start.day == 5


class TestBOCRowGrouping:
    """Tests for row grouping logic."""

    def test_group_by_row_empty(self):
        """Test grouping empty block list."""
        provider = BOCCreditProvider()
        result = provider._group_by_row([])
        assert result == []

    def test_group_by_row_single_block(self):
        """Test grouping single block."""
        provider = BOCCreditProvider()
        blocks = [(100.0, 500.0, 50.0, "test content")]
        result = provider._group_by_row(blocks)
        assert len(result) == 1
        assert result[0] == [(500.0, 50.0, "test content")]

    def test_group_by_row_same_row(self):
        """Test grouping blocks on same row."""
        provider = BOCCreditProvider()
        blocks = [
            (100.0, 100.0, 50.0, "col1"),
            (100.0, 200.0, 150.0, "col2"),
            (100.0, 300.0, 250.0, "col3"),
        ]
        result = provider._group_by_row(blocks)
        assert len(result) == 1
        assert len(result[0]) == 3

    def test_group_by_row_different_rows(self):
        """Test grouping blocks on different rows."""
        provider = BOCCreditProvider()
        blocks = [
            (100.0, 100.0, 50.0, "row1"),
            (150.0, 100.0, 50.0, "row2"),  # 50 pixels apart, > tolerance
            (200.0, 100.0, 50.0, "row3"),
        ]
        result = provider._group_by_row(blocks)
        assert len(result) == 3


class TestBOCTransactionRowParsing:
    """Tests for individual transaction row parsing."""

    def test_parse_transaction_row_valid(self, tmp_path):
        """Test parsing a valid transaction row."""
        provider = BOCCreditProvider()

        file_path = tmp_path / "test.pdf"
        file_path.write_bytes(b"%PDF-1.4")

        # Row blocks: (x1, x0, content) sorted by x0
        row_blocks = [
            (100.0, 50.0, "2025-12-15"),  # Trans date
            (180.0, 120.0, "2025-12-15"),  # Post date
            (250.0, 200.0, "1234"),  # Card last 4
            (400.0, 270.0, "McDonald's Beijing"),  # Description
            (560.0, 510.0, "45.00"),  # Expense (x1 > 500)
        ]

        result = provider._parse_transaction_row(
            row_blocks,
            "1234",
            file_path,
            0,
            (date(2025, 11, 5), date(2025, 12, 4)),
        )

        assert result is not None
        assert result.date == date(2025, 12, 15)
        assert result.amount == Decimal("45.00")
        assert result.card_last4 == "1234"
        assert "McDonald's" in result.description

    def test_parse_transaction_row_deposit(self, tmp_path):
        """Test parsing a deposit (refund) transaction row."""
        provider = BOCCreditProvider()

        file_path = tmp_path / "test.pdf"
        file_path.write_bytes(b"%PDF-1.4")

        # Row blocks with deposit (x1 < 500)
        row_blocks = [
            (100.0, 50.0, "2025-12-15"),  # Trans date
            (180.0, 120.0, "2025-12-15"),  # Post date
            (250.0, 200.0, "1234"),  # Card last 4
            (400.0, 270.0, "Refund"),  # Description
            (470.0, 420.0, "100.00"),  # Deposit (x1 between 400-500)
        ]

        result = provider._parse_transaction_row(
            row_blocks,
            "1234",
            file_path,
            0,
            None,
        )

        assert result is not None
        assert result.amount == Decimal("-100.00")  # Deposit is negative

    def test_parse_transaction_row_no_date(self, tmp_path):
        """Test parsing row without date returns None."""
        provider = BOCCreditProvider()

        file_path = tmp_path / "test.pdf"
        file_path.write_bytes(b"%PDF-1.4")

        row_blocks = [
            (250.0, 200.0, "1234"),
            (400.0, 270.0, "Some description"),
            (560.0, 510.0, "45.00"),
        ]

        result = provider._parse_transaction_row(
            row_blocks,
            "1234",
            file_path,
            0,
            None,
        )

        assert result is None

    def test_parse_transaction_row_with_comma_amount(self, tmp_path):
        """Test parsing amount with comma separator."""
        provider = BOCCreditProvider()

        file_path = tmp_path / "test.pdf"
        file_path.write_bytes(b"%PDF-1.4")

        row_blocks = [
            (100.0, 50.0, "2025-12-15"),
            (180.0, 120.0, "2025-12-15"),
            (250.0, 200.0, "1234"),
            (400.0, 270.0, "Large Purchase"),
            (560.0, 510.0, "12,345.67"),  # Amount with comma
        ]

        result = provider._parse_transaction_row(
            row_blocks,
            "1234",
            file_path,
            0,
            None,
        )

        assert result is not None
        assert result.amount == Decimal("12345.67")
