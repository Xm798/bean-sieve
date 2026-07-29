"""Tests for the SPDB credit card transaction-detail provider."""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.providers import get_provider
from bean_sieve.providers.banks.credit.spdb import SPDBCreditProvider

HEADERS = [
    "交易日期",
    "记账日期",
    "交易摘要",
    "卡号末四位",
    "卡片类型",
    "交易币种",
    "交易金额",
    "原始交易金额&币种",
]


def create_spdb_xls(
    rows: list[list[object]],
    path: Path,
    *,
    header_row: int = 0,
) -> Path:
    """Create a synthetic SPDB-format XLS report."""
    try:
        import xlwt
    except ImportError:
        pytest.skip("xlwt not installed, skipping XLS creation tests")

    workbook = xlwt.Workbook()
    sheet = workbook.add_sheet("账单明细")
    for column, header in enumerate(HEADERS):
        sheet.write(header_row, column, header)
    for row_index, row in enumerate(rows, start=header_row + 1):
        for column, value in enumerate(row):
            sheet.write(row_index, column, value)
    workbook.save(str(path))
    return path


@pytest.fixture
def sample_rows() -> list[list[object]]:
    """Return wholly synthetic transaction rows."""
    return [
        [
            "20300102",
            "20300103",
            "测试商户甲",
            "8888",
            "测试标准卡",
            "人民币",
            "314.15",
            "314.15(CNY)",
        ],
        [
            "20300117",
            "20300118",
            "测试还款",
            "1234",
            "测试标准卡",
            "人民币",
            "-1618.03",
            "-1618.03(CNY)",
        ],
        [
            "20300131",
            "20300201",
            "测试商户乙",
            "8888",
            "测试虚拟卡",
            "人民币",
            "271.82",
            "-",
        ],
    ]


@pytest.fixture
def spdb_xls_file(
    tmp_path: Path,
    sample_rows: list[list[object]],
) -> Path:
    """Create a temporary synthetic SPDB report."""
    return create_spdb_xls(
        sample_rows,
        tmp_path / "测试用户203001交易明细报表00000000.xls",
    )


class TestSPDBCreditProvider:
    """Tests for core provider behavior."""

    def test_provider_registration(self) -> None:
        provider = get_provider("spdb_credit")

        assert isinstance(provider, SPDBCreditProvider)
        assert provider.provider_name == "浦发银行信用卡"
        assert provider.supported_formats == [".xls"]

    def test_can_handle_explicit_filename(self) -> None:
        assert SPDBCreditProvider.can_handle(Path("浦发信用卡交易明细.xls"))
        assert SPDBCreditProvider.can_handle(Path("SPDB_statement.xls"))
        assert not SPDBCreditProvider.can_handle(Path("浦发信用卡交易明细.csv"))

    def test_can_handle_unique_binary_header(self, spdb_xls_file: Path) -> None:
        assert SPDBCreditProvider.can_handle(spdb_xls_file)

    def test_rejects_other_xls_header(self, tmp_path: Path) -> None:
        try:
            import xlwt
        except ImportError:
            pytest.skip("xlwt not installed, skipping XLS creation tests")

        path = tmp_path / "交易明细报表.xls"
        workbook = xlwt.Workbook()
        sheet = workbook.add_sheet("Sheet1")
        sheet.write(0, 0, "交易日期")
        sheet.write(0, 1, "金额")
        workbook.save(str(path))

        assert not SPDBCreditProvider.can_handle(path)

    def test_rejects_keyword_filename_with_other_header(
        self,
        tmp_path: Path,
    ) -> None:
        try:
            import xlwt
        except ImportError:
            pytest.skip("xlwt not installed, skipping XLS creation tests")

        path = tmp_path / "浦发借记卡交易明细.xls"
        workbook = xlwt.Workbook()
        sheet = workbook.add_sheet("Sheet1")
        sheet.write(0, 0, "交易日期")
        sheet.write(0, 1, "金额")
        workbook.save(str(path))

        assert not SPDBCreditProvider.can_handle(path)

    def test_parse_transactions(self, spdb_xls_file: Path) -> None:
        transactions = SPDBCreditProvider().parse(spdb_xls_file)

        assert len(transactions) == 3

        expense = transactions[0]
        assert expense.date == date(2030, 1, 2)
        assert expense.post_date == date(2030, 1, 3)
        assert expense.amount == Decimal("314.15")
        assert expense.currency == "CNY"
        assert expense.description == "测试商户甲"
        assert expense.card_last4 == "8888"
        assert expense.provider == "spdb_credit"
        assert expense.source_line == 2
        assert expense.is_expense
        assert expense.metadata == {
            "card_type": "测试标准卡",
            "original_amount": Decimal("314.15"),
            "original_currency": "CNY",
        }

        payment = transactions[1]
        assert payment.amount == Decimal("-1618.03")
        assert payment.card_last4 == "1234"
        assert payment.is_income

        no_original_amount = transactions[2]
        assert no_original_amount.metadata == {"card_type": "测试虚拟卡"}

    def test_statement_period_is_inferred(
        self,
        spdb_xls_file: Path,
    ) -> None:
        transactions = SPDBCreditProvider().parse(spdb_xls_file)

        assert transactions
        assert all(
            transaction.statement_period
            == (
                date(2030, 1, 2),
                date(2030, 1, 31),
            )
            for transaction in transactions
        )

    def test_empty_statement(self, tmp_path: Path) -> None:
        path = create_spdb_xls([], tmp_path / "empty.xls")

        assert SPDBCreditProvider().parse(path) == []

    def test_header_can_appear_within_first_ten_rows(self, tmp_path: Path) -> None:
        rows = [
            [
                "20300305",
                "20300306",
                "测试延后表头",
                "8888",
                "测试标准卡",
                "人民币",
                "0.13",
                "0.13(CNY)",
            ]
        ]
        path = create_spdb_xls(rows, tmp_path / "header-row.xls", header_row=9)

        transactions = SPDBCreditProvider().parse(path)

        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("0.13")
        assert transactions[0].source_line == 11


