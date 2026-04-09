"""Tests for ZA Bank (众安银行) statement provider."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from bean_sieve.core.types import Transaction
from bean_sieve.providers import get_provider
from bean_sieve.providers.banks.debit.zabank import ZABankProvider


class TestZABankProvider:
    """Tests for ZABankProvider registration and detection."""

    def test_provider_registration(self):
        """Test that ZABank provider is properly registered."""
        provider = get_provider("zabank_debit")
        assert isinstance(provider, ZABankProvider)
        assert provider.provider_id == "zabank_debit"
        assert provider.provider_name == "众安银行"
        assert ".pdf" in provider.supported_formats

    def test_can_handle_by_keyword(self):
        """Test file detection by keyword in filename."""
        assert ZABankProvider.can_handle(Path("zabank_202603.pdf"))
        assert ZABankProvider.can_handle(Path("众安银行月结单.pdf"))
        assert ZABankProvider.can_handle(Path("ZA Bank Statement.pdf"))

    def test_cannot_handle_other(self):
        """Test non-matching files are rejected."""
        assert not ZABankProvider.can_handle(Path("statement.csv"))
        assert not ZABankProvider.can_handle(Path("other_bank.pdf"))
        # Generic "Statement_" should NOT match without ZABank keyword
        assert not ZABankProvider.can_handle(Path("Statement_202603.pdf"))


class TestZABankDateParsing:
    """Tests for date parsing."""

    def test_parse_date_valid(self):
        """Test parsing 'dd Mon yyyy' format."""
        assert ZABankProvider._parse_date("28 Mar 2026") == date(2026, 3, 28)
        assert ZABankProvider._parse_date("1 Jan 2026") == date(2026, 1, 1)
        assert ZABankProvider._parse_date("31 Dec 2025") == date(2025, 12, 31)

    def test_parse_date_invalid(self):
        """Test invalid date strings."""
        assert ZABankProvider._parse_date("") is None
        assert ZABankProvider._parse_date("2026-03-28") is None
        assert ZABankProvider._parse_date("28 Xyz 2026") is None

    def test_parse_date_invalid_day(self):
        """Test invalid day values."""
        assert ZABankProvider._parse_date("32 Mar 2026") is None


class TestZABankAmountParsing:
    """Tests for amount parsing."""

    def test_withdrawal_positive(self):
        """Withdrawal should be positive (expense)."""
        assert ZABankProvider._parse_amount("", "183.06") == Decimal("183.06")

    def test_deposit_negative(self):
        """Deposit should be negative (income)."""
        assert ZABankProvider._parse_amount("10,000.00", "") == Decimal("-10000.00")

    def test_no_amount(self):
        """No deposit or withdrawal returns None."""
        assert ZABankProvider._parse_amount("", "") is None

    def test_amount_with_comma(self):
        """Amounts with thousand separators."""
        assert ZABankProvider._parse_amount("", "40,000.00") == Decimal("40000.00")
        assert ZABankProvider._parse_amount("1,766.31", "") == Decimal("-1766.31")

    def test_both_deposit_and_withdrawal(self):
        """When both are present, withdrawal takes precedence."""
        result = ZABankProvider._parse_amount("500.00", "200.00")
        assert result == Decimal("200.00")


class TestZABankDetailExtraction:
    """Tests for transaction detail line parsing."""

    def test_extract_order_id_next_line(self):
        """Test extracting transaction number from the next line."""
        lines = [
            "货币兑换",
            "sell卖出CNY100.00/buy买入HKD112.38",
            "Exchange rate汇率:1HKD=0.889792CNY",
            "Transaction number交易编号:",
            "20260101000000000000001",
        ]
        assert ZABankProvider._extract_order_id(lines) == "20260101000000000000001"

    def test_extract_order_id_same_line(self):
        """Test extracting transaction number on the same line as the label."""
        lines = ["Transaction number交易编号:20260101000000000000001"]
        assert ZABankProvider._extract_order_id(lines) == "20260101000000000000001"

    def test_extract_order_id_none(self):
        """Test no order ID for non-exchange transactions."""
        lines = ["存入", "TEST USER 622***********1234"]
        assert ZABankProvider._extract_order_id(lines) is None

    def test_extract_order_id_empty_lines(self):
        """Test with empty detail lines."""
        assert ZABankProvider._extract_order_id([]) is None

    def test_extract_exchange_info(self):
        """Test extracting exchange rate info."""
        lines = [
            "货币兑换",
            "sell卖出CNY100.00/buy买入HKD112.38",
            "Exchange rate汇率:1HKD=0.889792CNY",
        ]
        result = ZABankProvider._extract_exchange_info(lines)
        assert result == "sell卖出CNY100.00/buy买入HKD112.38"

    def test_extract_counterparty_ascii(self):
        """Test extracting ASCII-name counterparty from transfer details."""
        lines = ["存入", "TEST USER 622***********1234"]
        result = ZABankProvider._extract_counterparty(lines)
        assert result == "TEST USER 622***********1234"

    def test_extract_counterparty_chinese(self):
        """Test extracting Chinese-name counterparty."""
        lines = ["转出", "陈*明 613*****8888"]
        result = ZABankProvider._extract_counterparty(lines)
        assert result == "陈*明 613*****8888"

    def test_extract_counterparty_none_for_exchange(self):
        """No counterparty for currency exchange transactions."""
        lines = [
            "货币兑换",
            "sell卖出CNY100.00/buy买入HKD112.38",
            "Exchange rate汇率:1HKD=0.889792CNY",
            "Transaction number交易编号:",
            "20260101000000000000001",
        ]
        assert ZABankProvider._extract_counterparty(lines) is None


class TestZABankRowParsing:
    """Tests for _parse_row."""

    def setup_method(self):
        self.provider = ZABankProvider()
        self.file_path = Path("/tmp/test.pdf")
        self.period = (date(2026, 3, 1), date(2026, 3, 31))

    def test_parse_simple_withdrawal(self):
        """Test parsing a simple withdrawal row."""
        row = ["28 Mar 2026", "WeChat Pay Hong Kong", "", "183.06", "448.45"]
        txn = self.provider._parse_row(row, 0, "HKD", self.file_path, self.period)

        assert txn is not None
        assert txn.date == date(2026, 3, 28)
        assert txn.amount == Decimal("183.06")
        assert txn.currency == "HKD"
        assert txn.description == "WeChat Pay Hong Kong"
        assert txn.statement_period == self.period

    def test_parse_deposit(self):
        """Test parsing a deposit (income) row."""
        row = [
            "27 Mar 2026",
            "Payment Connect inward transfer\n存入\n"
            "TEST USER 622***********1234",
            "10,000.00",
            "",
            "10,000.00",
        ]
        txn = self.provider._parse_row(row, 0, "CNY", self.file_path, self.period)

        assert txn is not None
        assert txn.amount == Decimal("-10000.00")
        assert txn.currency == "CNY"
        assert txn.description == "Payment Connect inward transfer"
        assert txn.payee == "TEST USER 622***********1234"

    def test_parse_currency_exchange(self):
        """Test parsing currency exchange with transaction number."""
        row = [
            "28 Mar 2026",
            "Currency Exchange\n货币兑换\n"
            "sell卖出CNY100.00/buy买入HKD112.38\n"
            "Exchange rate汇率:1HKD=0.889792CNY\n"
            "Transaction number交易编号:\n"
            "20260101000000000000001",
            "112.38",
            "",
            "199.30",
        ]
        txn = self.provider._parse_row(row, 0, "HKD", self.file_path, self.period)

        assert txn is not None
        assert txn.amount == Decimal("-112.38")
        assert txn.order_id == "20260101000000000000001"
        assert txn.metadata.get("exchange_info") == "sell卖出CNY100.00/buy买入HKD112.38"

    def test_skip_opening_balance(self):
        """Test that opening balance rows are skipped."""
        row = ["28 Feb 2026", "Opening balance 上期结余", "", "", "631.51"]
        txn = self.provider._parse_row(row, 0, "HKD", self.file_path, self.period)
        assert txn is None

    def test_skip_invalid_row(self):
        """Test that rows with missing data are skipped."""
        assert self.provider._parse_row([], 0, "HKD", self.file_path, None) is None
        assert (
            self.provider._parse_row(
                ["", "", "", "", ""], 0, "HKD", self.file_path, None
            )
            is None
        )
        assert (
            self.provider._parse_row(
                [None, None, None, None, None], 0, "HKD", self.file_path, None
            )
            is None
        )

    def test_skip_short_row(self):
        """Test that rows with fewer than 5 columns are skipped."""
        assert (
            self.provider._parse_row(
                ["28 Mar 2026", "desc", "100"], 0, "HKD", self.file_path, None
            )
            is None
        )

    def test_balance_in_metadata(self):
        """Test that balance is stored in metadata."""
        row = ["28 Mar 2026", "WeChat Pay Hong Kong", "", "183.06", "448.45"]
        txn = self.provider._parse_row(row, 0, "HKD", self.file_path, self.period)
        assert txn is not None
        assert txn.metadata["balance"] == "448.45"


class TestZABankExchangeMerge:
    """Tests for currency exchange pair merging."""

    def test_merge_exchange_pairs(self):
        """Test that paired exchange transactions are merged with @@ price."""
        sell = Transaction(
            date=date(2026, 3, 31),
            amount=Decimal("1766.31"),
            currency="CNY",
            description="Currency exchange",
            order_id="20260101000000000000001",
            card_last4="CNY",
            provider="zabank_debit",
            metadata={"exchange_info": "sell卖出CNY1,766.31/buy买入HKD2,000.00"},
        )
        buy = Transaction(
            date=date(2026, 3, 31),
            amount=Decimal("-2000.00"),
            currency="HKD",
            description="Currency Exchange",
            order_id="20260101000000000000001",
            card_last4="HKD",
            provider="zabank_debit",
            metadata={"exchange_info": "sell卖出CNY1,766.31/buy买入HKD2,000.00"},
        )
        normal = Transaction(
            date=date(2026, 3, 28),
            amount=Decimal("183.06"),
            currency="HKD",
            description="WeChat Pay Hong Kong",
            card_last4="HKD",
            provider="zabank_debit",
        )

        result = ZABankProvider._merge_exchange_pairs([normal, sell, buy])

        assert len(result) == 2
        # Normal transaction preserved
        assert result[0].description == "WeChat Pay Hong Kong"
        # Merged exchange: sell side kept with price from buy side
        merged = result[1]
        assert merged.amount == Decimal("1766.31")
        assert merged.currency == "CNY"
        assert merged.price_amount == Decimal("2000.00")
        assert merged.price_currency == "HKD"
        assert "CNY→HKD" in merged.description

    def test_unpaired_exchange_kept(self):
        """Test that unpaired exchange transactions are kept as-is."""
        lone = Transaction(
            date=date(2026, 3, 31),
            amount=Decimal("100.00"),
            currency="CNY",
            description="Currency exchange",
            order_id="20260101000000000000002",
            card_last4="CNY",
            provider="zabank_debit",
        )
        result = ZABankProvider._merge_exchange_pairs([lone])
        assert len(result) == 1
        assert result[0].price_amount is None


class TestZABankSectionDetection:
    """Tests for currency section detection."""

    def test_is_summary_table(self):
        """Test identifying the deposit summary table."""
        mock_table = MagicMock()
        mock_table.extract.return_value = [
            ["", "HKD Savings 港元活期储蓄 (0.20%)", "18.55"]
        ]
        assert ZABankProvider._is_summary_table(mock_table)

    def test_is_not_summary_table(self):
        """Test that transaction tables are not marked as summary."""
        mock_table = MagicMock()
        mock_table.extract.return_value = [
            ["28 Mar 2026", "WeChat Pay Hong Kong", "", "183.06", "448.45"]
        ]
        assert not ZABankProvider._is_summary_table(mock_table)

    def test_is_not_summary_table_savings_in_description(self):
        """Test that 'Savings' in a transaction description is not a false positive."""
        mock_table = MagicMock()
        mock_table.extract.return_value = [
            ["28 Mar 2026", "HKD Savings Interest", "0.01", "", "631.52"]
        ]
        assert not ZABankProvider._is_summary_table(mock_table)

    def test_get_currency_for_position(self):
        """Test resolving currency by y-position."""
        positions = [(100.0, "HKD"), (400.0, "CNY"), (700.0, "USD")]

        # Before first marker
        assert ZABankProvider._get_currency_for_position(50.0, positions) is None
        # In HKD section
        assert ZABankProvider._get_currency_for_position(200.0, positions) == "HKD"
        # In CNY section
        assert ZABankProvider._get_currency_for_position(500.0, positions) == "CNY"
        # In USD section
        assert ZABankProvider._get_currency_for_position(800.0, positions) == "USD"

    def test_get_currency_empty_positions(self):
        """Test with no section positions."""
        assert ZABankProvider._get_currency_for_position(100.0, []) is None


class TestZABankStatementPeriod:
    """Tests for statement period extraction."""

    def test_extract_period(self):
        """Test extracting period from PDF header text."""
        provider = ZABankProvider()
        mock_page = MagicMock()
        mock_page.get_text.return_value = (
            "CONSOLIDATED MONTHLY STATEMENT 综合月结单\n01 Mar 2026 - 31 Mar 2026\n"
        )
        mock_doc = MagicMock()
        mock_doc.page_count = 1
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)

        result = provider._extract_statement_period(mock_doc)
        assert result == (date(2026, 3, 1), date(2026, 3, 31))

    def test_extract_period_no_match(self):
        """Test when no period is found."""
        provider = ZABankProvider()
        mock_page = MagicMock()
        mock_page.get_text.return_value = "Some other content"
        mock_doc = MagicMock()
        mock_doc.page_count = 1
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)

        assert provider._extract_statement_period(mock_doc) is None


class TestZABankContentDetection:
    """Tests for _match_content."""

    def test_match_content_zabank(self, tmp_path):
        """Test content detection for ZA Bank PDFs."""
        mock_page = MagicMock()
        mock_page.get_text.return_value = (
            "ZA Bank Limited 众安银行有限公司\nCONSOLIDATED MONTHLY STATEMENT\n"
        )
        mock_doc = MagicMock()
        mock_doc.page_count = 1
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_doc.close = MagicMock()

        with patch("fitz.open", return_value=mock_doc):
            assert ZABankProvider._match_content(tmp_path / "test.pdf")

    def test_no_match_other_bank(self, tmp_path):
        """Test that other bank PDFs are not matched."""
        mock_page = MagicMock()
        mock_page.get_text.return_value = "中国银行信用卡账单"
        mock_doc = MagicMock()
        mock_doc.page_count = 1
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_doc.close = MagicMock()

        with patch("fitz.open", return_value=mock_doc):
            assert not ZABankProvider._match_content(tmp_path / "test.pdf")
