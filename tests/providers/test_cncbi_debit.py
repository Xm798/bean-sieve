"""Tests for CITIC Bank International (中信银行国际) debit account provider."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.providers import get_provider
from bean_sieve.providers.banks.debit.cncbi import CNCBIDebitProvider

HEADER_SIMPLIFIED = "过账日,交易日,账项资料,支出,收入,结余,"
HEADER_TRADITIONAL = "過賬日,交易日,賬項資料,支出,收入,結餘,"
HEADER_ENGLISH = "Post Date,Trans.Date,Description,Debit,Credit,Balance,"


def write_csv(
    tmp_path: Path, header: str, body_lines: list[str], filename: str = "CNCBI.csv"
) -> Path:
    file_path = tmp_path / filename
    content = header + "\n" + "\n".join(body_lines) + "\n"
    file_path.write_text(content, encoding="utf-8")
    return file_path


@pytest.fixture
def sample_rows() -> list[str]:
    return [
        '01/04/2030,01/04/2030,承上结余,,,"10,000.00",',
        '02/04/2030,02/04/2030,转数快 本地/海外汇入 - FICT3004021234567890 payee-a,,"500.00","10,500.00",',
        '15/04/2030,15/04/2030,转数快 本地/海外汇出 - FOCT3004151234567890 payee-b,"200.00",,"10,300.00",',
        '20/04/2030,20/04/2030,本行内部转账支出,"100.00",,"10,200.00",',
        '30/04/2030,30/04/2030,利息存入,,1.50,"10,201.50",',
        '30/04/2030,30/04/2030,转承结余,,,"10,201.50",',
    ]


class TestCNCBIDebitProvider:
    def test_provider_registration(self) -> None:
        provider = get_provider("cncbi_debit")
        assert isinstance(provider, CNCBIDebitProvider)
        assert provider.provider_id == "cncbi_debit"
        assert ".csv" in provider.supported_formats

    def test_can_handle_by_filename(self, tmp_path: Path) -> None:
        f = tmp_path / "CNCBI_2030_04.csv"
        f.write_text(HEADER_ENGLISH + "\n", encoding="utf-8")
        assert CNCBIDebitProvider.can_handle(f)

    def test_can_handle_by_content_simplified(self, tmp_path: Path) -> None:
        f = tmp_path / "statement.csv"
        f.write_text(HEADER_SIMPLIFIED + "\n", encoding="utf-8")
        assert CNCBIDebitProvider.can_handle(f)

    def test_can_handle_by_content_traditional(self, tmp_path: Path) -> None:
        f = tmp_path / "statement.csv"
        f.write_text(HEADER_TRADITIONAL + "\n", encoding="utf-8")
        assert CNCBIDebitProvider.can_handle(f)

    def test_can_handle_rejects_unrelated(self, tmp_path: Path) -> None:
        f = tmp_path / "random.csv"
        f.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
        assert not CNCBIDebitProvider.can_handle(f)

    def test_parse_simplified_header(
        self, tmp_path: Path, sample_rows: list[str]
    ) -> None:
        f = write_csv(tmp_path, HEADER_SIMPLIFIED, sample_rows)
        txns = CNCBIDebitProvider().parse(f)
        # 6 rows minus 2 balance carry-forward rows
        assert len(txns) == 4

    def test_parse_traditional_header(
        self, tmp_path: Path, sample_rows: list[str]
    ) -> None:
        f = write_csv(tmp_path, HEADER_TRADITIONAL, sample_rows)
        txns = CNCBIDebitProvider().parse(f)
        assert len(txns) == 4

    def test_parse_english_header(self, tmp_path: Path) -> None:
        body = [
            '01/04/2030,01/04/2030,Balance Brought Forward,,,"10,000.00",',
            '02/04/2030,02/04/2030,FPS Inward - FICT3004021234567890 payee-a,,"500.00","10,500.00",',
            '15/04/2030,15/04/2030,FPS Outward - FOCT3004151234567890 payee-b,"200.00",,"10,300.00",',
            '30/04/2030,30/04/2030,Balance Carried Forward,,,"10,300.00",',
        ]
        f = write_csv(tmp_path, HEADER_ENGLISH, body)
        txns = CNCBIDebitProvider().parse(f)
        assert len(txns) == 2

    def test_amount_signs(self, tmp_path: Path, sample_rows: list[str]) -> None:
        f = write_csv(tmp_path, HEADER_SIMPLIFIED, sample_rows)
        txns = CNCBIDebitProvider().parse(f)
        # Income (汇入)
        assert txns[0].amount == Decimal("-500.00")
        assert txns[0].is_income
        # Expense (汇出)
        assert txns[1].amount == Decimal("200.00")
        assert txns[1].is_expense
        # Internal transfer expense
        assert txns[2].amount == Decimal("100.00")
        # Interest income
        assert txns[3].amount == Decimal("-1.50")

    def test_date_parsing(self, tmp_path: Path, sample_rows: list[str]) -> None:
        f = write_csv(tmp_path, HEADER_SIMPLIFIED, sample_rows)
        txns = CNCBIDebitProvider().parse(f)
        assert txns[0].date == date(2030, 4, 2)
        assert txns[0].post_date == date(2030, 4, 2)

    def test_currency_default_hkd(self, tmp_path: Path, sample_rows: list[str]) -> None:
        f = write_csv(tmp_path, HEADER_SIMPLIFIED, sample_rows)
        txns = CNCBIDebitProvider().parse(f)
        assert all(t.currency == "HKD" for t in txns)

    def test_order_id_from_fps_reference(
        self, tmp_path: Path, sample_rows: list[str]
    ) -> None:
        f = write_csv(tmp_path, HEADER_SIMPLIFIED, sample_rows)
        txns = CNCBIDebitProvider().parse(f)
        assert txns[0].order_id == "FICT3004021234567890"
        assert txns[1].order_id == "FOCT3004151234567890"
        # Internal transfer / interest have no FPS reference
        assert txns[2].order_id is None
        assert txns[3].order_id is None

    def test_payee_extracted_after_reference(
        self, tmp_path: Path, sample_rows: list[str]
    ) -> None:
        f = write_csv(tmp_path, HEADER_SIMPLIFIED, sample_rows)
        txns = CNCBIDebitProvider().parse(f)
        assert txns[0].payee == "payee-a"
        assert txns[1].payee == "payee-b"

    def test_balance_in_metadata(self, tmp_path: Path, sample_rows: list[str]) -> None:
        f = write_csv(tmp_path, HEADER_SIMPLIFIED, sample_rows)
        txns = CNCBIDebitProvider().parse(f)
        assert txns[0].metadata["balance"] == "10,500.00"

    def test_source_info(self, tmp_path: Path, sample_rows: list[str]) -> None:
        f = write_csv(tmp_path, HEADER_SIMPLIFIED, sample_rows)
        txns = CNCBIDebitProvider().parse(f)
        assert all(t.source_file == f for t in txns)
        assert all(t.source_line and t.source_line > 1 for t in txns)
        assert all(t.provider == "cncbi_debit" for t in txns)


class TestCNCBIDebitEdgeCases:
    def test_empty_body(self, tmp_path: Path) -> None:
        f = write_csv(tmp_path, HEADER_SIMPLIFIED, [])
        assert CNCBIDebitProvider().parse(f) == []

    def test_header_only_with_balance_rows(self, tmp_path: Path) -> None:
        body = [
            '01/04/2030,01/04/2030,承上结余,,,"100.00",',
            '30/04/2030,30/04/2030,转承结余,,,"100.00",',
        ]
        f = write_csv(tmp_path, HEADER_SIMPLIFIED, body)
        assert CNCBIDebitProvider().parse(f) == []

    def test_invalid_header_raises(self, tmp_path: Path) -> None:
        f = write_csv(tmp_path, "foo,bar,baz", ["1,2,3"])
        with pytest.raises(ValueError, match="Cannot identify CNCBI header"):
            CNCBIDebitProvider().parse(f)

    def test_thousand_separator_in_amount(self, tmp_path: Path) -> None:
        body = [
            '01/04/2030,01/04/2030,转数快 - FICT3004011111111111 payee-x,,"30,000.00","30,000.00",',
        ]
        f = write_csv(tmp_path, HEADER_SIMPLIFIED, body)
        txns = CNCBIDebitProvider().parse(f)
        assert len(txns) == 1
        assert txns[0].amount == Decimal("-30000.00")