class TestSPDBCellFormats:
    """Tests for XLS cell-type and value variations."""

    def test_numeric_cells_and_excel_dates(self, tmp_path: Path) -> None:
        try:
            import xlwt
        except ImportError:
            pytest.skip("xlwt not installed, skipping XLS creation tests")

        path = tmp_path / "numeric-cells.xls"
        workbook = xlwt.Workbook()
        sheet = workbook.add_sheet("账单明细")
        for column, header in enumerate(HEADERS):
            sheet.write(0, column, header)

        date_style = xlwt.easyxf(num_format_str="YYYY-MM-DD")
        sheet.write(1, 0, datetime(2030, 2, 3), date_style)
        sheet.write(1, 1, datetime(2030, 2, 4), date_style)
        sheet.write(1, 2, "测试数值单元格")
        sheet.write(1, 3, 321)
        sheet.write(1, 4, "测试标准卡")
        sheet.write(1, 5, "美元")
        sheet.write(1, 6, 73.19)
        sheet.write(1, 7, "73.19(USD)")
        workbook.save(str(path))

        transactions = SPDBCreditProvider().parse(path)

        assert len(transactions) == 1
        transaction = transactions[0]
        assert transaction.date == date(2030, 2, 3)
        assert transaction.post_date == date(2030, 2, 4)
        assert transaction.card_last4 == "0321"
        assert transaction.amount == Decimal("73.19")
        assert transaction.currency == "USD"

    def test_amount_with_thousands_separator(self, tmp_path: Path) -> None:
        rows = [
            [
                "20300407",
                "20300408",
                "测试大额消费",
                "8888",
                "测试标准卡",
                "人民币",
                "12,345.67",
                "12,345.67(CNY)",
            ]
        ]
        path = create_spdb_xls(rows, tmp_path / "commas.xls")

        transactions = SPDBCreditProvider().parse(path)

        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("12345.67")

    def test_foreign_currency_and_original_amount(self, tmp_path: Path) -> None:
        rows = [
            [
                "20300509",
                "20300510",
                "测试境外商户",
                "8888",
                "测试标准卡",
                "人民币",
                "73.19",
                "271.82(USD)",
            ]
        ]
        path = create_spdb_xls(rows, tmp_path / "foreign.xls")

        transactions = SPDBCreditProvider().parse(path)

        assert len(transactions) == 1
        transaction = transactions[0]
        assert transaction.currency == "CNY"
        assert transaction.amount == Decimal("73.19")
        assert transaction.metadata["original_amount"] == Decimal("271.82")
        assert transaction.metadata["original_currency"] == "USD"

    def test_unrecognized_original_value_is_preserved(
        self,
        tmp_path: Path,
    ) -> None:
        rows = [
            [
                "20300611",
                "20300612",
                "测试原币格式",
                "8888",
                "测试标准卡",
                "人民币",
                "271.82",
                "测试原币信息",
            ]
        ]
        path = create_spdb_xls(rows, tmp_path / "original-value.xls")

        transactions = SPDBCreditProvider().parse(path)

        assert transactions[0].metadata["original_transaction"] == "测试原币信息"


class TestSPDBMalformedReports:
    """Tests for malformed workbooks and rows."""

    def test_invalid_rows_are_skipped_without_sensitive_logs(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        rows = [
            ["", "", "", "", "", "", "", ""],
            [
                "not-a-date",
                "20300702",
                "测试无效日期",
                "8888",
                "测试标准卡",
                "人民币",
                "314.15",
                "314.15(CNY)",
            ],
            [
                "20300703",
                "20300704",
                "测试有效交易",
                "8888",
                "测试标准卡",
                "人民币",
                "271.82",
                "271.82(CNY)",
            ],
        ]
        path = create_spdb_xls(rows, tmp_path / "mixed-rows.xls")

        transactions = SPDBCreditProvider().parse(path)

        assert len(transactions) == 1
        assert transactions[0].description == "测试有效交易"
        assert path.name not in caplog.text
        assert "not-a-date" not in caplog.text

    def test_fewer_columns_returns_empty(self, tmp_path: Path) -> None:
        try:
            import xlwt
        except ImportError:
            pytest.skip("xlwt not installed, skipping XLS creation tests")

        path = tmp_path / "truncated.xls"
        workbook = xlwt.Workbook()
        sheet = workbook.add_sheet("账单明细")
        for column, header in enumerate(HEADERS[:3]):
            sheet.write(0, column, header)
        workbook.save(str(path))

        assert SPDBCreditProvider().parse(path) == []

    def test_unreadable_xls_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.xls"
        path.write_bytes(b"not an Excel workbook")

        assert SPDBCreditProvider().parse(path) == []
