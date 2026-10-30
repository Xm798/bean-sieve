"""Tests for Bank of China (BOC) credit card statement provider."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import fitz
import pytest

from bean_sieve.core.metadata_keys import STATEMENT_CYCLE
from bean_sieve.providers import get_provider
from bean_sieve.providers.banks.credit.boc import BOCCreditProvider

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "boc_credit"


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

    @staticmethod
    def _can_handle_first_page(tmp_path: Path, text: str) -> bool:
        mock_page = MagicMock()
        mock_page.get_text.return_value = text
        mock_doc = MagicMock()
        mock_doc.page_count = 1
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_doc.__enter__.return_value = mock_doc

        file_path = tmp_path / "2030-01_statement.pdf"
        file_path.write_bytes(b"%PDF-1.4")
        with patch("fitz.open", return_value=mock_doc):
            return BOCCreditProvider.can_handle(file_path)

    def test_can_handle_by_first_page_title(self, tmp_path):
        assert self._can_handle_first_page(tmp_path, "中国银行信用卡账单\n2030-01-02")

    def test_rejects_other_bank_credit_card_statement(self, tmp_path):
        assert not self._can_handle_first_page(
            tmp_path, "浦发银行信用卡账单\n2030-01-02"
        )

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

    def test_filename_period_spans_two_months(self, tmp_path):
        """Filename fallback: span the whole previous-and-current months."""
        file_path = tmp_path / "中国银行信用卡电子合并账单2025年12月账单.PDF"

        assert BOCCreditProvider._filename_period(file_path) == (
            date(2025, 11, 1),
            date(2025, 12, 31),
        )

    def test_closing_date_read_from_the_summary_block(self):
        """Due date then closing date in one block, as the summary prints them."""
        mock_page = MagicMock()
        mock_page.get_text.return_value = [
            (0, 0, 0, 0, "2025-12-25\n2025-12-04\n95.59\nUSD  0.00\n", 0, 0),
        ]
        mock_doc = MagicMock()
        mock_doc.page_count = 1
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)

        assert BOCCreditProvider()._find_closing_date(mock_doc) == date(2025, 12, 4)

    def test_cycle_starts_the_day_after_the_closing_a_month_earlier(self):
        [pdf] = EXAMPLE.glob("*.pdf")

        transactions = BOCCreditProvider().parse(pdf)

        assert transactions[0].metadata[STATEMENT_CYCLE] == (
            date(2030, 2, 5),
            date(2030, 3, 4),
        )

    @staticmethod
    def quiet_statement(path: Path, closing: str, card: str) -> Path:
        """A statement printing its closing date and one card without rows."""
        doc = fitz.open()
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(30, 80, 300, 140), f"2030-02-28\n{closing}\n0.00")
        page.insert_text(
            (30, 200), f"(卡号：000000000000{card})", fontname="china-s", fontsize=9
        )
        doc.save(path)
        doc.close()
        return path

    @pytest.mark.parametrize(
        ("card", "start"),
        [("1234", date(2030, 2, 21)), ("9999", date(2030, 2, 5))],
    )
    @pytest.mark.parametrize("reverse", [False, True])
    def test_a_quiet_statement_anchors_only_its_own_card(
        self, tmp_path, card, start, reverse
    ):
        """BOC bills per card: the billing day moved for card 1234 only."""
        [example] = EXAMPLE.glob("*.pdf")
        previous = self.quiet_statement(tmp_path / "previous.pdf", "2030-02-20", card)
        provider = BOCCreditProvider()
        paths = [previous, example]
        parsed = {p: provider.parse(p) for p in (paths[::-1] if reverse else paths)}

        by_card = {t.card_last4: t.metadata[STATEMENT_CYCLE] for t in parsed[example]}
        assert by_card["1234"] == (start, date(2030, 3, 4))


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

    def test_group_by_row_adjacent_rows_not_merged(self):
        """Tightly-spaced rows must stay separate when each is date-anchored.

        Regression: two consecutive same-day rows only ~16pt apart were merged
        by the flat tolerance (20pt), and the second amount overwrote the first,
        dropping one transaction entirely.
        """
        provider = BOCCreditProvider()
        # Synthetic blocks: (y0, x1, x0, content). The two anchors are 16pt
        # apart (< the 20pt tolerance) to reproduce the merge.
        blocks = [
            (100.0, 452.7, 42.1, "2030-01-02\n2030-01-02\n1234\npayee-a\n10.00"),
            (116.0, 452.7, 42.1, "2030-01-02\n2030-01-02\n1234\npayee-b\n20.00"),
        ]
        result = provider._group_by_row(blocks)
        assert len(result) == 2

    def test_group_by_row_wrapped_description_attaches(self):
        """A description wrapping onto a second visual line stays in its row."""
        provider = BOCCreditProvider()
        blocks = [
            (200.0, 260.3, 42.1, "2030-01-02\n2030-01-03\n1234"),  # anchor
            (190.0, 390.0, 298.6, "payee-long-part-one"),  # desc line 1 (above)
            (200.0, 539.0, 319.3, "part-two\n30.00"),  # desc line 2 + expense
        ]
        result = provider._group_by_row(blocks)
        assert len(result) == 1
        assert len(result[0]) == 3


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
        )

        assert result is not None
        assert result.amount == Decimal("12345.67")


class TestBOCTableBoundaries:
    """Tests for locating transaction tables in a merged statement."""

    def test_next_card_header_ends_table(self, tmp_path):
        """A card section following a table on the same page is not a row."""

        def block(x0, y0, x1, text):
            return (x0, y0, x1, y0 + 10, text, 0, 0)

        mock_page = MagicMock()
        mock_page.get_text.return_value = [
            block(20, 100, 200, "卡片A(卡号：1111)\n"),
            block(500, 140, 550, "Expenditure\n"),
            block(40, 200, 450, "2030-01-02\n2030-01-03\n1111\npayee-a\n30.00\n"),
            block(20, 240, 200, "卡片B(卡号：2222)\n"),
            block(500, 300, 560, "Available Balance\n"),
            block(40, 330, 550, "外币/USD\n0.00\n0.00\n40.00\n"),
        ]

        mock_doc = MagicMock()
        mock_doc.page_count = 1
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)

        file_path = tmp_path / "中国银行信用卡电子合并账单2030年01月账单.PDF"
        file_path.write_bytes(b"%PDF-1.4")

        with patch("fitz.open", return_value=mock_doc):
            transactions = BOCCreditProvider().parse(file_path)

        assert len(transactions) == 1
        txn = transactions[0]
        assert txn.amount == Decimal("-30.00")
        assert txn.description == "payee-a"
        assert txn.card_last4 == "1111"

    def test_trailing_summary_without_loyalty_section_is_dropped(self, tmp_path):
        """A summary after the last card's table stays out of its last row."""

        def block(x0, y0, x1, text):
            return (x0, y0, x1, y0 + 10, text, 0, 0)

        mock_page = MagicMock()
        mock_page.get_text.return_value = [
            block(20, 100, 200, "卡片A(卡号：1111)\n"),
            block(500, 140, 550, "Expenditure\n"),
            block(40, 200, 450, "2030-01-02\n2030-01-03\n1111\npayee-a\n30.00\n"),
            block(500, 300, 560, "Available Balance\n"),
            block(40, 330, 550, "外币/USD\n0.00\n0.00\n40.00\n"),
        ]

        mock_doc = MagicMock()
        mock_doc.page_count = 1
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)

        file_path = tmp_path / "中国银行信用卡电子合并账单2030年01月账单.PDF"
        file_path.write_bytes(b"%PDF-1.4")

        with patch("fitz.open", return_value=mock_doc):
            transactions = BOCCreditProvider().parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("-30.00")
        assert transactions[0].description == "payee-a"
