"""Tests for HSBC Hong Kong debit / savings account provider."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.providers import auto_detect_provider, get_provider
from bean_sieve.providers.banks.debit.hsbchk import HSBCHKDebitProvider

HEADER = "Date,Description,Billing amount,Billing currency,Balance,Balance currency"


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
    # Synthetic data only — fake reference codes and round amounts.
    return [
        '02/04/2030,HC11111111111111   02APR,"1,000.00"\t,HKD,"11,000.00"\t,HKD',
        '05/04/2030,NET- 2222222222222222,"-500.00"\t,HKD,"10,500.00"\t,HKD',
        '10/04/2030,Interest credit,"5.00"\t,HKD,"10,505.00"\t,HKD',
        '15/04/2030,ATM withdrawal placeholder,"-200.00"\t,HKD,"10,305.00"\t,HKD',
    ]


class TestHSBCHKDebitProvider:
    def test_provider_registration(self) -> None:
        provider = get_provider("hsbchk_debit")
        assert isinstance(provider, HSBCHKDebitProvider)
        assert provider.provider_id == "hsbchk_debit"
        assert ".csv" in provider.supported_formats

    def test_can_handle_by_content(self, tmp_path: Path) -> None:
        f = write_csv(tmp_path, [])
        assert HSBCHKDebitProvider.can_handle(f)

    def test_rejects_credit_format(self, tmp_path: Path) -> None:
        f = tmp_path / "TransactionHistory.csv"
        f.write_text(
            "Transaction date,Post date,Description,Billing amount,"
            "Billing currency,Transaction status,Merchant name,"
            "Country / region,Area / district,Credit / Debit\n",
            encoding="utf-8",
        )
        assert not HSBCHKDebitProvider.can_handle(f)

    def test_rejects_unrelated_csv(self, tmp_path: Path) -> None:
        f = tmp_path / "random.csv"
        f.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
        assert not HSBCHKDebitProvider.can_handle(f)

    def test_auto_detect_picks_debit(self, tmp_path: Path) -> None:
        f = write_csv(tmp_path, [])
        provider = auto_detect_provider(f)
        assert provider is not None
        assert provider.provider_id == "hsbchk_debit"

    def test_parse_basic(self, tmp_path: Path, sample_rows: list[str]) -> None:
        f = write_csv(tmp_path, sample_rows)
        txns = HSBCHKDebitProvider().parse(f)
        assert len(txns) == 4

    def test_amount_signs(self, tmp_path: Path, sample_rows: list[str]) -> None:
        f = write_csv(tmp_path, sample_rows)
        txns = HSBCHKDebitProvider().parse(f)
        # Source +1000 (inflow) → -1000 (income in sieve)
        assert txns[0].amount == Decimal("-1000.00")
        assert txns[0].is_income
        # Source -500 (outflow) → +500 (expense in sieve)
        assert txns[1].amount == Decimal("500.00")
        assert txns[1].is_expense
        # Interest inflow
        assert txns[2].amount == Decimal("-5.00")
        # ATM withdrawal outflow
        assert txns[3].amount == Decimal("200.00")

    def test_dates_parsed_dd_mm_yyyy(
        self, tmp_path: Path, sample_rows: list[str]
    ) -> None:
        f = write_csv(tmp_path, sample_rows)
        txns = HSBCHKDebitProvider().parse(f)
        assert txns[0].date == date(2030, 4, 2)
        assert txns[3].date == date(2030, 4, 15)

    def test_currency_default(self, tmp_path: Path, sample_rows: list[str]) -> None:
        f = write_csv(tmp_path, sample_rows)
        txns = HSBCHKDebitProvider().parse(f)
        assert all(t.currency == "HKD" for t in txns)

    def test_order_id_extracted_from_reference(
        self, tmp_path: Path, sample_rows: list[str]
    ) -> None:
        f = write_csv(tmp_path, sample_rows)
        txns = HSBCHKDebitProvider().parse(f)
        assert txns[0].order_id == "HC11111111111111"
        assert txns[1].order_id == "NET- 2222222222222222"
        # Plain text rows have no leading reference
        assert txns[2].order_id is None

    def test_balance_in_metadata(self, tmp_path: Path, sample_rows: list[str]) -> None:
        f = write_csv(tmp_path, sample_rows)
        txns = HSBCHKDebitProvider().parse(f)
        assert txns[0].metadata["balance"] == "11,000.00"

    def test_balance_currency_omitted_when_same(
        self, tmp_path: Path, sample_rows: list[str]
    ) -> None:
        f = write_csv(tmp_path, sample_rows)
        txns = HSBCHKDebitProvider().parse(f)
        # Both currencies are HKD → balance_currency should not appear
        assert "balance_currency" not in txns[0].metadata

    def test_balance_currency_kept_when_different(self, tmp_path: Path) -> None:
        body = [
            '02/04/2030,Foreign settle placeholder,"100.00"\t,USD,"7,800.00"\t,HKD',
        ]
        f = write_csv(tmp_path, body)
        txns = HSBCHKDebitProvider().parse(f)
        assert txns[0].metadata["balance_currency"] == "HKD"

    def test_thousand_separator(self, tmp_path: Path) -> None:
        body = [
            '02/04/2030,HC33333333333333   02APR,"-12,345.67"\t,HKD,"100,000.00"\t,HKD',
        ]
        f = write_csv(tmp_path, body)
        txns = HSBCHKDebitProvider().parse(f)
        assert txns[0].amount == Decimal("12345.67")

    def test_source_info(self, tmp_path: Path, sample_rows: list[str]) -> None:
        f = write_csv(tmp_path, sample_rows)
        txns = HSBCHKDebitProvider().parse(f)
        assert all(t.source_file == f for t in txns)
        assert all(t.source_line and t.source_line > 1 for t in txns)
        assert all(t.provider == "hsbchk_debit" for t in txns)


class TestHSBCHKDebitEdgeCases:
    def test_empty_body(self, tmp_path: Path) -> None:
        f = write_csv(tmp_path, [])
        assert HSBCHKDebitProvider().parse(f) == []

    def test_invalid_header_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.csv"
        f.write_text("foo,bar,baz\n1,2,3\n", encoding="utf-8")
        with pytest.raises(ValueError, match="Cannot identify HSBC HK debit"):
            HSBCHKDebitProvider().parse(f)

    def test_zero_amount_skipped(self, tmp_path: Path) -> None:
        body = ['02/04/2030,placeholder,"0.00"\t,HKD,"100.00"\t,HKD']
        f = write_csv(tmp_path, body)
        assert HSBCHKDebitProvider().parse(f) == []
