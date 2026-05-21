"""Tests for HSBC Hong Kong credit card provider."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.providers import auto_detect_provider, get_provider
from bean_sieve.providers.banks.credit.hsbchk import HSBCHKCreditProvider

HEADER = (
    "Transaction date,Post date,Description,Billing amount,Billing currency,"
    "Transaction status,Merchant name,Country / region,Area / district,Credit / Debit"
)


def write_csv(
    tmp_path: Path,
    body_lines: list[str],
    filename: str = "TransactionHistory.csv",
) -> Path:
    file_path = tmp_path / filename
    content = HEADER + "\n" + "\n".join(body_lines) + "\n"
    file_path.write_text(content, encoding="utf-8")
    return file_path


@pytest.fixture
def sample_rows() -> list[str]:
    # Synthetic data only — clearly fake dates and merchants.
    return [
        "02/04/2030,03/04/2030,QR       merchant-a                CHN          CN,"
        '"-100.00"\t,CNY,POSTED,merchant-a,CHINA,CHN,DEBIT',
        "03/04/2030,04/04/2030,RETURN:  merchant-b                CHN          CN,"
        '"50.00"\t,CNY,POSTED,merchant-b,CHINA,CHN,CREDIT',
        '05/04/2030,05/04/2030,PAYMENT - THANK YOU,"1,000.00"\t,CNY,POSTED,,,,CREDIT',
        "10/04/2030,11/04/2030,APPLEPAY merchant-c                CHN          CN,"
        '"-25.50"\t,CNY,POSTED,merchant-c,CHINA,CHN,DEBIT',
    ]


class TestHSBCHKCreditProvider:
    def test_provider_registration(self) -> None:
        provider = get_provider("hsbchk_credit")
        assert isinstance(provider, HSBCHKCreditProvider)
        assert provider.provider_id == "hsbchk_credit"
        assert provider.per_card_statement is True
        assert ".csv" in provider.supported_formats

    def test_can_handle_by_content(self, tmp_path: Path) -> None:
        f = write_csv(tmp_path, [])
        assert HSBCHKCreditProvider.can_handle(f)

    def test_can_handle_with_card_suffix_filename(self, tmp_path: Path) -> None:
        f = write_csv(tmp_path, [], filename="TransactionHistory_8888.csv")
        assert HSBCHKCreditProvider.can_handle(f)

    def test_can_handle_with_browser_dedupe_suffix(self, tmp_path: Path) -> None:
        f = write_csv(tmp_path, [], filename="TransactionHistory_8888 (1).csv")
        assert HSBCHKCreditProvider.can_handle(f)

    def test_rejects_debit_format(self, tmp_path: Path) -> None:
        f = tmp_path / "TransactionHistory.csv"
        f.write_text(
            "Date,Description,Billing amount,Billing currency,Balance,Balance currency\n",
            encoding="utf-8",
        )
        assert not HSBCHKCreditProvider.can_handle(f)

    def test_rejects_unrelated_csv(self, tmp_path: Path) -> None:
        f = tmp_path / "random.csv"
        f.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
        assert not HSBCHKCreditProvider.can_handle(f)

    def test_auto_detect_credit_vs_debit(self, tmp_path: Path) -> None:
        f = write_csv(tmp_path, [])
        provider = auto_detect_provider(f)
        assert provider is not None
        assert provider.provider_id == "hsbchk_credit"

    def test_parse_basic(self, tmp_path: Path, sample_rows: list[str]) -> None:
        f = write_csv(tmp_path, sample_rows)
        txns = HSBCHKCreditProvider().parse(f)
        assert len(txns) == 4

    def test_amount_signs(self, tmp_path: Path, sample_rows: list[str]) -> None:
        f = write_csv(tmp_path, sample_rows)
        txns = HSBCHKCreditProvider().parse(f)
        # DEBIT in source (-100) → +100 expense in sieve
        assert txns[0].amount == Decimal("100.00")
        assert txns[0].is_expense
        # CREDIT refund (+50) → -50 income in sieve
        assert txns[1].amount == Decimal("-50.00")
        assert txns[1].is_income
        # CREDIT payment (+1000) → -1000 income
        assert txns[2].amount == Decimal("-1000.00")
        # DEBIT (-25.50) → +25.50 expense
        assert txns[3].amount == Decimal("25.50")

    def test_dates_parsed_dd_mm_yyyy(
        self, tmp_path: Path, sample_rows: list[str]
    ) -> None:
        f = write_csv(tmp_path, sample_rows)
        txns = HSBCHKCreditProvider().parse(f)
        assert txns[0].date == date(2030, 4, 2)
        assert txns[0].post_date == date(2030, 4, 3)
        assert txns[3].date == date(2030, 4, 10)

    def test_currency(self, tmp_path: Path, sample_rows: list[str]) -> None:
        f = write_csv(tmp_path, sample_rows)
        txns = HSBCHKCreditProvider().parse(f)
        assert all(t.currency == "CNY" for t in txns)

    def test_payee_from_merchant_column(
        self, tmp_path: Path, sample_rows: list[str]
    ) -> None:
        f = write_csv(tmp_path, sample_rows)
        txns = HSBCHKCreditProvider().parse(f)
        assert txns[0].payee == "merchant-a"
        assert txns[1].payee == "merchant-b"
        # Empty merchant (PAYMENT row) → None
        assert txns[2].payee is None

    def test_description_whitespace_collapsed(
        self, tmp_path: Path, sample_rows: list[str]
    ) -> None:
        f = write_csv(tmp_path, sample_rows)
        txns = HSBCHKCreditProvider().parse(f)
        # No long runs of whitespace remain
        assert "  " not in txns[0].description
        assert "merchant-a" in txns[0].description

    def test_card_last4_extracted_from_filename(
        self, tmp_path: Path, sample_rows: list[str]
    ) -> None:
        f = write_csv(tmp_path, sample_rows, filename="TransactionHistory_8888.csv")
        txns = HSBCHKCreditProvider().parse(f)
        assert all(t.card_last4 == "8888" for t in txns)

    def test_card_last4_extracted_with_paren_suffix(
        self, tmp_path: Path, sample_rows: list[str]
    ) -> None:
        f = write_csv(tmp_path, sample_rows, filename="TransactionHistory_8888 (1).csv")
        txns = HSBCHKCreditProvider().parse(f)
        assert all(t.card_last4 == "8888" for t in txns)

    def test_card_last4_none_when_unrenamed(
        self, tmp_path: Path, sample_rows: list[str]
    ) -> None:
        f = write_csv(tmp_path, sample_rows)
        txns = HSBCHKCreditProvider().parse(f)
        assert all(t.card_last4 is None for t in txns)

    def test_statement_period_inferred(
        self, tmp_path: Path, sample_rows: list[str]
    ) -> None:
        f = write_csv(tmp_path, sample_rows)
        txns = HSBCHKCreditProvider().parse(f)
        period = (date(2030, 4, 2), date(2030, 4, 10))
        assert all(t.statement_period == period for t in txns)

    def test_metadata_captures_extras(
        self, tmp_path: Path, sample_rows: list[str]
    ) -> None:
        f = write_csv(tmp_path, sample_rows)
        txns = HSBCHKCreditProvider().parse(f)
        assert txns[0].metadata["transaction_status"] == "POSTED"
        assert txns[0].metadata["country"] == "CHINA"
        assert txns[0].metadata["direction"] == "DEBIT"
        # PAYMENT row has empty merchant/country/district — they should not appear
        assert "country" not in txns[2].metadata

    def test_thousand_separator(self, tmp_path: Path) -> None:
        body = [
            "01/05/2030,02/05/2030,QR merchant-d  CHN  CN,"
            '"-12,345.67"\t,CNY,POSTED,merchant-d,CHINA,CHN,DEBIT',
        ]
        f = write_csv(tmp_path, body)
        txns = HSBCHKCreditProvider().parse(f)
        assert txns[0].amount == Decimal("12345.67")

    def test_source_info(self, tmp_path: Path, sample_rows: list[str]) -> None:
        f = write_csv(tmp_path, sample_rows)
        txns = HSBCHKCreditProvider().parse(f)
        assert all(t.source_file == f for t in txns)
        assert all(t.source_line and t.source_line > 1 for t in txns)
        assert all(t.provider == "hsbchk_credit" for t in txns)


class TestHSBCHKCreditEdgeCases:
    def test_empty_body(self, tmp_path: Path) -> None:
        f = write_csv(tmp_path, [])
        assert HSBCHKCreditProvider().parse(f) == []

    def test_invalid_header_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.csv"
        f.write_text("foo,bar,baz\n1,2,3\n", encoding="utf-8")
        with pytest.raises(ValueError, match="Cannot identify HSBC HK credit"):
            HSBCHKCreditProvider().parse(f)

    def test_zero_amount_skipped(self, tmp_path: Path) -> None:
        body = [
            "02/04/2030,03/04/2030,merchant-x  CHN  CN,"
            '"0.00"\t,CNY,POSTED,merchant-x,CHINA,CHN,DEBIT',
        ]
        f = write_csv(tmp_path, body)
        assert HSBCHKCreditProvider().parse(f) == []
