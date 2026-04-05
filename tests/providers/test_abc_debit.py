"""Tests for Agricultural Bank of China (农业银行) debit card provider."""

from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook

from bean_sieve.providers.banks.debit.abc import ABCDebitProvider


def _create_xlsx(path: Path, rows: list[list]) -> Path:
    """Create an XLSX file with given rows."""
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    wb.save(path)
    return path


TITLE_ROW = ["账户明细查询"]
INFO_ROW = ["账户：622848****8888 户名：测试用户 起始日期：20260105 截止日期：20260405"]
HEADER_ROW = [
    "交易日期",
    "交易时间",
    "交易金额",
    "本次余额",
    "对方户名",
    "对方账号",
    "交易行",
    "交易渠道",
    "交易类型",
    "交易用途",
    "交易摘要",
]


class TestABCDebitProvider:
    """Tests for ABCDebitProvider."""

    def test_parse_basic(self, tmp_path: Path) -> None:
        """Test basic expense and income parsing."""
        rows = [
            TITLE_ROW,
            INFO_ROW,
            HEADER_ROW,
            [
                "2026-03-29",
                "12:17:04",
                "-4.00",
                "426.44",
                "支付宝-消费测试商户A",
                "208884****0912",
                "9999-01",
                "电子商务",
                "转账",
                "UA0329测试支付宝-消费-测试商户A",
                "支付宝",
            ],
            [
                "2026-02-10",
                "16:31:46",
                "+1000.00",
                "1393.18",
                "测试用户",
                "621486****5555",
                "9999-01",
                "",
                "转账",
                "",
                "银联入账",
            ],
        ]
        f = _create_xlsx(tmp_path / "detail20260405.xlsx", rows)
        provider = ABCDebitProvider()
        txns = provider.parse(f)

        assert len(txns) == 2

        # Expense: source -4.00 → bean-sieve +4.00
        assert txns[0].amount == Decimal("4.00")
        assert txns[0].date.isoformat() == "2026-03-29"
        assert txns[0].time is not None
        assert txns[0].time.isoformat() == "12:17:04"
        assert txns[0].payee == "支付宝-消费测试商户A"
        assert txns[0].card_last4 == "8888"
        assert txns[0].provider == "abc_debit"

        # Income: source +1000.00 → bean-sieve -1000.00
        assert txns[1].amount == Decimal("-1000.00")
        assert txns[1].payee == "测试用户"

    def test_skip_zero_amount(self, tmp_path: Path) -> None:
        """Test that zero-amount rows (e.g. interest tax) are skipped."""
        rows = [
            TITLE_ROW,
            INFO_ROW,
            HEADER_ROW,
            [
                "2026-03-21",
                "",
                "+0.00",
                "485.44",
                "",
                "",
                "中国农业银行测试分理处",
                "",
                "转账",
                "个人活期结息",
                "利息税",
            ],
        ]
        f = _create_xlsx(tmp_path / "detail20260405.xlsx", rows)
        txns = ABCDebitProvider().parse(f)
        assert len(txns) == 0

    def test_empty_statement(self, tmp_path: Path) -> None:
        """Test parsing a statement with no data rows."""
        rows = [TITLE_ROW, INFO_ROW, HEADER_ROW]
        f = _create_xlsx(tmp_path / "detail20260405.xlsx", rows)
        txns = ABCDebitProvider().parse(f)
        assert txns == []

    def test_invalid_title_returns_empty(self, tmp_path: Path) -> None:
        """Test that a file without proper title is rejected."""
        rows = [["其他报表"], INFO_ROW, HEADER_ROW]
        f = _create_xlsx(tmp_path / "detail20260405.xlsx", rows)
        txns = ABCDebitProvider().parse(f)
        assert txns == []

    def test_missing_time(self, tmp_path: Path) -> None:
        """Test rows with empty time field (e.g. interest entries)."""
        rows = [
            TITLE_ROW,
            INFO_ROW,
            HEADER_ROW,
            [
                "2026-03-21",
                "",
                "+0.07",
                "485.44",
                "",
                "",
                "中国农业银行测试分理处",
                "",
                "转账",
                "个人活期结息",
                "结息",
            ],
        ]
        f = _create_xlsx(tmp_path / "detail20260405.xlsx", rows)
        txns = ABCDebitProvider().parse(f)
        assert len(txns) == 1
        assert txns[0].time is None
        assert txns[0].amount == Decimal("-0.07")  # income

    def test_card_last4_extraction(self, tmp_path: Path) -> None:
        """Test extracting card last 4 digits from various formats."""
        rows = [
            TITLE_ROW,
            ["账户：6228****1234 户名：测试 起始日期：20260101 截止日期：20260401"],
            HEADER_ROW,
            [
                "2026-01-15",
                "10:00:00",
                "-50.00",
                "100.00",
                "测试商户B",
                "123456789",
                "9999-01",
                "电子商务",
                "转账",
                "测试交易",
                "微信支付",
            ],
        ]
        f = _create_xlsx(tmp_path / "detail20260401.xlsx", rows)
        txns = ABCDebitProvider().parse(f)
        assert len(txns) == 1
        assert txns[0].card_last4 == "1234"

    def test_no_card_info(self, tmp_path: Path) -> None:
        """Test graceful handling when card info is missing."""
        rows = [
            TITLE_ROW,
            ["账户信息不完整"],
            HEADER_ROW,
            [
                "2026-01-15",
                "10:00:00",
                "-25.00",
                "100.00",
                "测试商户C",
                "",
                "9999-01",
                "电子商务",
                "转账",
                "测试交易",
                "支付宝",
            ],
        ]
        f = _create_xlsx(tmp_path / "detail20260401.xlsx", rows)
        txns = ABCDebitProvider().parse(f)
        assert len(txns) == 1
        assert txns[0].card_last4 is None

    def test_description_building(self, tmp_path: Path) -> None:
        """Test description is built from purpose and summary."""
        rows = [
            TITLE_ROW,
            INFO_ROW,
            HEADER_ROW,
            # Both purpose and summary present
            [
                "2026-03-20",
                "13:18:00",
                "-21.00",
                "497.38",
                "测试咖啡店",
                "543098213",
                "9999-01",
                "电子商务",
                "转账",
                "NA2026032033195470测试咖啡店",
                "微信支付",
            ],
            # Empty purpose, only summary
            [
                "2026-03-21",
                "",
                "+0.07",
                "485.44",
                "",
                "",
                "中国农业银行测试分理处",
                "",
                "转账",
                "",
                "结息",
            ],
        ]
        f = _create_xlsx(tmp_path / "detail20260405.xlsx", rows)
        txns = ABCDebitProvider().parse(f)
        assert txns[0].description == "NA2026032033195470测试咖啡店 | 微信支付"
        assert txns[1].description == "结息"

    def test_amount_with_comma(self, tmp_path: Path) -> None:
        """Test amounts with thousand separators."""
        rows = [
            TITLE_ROW,
            INFO_ROW,
            HEADER_ROW,
            [
                "2026-01-15",
                "10:00:00",
                "-1,234.56",
                "100.00",
                "测试大额消费",
                "",
                "9999-01",
                "电子商务",
                "转账",
                "测试大额交易",
                "支付宝",
            ],
        ]
        f = _create_xlsx(tmp_path / "detail20260405.xlsx", rows)
        txns = ABCDebitProvider().parse(f)
        assert len(txns) == 1
        assert txns[0].amount == Decimal("1234.56")

    def test_skip_invalid_date_rows(self, tmp_path: Path) -> None:
        """Test that rows with invalid dates are skipped."""
        rows = [
            TITLE_ROW,
            INFO_ROW,
            HEADER_ROW,
            ["", "10:00:00", "-10.00", "100.00", "测试", "", "", "", "", "", ""],
            [
                "not-a-date",
                "10:00:00",
                "-10.00",
                "100.00",
                "测试",
                "",
                "",
                "",
                "",
                "",
                "",
            ],
        ]
        f = _create_xlsx(tmp_path / "detail20260405.xlsx", rows)
        txns = ABCDebitProvider().parse(f)
        assert len(txns) == 0

    def test_mixed_valid_and_invalid_rows(self, tmp_path: Path) -> None:
        """Test that valid rows parse correctly when mixed with invalid rows."""
        rows = [
            TITLE_ROW,
            INFO_ROW,
            HEADER_ROW,
            ["", "10:00:00", "-10.00", "100.00", "测试", "", "", "", "", "", ""],
            [
                "2026-01-20",
                "09:30:00",
                "-15.50",
                "84.50",
                "测试商户E",
                "",
                "9999-01",
                "电子商务",
                "转账",
                "测试有效交易",
                "微信支付",
            ],
            ["not-a-date", "10:00:00", "-5.00", "79.50", "测试", "", "", "", "", "", ""],
        ]
        f = _create_xlsx(tmp_path / "detail20260405.xlsx", rows)
        txns = ABCDebitProvider().parse(f)
        assert len(txns) == 1
        assert txns[0].amount == Decimal("15.50")
        assert txns[0].date.isoformat() == "2026-01-20"

    @pytest.mark.parametrize("ext", [".xlsx", ".xls"])
    def test_supported_formats(self, ext: str) -> None:
        """Test that supported formats are declared."""
        assert ext in ABCDebitProvider.supported_formats

    def test_filename_pattern(self) -> None:
        """Test filename pattern matching."""
        pattern = ABCDebitProvider.filename_pattern
        assert pattern is not None
        assert pattern.search("detail20260405")
        assert pattern.search("detail20260105")
        assert not pattern.search("transaction_list")
