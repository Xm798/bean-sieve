"""Tests for Bank of China (中国银行) debit card provider."""

from decimal import Decimal
from pathlib import Path

from bean_sieve.providers.banks.debit.boc import BOCDebitProvider


def _make_csv(rows: list[str]) -> str:
    header = "交易时间,业务摘要,对方账户名称,对方账户账号,币种,钞/汇,收入金额,支出金额,余额,交易渠道/场所,附言"
    return "\n".join([header, *rows])


class TestBOCDebitProvider:
    """Tests for BOCDebitProvider."""

    def test_parse_expense(self, tmp_path: Path) -> None:
        """Test parsing an expense transaction."""
        csv_file = tmp_path / "中国银行.csv"
        csv_file.write_text(
            _make_csv(
                [
                    '2026/06/15 10:30:00,网上快捷支付,支付宝-测试商户,X100******999N,人民币元,-,,"2,500.00","50,000.00",在线交易网关,支付宝-测试商户',
                ]
            ),
            encoding="utf-8",
        )

        provider = BOCDebitProvider()
        txns = provider.parse(csv_file)

        assert len(txns) == 1
        assert txns[0].date.isoformat() == "2026-06-15"
        assert txns[0].time is not None
        assert txns[0].time.hour == 10
        assert txns[0].time.minute == 30
        assert txns[0].amount == Decimal("2500.00")
        assert txns[0].currency == "CNY"
        assert txns[0].payee == "支付宝-测试商户"
        assert txns[0].description == "网上快捷支付 | 支付宝-测试商户"
        assert txns[0].provider == "boc_debit"

    def test_parse_income(self, tmp_path: Path) -> None:
        """Test parsing an income transaction (negative amount)."""
        csv_file = tmp_path / "中国银行.csv"
        csv_file.write_text(
            _make_csv(
                [
                    '2026/06/16 14:00:00,网上快捷提现,测试用户A,X888******000A,人民币元,-,"3,000.00",,"80,000.00",在线交易网关,--测试用户A支付宝余额提现-0001',
                ]
            ),
            encoding="utf-8",
        )

        provider = BOCDebitProvider()
        txns = provider.parse(csv_file)

        assert len(txns) == 1
        assert txns[0].amount == Decimal("-3000.00")
        assert txns[0].payee == "测试用户A"

    def test_parse_interest(self, tmp_path: Path) -> None:
        """Test parsing interest row (no counterparty)."""
        csv_file = tmp_path / "中国银行.csv"
        csv_file.write_text(
            _make_csv(
                [
                    '2026/06/20 23:59:59,结息,,,人民币元,-,1.23,,"10,000.00",,',
                ]
            ),
            encoding="utf-8",
        )

        provider = BOCDebitProvider()
        txns = provider.parse(csv_file)

        assert len(txns) == 1
        assert txns[0].amount == Decimal("-1.23")
        assert txns[0].payee is None
        assert txns[0].description == "结息"

    def test_parse_foreign_currency(self, tmp_path: Path) -> None:
        """Test parsing HKD transaction."""
        csv_file = tmp_path / "中国银行.csv"
        csv_file.write_text(
            _make_csv(
                [
                    "2026/06/20 23:30:00,结息,,,港币,现汇,0.88,,5.00,,",
                ]
            ),
            encoding="utf-8",
        )

        provider = BOCDebitProvider()
        txns = provider.parse(csv_file)

        assert len(txns) == 1
        assert txns[0].currency == "HKD"
        assert txns[0].amount == Decimal("-0.88")

    def test_parse_multiple_rows(self, tmp_path: Path) -> None:
        """Test parsing multiple transactions."""
        csv_file = tmp_path / "中国银行.csv"
        csv_file.write_text(
            _make_csv(
                [
                    '2026/06/15 10:30:00,网上快捷支付,支付宝-测试商户A,X100******999N,人民币元,-,,"1,000.00","50,000.00",在线交易网关,支付宝-测试商户A',
                    '2026/06/14 09:00:00,互联互通,TEST USER,X200******888N,人民币元,-,,"5,000.00","60,000.00",手机银行,',
                    '2026/06/13 18:00:00,转入,测试用户B,X300******777N,人民币元,-,"8,000.00",,"70,000.00",手机银行,',
                ]
            ),
            encoding="utf-8",
        )

        provider = BOCDebitProvider()
        txns = provider.parse(csv_file)

        assert len(txns) == 3
        assert txns[0].amount == Decimal("1000.00")
        assert txns[1].amount == Decimal("5000.00")
        assert txns[2].amount == Decimal("-8000.00")

    def test_empty_file(self, tmp_path: Path) -> None:
        """Test parsing empty file (header only)."""
        csv_file = tmp_path / "中国银行.csv"
        csv_file.write_text(_make_csv([]), encoding="utf-8")

        provider = BOCDebitProvider()
        txns = provider.parse(csv_file)

        assert txns == []

    def test_skip_invalid_rows(self, tmp_path: Path) -> None:
        """Test that rows with missing data are skipped."""
        csv_file = tmp_path / "中国银行.csv"
        csv_file.write_text(
            _make_csv(
                [
                    "not-a-date,something,,,,,,,,,,",
                    '2026/06/15 10:30:00,网上快捷支付,测试商户,X100******999N,人民币元,-,,100.00,"50,000.00",在线交易网关,测试商户',
                ]
            ),
            encoding="utf-8",
        )

        provider = BOCDebitProvider()
        txns = provider.parse(csv_file)

        assert len(txns) == 1

    def test_remarks_dash_excluded(self, tmp_path: Path) -> None:
        """Test that '--' remarks are excluded from description."""
        csv_file = tmp_path / "中国银行.csv"
        csv_file.write_text(
            _make_csv(
                [
                    '2026/06/15 15:00:00,网上快捷支付,支付宝-测试电力公司,X100******999N,人民币元,-,,88.88,"50,000.00",在线交易网关,--',
                ]
            ),
            encoding="utf-8",
        )

        provider = BOCDebitProvider()
        txns = provider.parse(csv_file)

        assert len(txns) == 1
        assert txns[0].description == "网上快捷支付"

    def test_remarks_dash_prefix_stripped(self, tmp_path: Path) -> None:
        """Test that '--' prefix is stripped from non-empty remarks."""
        csv_file = tmp_path / "中国银行.csv"
        csv_file.write_text(
            _make_csv(
                [
                    '2026/06/16 14:00:00,网上快捷提现,测试用户A,X888******000A,人民币元,-,"3,000.00",,"80,000.00",在线交易网关,--测试用户A余额提现-0001',
                ]
            ),
            encoding="utf-8",
        )

        provider = BOCDebitProvider()
        txns = provider.parse(csv_file)

        assert len(txns) == 1
        assert txns[0].description == "网上快捷提现 | 测试用户A余额提现-0001"

    def test_parse_datetime_nbsp(self, tmp_path: Path) -> None:
        """Test non-breaking space between date and time (real data format)."""
        csv_file = tmp_path / "中国银行.csv"
        csv_file.write_text(
            _make_csv(
                [
                    '2026/06/15\xa010:30:00,网上快捷支付,测试商户,X100******999N,人民币元,-,,100.00,"50,000.00",在线交易网关,测试',
                ]
            ),
            encoding="utf-8",
        )

        provider = BOCDebitProvider()
        txns = provider.parse(csv_file)

        assert len(txns) == 1
        assert txns[0].time is not None
        assert txns[0].time.hour == 10

    def test_unknown_currency_passthrough(self, tmp_path: Path) -> None:
        """Test that mapped currency returns ISO code."""
        csv_file = tmp_path / "中国银行.csv"
        csv_file.write_text(
            _make_csv(
                [
                    "2026/06/20 23:30:00,结息,,,澳元,现汇,0.50,,10.00,,",
                ]
            ),
            encoding="utf-8",
        )

        provider = BOCDebitProvider()
        txns = provider.parse(csv_file)

        assert len(txns) == 1
        assert txns[0].currency == "AUD"

    def test_both_income_and_expense(self, tmp_path: Path) -> None:
        """Test that expense takes priority when both columns have values."""
        csv_file = tmp_path / "中国银行.csv"
        csv_file.write_text(
            _make_csv(
                [
                    '2026/06/15 10:30:00,测试摘要,测试商户,X100******999N,人民币元,-,200.00,300.00,"50,000.00",在线交易网关,测试',
                ]
            ),
            encoding="utf-8",
        )

        provider = BOCDebitProvider()
        txns = provider.parse(csv_file)

        assert len(txns) == 1
        assert txns[0].amount == Decimal("300.00")

    def test_header_not_at_row_zero(self, tmp_path: Path) -> None:
        """Test parsing when header is preceded by metadata lines."""
        csv_file = tmp_path / "中国银行.csv"
        content = "\n".join(
            [
                "中国银行个人网上银行",
                "查询日期: 2026-06-15",
                "交易时间,业务摘要,对方账户名称,对方账户账号,币种,钞/汇,收入金额,支出金额,余额,交易渠道/场所,附言",
                '2026/06/15 10:30:00,网上快捷支付,测试商户,X100******999N,人民币元,-,,100.00,"50,000.00",在线交易网关,测试',
            ]
        )
        csv_file.write_text(content, encoding="utf-8")

        provider = BOCDebitProvider()
        txns = provider.parse(csv_file)

        assert len(txns) == 1
        assert txns[0].source_line == 4

    def test_gbk_encoding(self, tmp_path: Path) -> None:
        """Test GBK encoding fallback."""
        csv_file = tmp_path / "中国银行.csv"
        csv_file.write_bytes(
            _make_csv(
                [
                    '2026/06/15 10:30:00,网上快捷支付,测试商户,X100******999N,人民币元,-,,500.00,"50,000.00",在线交易网关,测试商户',
                ]
            ).encode("gbk"),
        )

        provider = BOCDebitProvider()
        txns = provider.parse(csv_file)

        assert len(txns) == 1
        assert txns[0].amount == Decimal("500.00")

    def test_metadata_channel(self, tmp_path: Path) -> None:
        """Test that channel is captured in metadata."""
        csv_file = tmp_path / "中国银行.csv"
        csv_file.write_text(
            _make_csv(
                [
                    '2026/06/14 09:00:00,互联互通,TEST USER,X200******888N,人民币元,-,,"5,000.00","60,000.00",手机银行,',
                ]
            ),
            encoding="utf-8",
        )

        provider = BOCDebitProvider()
        txns = provider.parse(csv_file)

        assert txns[0].metadata["channel"] == "手机银行"
        assert txns[0].metadata["summary"] == "互联互通"

    def test_can_handle_csv(self, tmp_path: Path) -> None:
        """Test file detection."""
        csv_file = tmp_path / "中国银行.csv"
        csv_file.write_text(
            _make_csv(
                [
                    '2026/06/15 10:30:00,网上快捷支付,测试商户,X100******999N,人民币元,-,,100.00,"50,000.00",在线交易网关,测试',
                ]
            ),
            encoding="utf-8",
        )

        assert BOCDebitProvider.can_handle(csv_file)

    def test_no_handle_pdf(self, tmp_path: Path) -> None:
        """Test that PDF files are not handled (BOC credit uses PDF)."""
        pdf_file = tmp_path / "中国银行.pdf"
        pdf_file.write_bytes(b"%PDF-1.4")

        assert not BOCDebitProvider.can_handle(pdf_file)
