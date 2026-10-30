"""Tests for the SPDB credit card transaction-detail provider."""

import logging
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import fitz
import pytest

from bean_sieve.config import Config
from bean_sieve.core.metadata_keys import (
    ORIGINAL_AMOUNT,
    ORIGINAL_CURRENCY,
    STATEMENT_CYCLE,
)
from bean_sieve.providers import auto_detect_provider, get_provider
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
        assert provider.supported_formats == [".xls", ".pdf"]

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

    def test_statement_period_is_inferred_per_card(
        self,
        spdb_xls_file: Path,
    ) -> None:
        transactions = SPDBCreditProvider().parse(spdb_xls_file)

        assert [
            (transaction.card_last4, transaction.statement_period)
            for transaction in transactions
        ] == [
            ("8888", (date(2030, 1, 2), date(2030, 1, 31))),
            ("1234", (date(2030, 1, 17), date(2030, 1, 17))),
            ("8888", (date(2030, 1, 2), date(2030, 1, 31))),
        ]

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


def test_covered_ranges_keep_each_statement_period(spdb_xls_file: Path) -> None:
    """Per-account statements list every period instead of one merged span.

    SPDB bills per account, so both cards' periods land on the one configured
    account; what this pins is that they stay two ranges rather than one span
    stretched over the days between them.
    """
    provider = SPDBCreditProvider()
    config = Config.from_dict(
        {"providers": {"spdb_credit": {"accounts": {"8888": "Liabilities:CC:SPDB"}}}}
    )

    assert provider.get_covered_ranges(provider.parse(spdb_xls_file), config) == {
        "Liabilities:CC:SPDB": [
            (date(2030, 1, 2), date(2030, 1, 31)),
            (date(2030, 1, 17), date(2030, 1, 17)),
        ]
    }


EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def create_spdb_pdf(
    path: Path, month: str, pages: list[list[str]], billing_day: str = "20"
) -> Path:
    """Write a synthetic SPDB PDF statement, one text line per cell."""
    doc = fitz.open()
    for number, cells in enumerate(pages):
        page = doc.new_page(width=595, height=842)
        lines = cells
        if number == 0:
            lines = [
                f"{month}信用卡账单",
                "现呈上您浦发个人信用卡对账单。",
                "账单日：",
                "Statement Due Date",
                billing_day,
                "本期账务明细",
                "交易日期",
                "记账日期",
                *cells,
            ]
        for i, line in enumerate([*lines, f"第 {number + 1} 页"]):
            page.insert_text((40, 30 + i * 12), line, fontname="china-s", fontsize=8)
    doc.save(path)
    doc.close()
    return path


def one_row_pdf(tmp_path: Path, month: str, billing_day: str) -> Path:
    """A statement for ``month`` (YYYYMM) with one row on its 5th."""
    row = [f"{month}05", f"{month}05", "消费 payee-a", "8888", "￥57.90"]
    return create_spdb_pdf(
        tmp_path / f"{month}-{billing_day}.pdf", month, [row], billing_day
    )


@pytest.fixture
def spdb_pdf_file(tmp_path: Path) -> Path:
    return create_spdb_pdf(
        tmp_path / "statement.pdf",
        "203002",
        [
            [
                *("20300125", "20300126", "消费 payee-a", "8888", "￥33.00"),
                *("20300203", "20300203", "退货 payee-a", "8888", "￥-33.00"),
                *("20300205", "20300205", "分期付款-payee-b", "第01期共06期"),
                *("1234", "￥50.00"),
            ],
            [
                *("20300210", "20300210", "还款", "8888", "￥-230.00", "-230.00(CNY)"),
                *("20300218", "20300219", "境外消费 payee-c", "1234", "￥77.00"),
                "6.00(USD)",
            ],
        ],
    )


class TestSPDBPdf:
    """Tests for the PDF statement path."""

    def test_detected_by_title(self, spdb_pdf_file: Path) -> None:
        assert SPDBCreditProvider.can_handle(spdb_pdf_file)
        assert auto_detect_provider(spdb_pdf_file).provider_id == "spdb_credit"  # type: ignore[union-attr]

    def test_keyword_filename_without_title_is_rejected(self, tmp_path: Path) -> None:
        other = tmp_path / "浦发借记卡spdb.pdf"

        doc = fitz.open()
        doc.new_page().insert_text((40, 40), "other statement")
        doc.save(other)
        doc.close()
        assert not SPDBCreditProvider.can_handle(other)

    @pytest.mark.parametrize(
        ("example", "provider_id"),
        [("boc_credit", "boc_credit"), ("spdb_debit", "spdb_debit")],
    )
    def test_other_bank_pdfs_keep_their_provider(
        self, example: str, provider_id: str
    ) -> None:
        [pdf] = (EXAMPLES / example).glob("*.pdf")

        assert not SPDBCreditProvider.can_handle(pdf)
        assert auto_detect_provider(pdf).provider_id == provider_id  # type: ignore[union-attr]

    def test_parse_rows_across_pages(self, spdb_pdf_file: Path) -> None:
        transactions = SPDBCreditProvider().parse(spdb_pdf_file)

        assert [
            (t.date, t.post_date, t.description, t.card_last4, t.amount, t.currency)
            for t in transactions
        ] == [
            (date(2030, 1, 25), date(2030, 1, 26), "消费 payee-a", "8888", Decimal("33.00"), "CNY"),
            (date(2030, 2, 3), date(2030, 2, 3), "退货 payee-a", "8888", Decimal("-33.00"), "CNY"),
            (date(2030, 2, 5), date(2030, 2, 5), "分期付款-payee-b第01期共06期", "1234", Decimal("50.00"), "CNY"),
            (date(2030, 2, 10), date(2030, 2, 10), "还款", "8888", Decimal("-230.00"), "CNY"),
            (date(2030, 2, 18), date(2030, 2, 19), "境外消费 payee-c", "1234", Decimal("77.00"), "CNY"),
        ]  # fmt: skip
        assert [t.source_line for t in transactions] == [1, 2, 3, 4, 5]
        assert all(t.provider == "spdb_credit" for t in transactions)

    def test_metadata_matches_xls_shape(self, spdb_pdf_file: Path) -> None:
        transactions = SPDBCreditProvider().parse(spdb_pdf_file)
        cycle = (date(2030, 1, 21), date(2030, 2, 20))

        assert transactions[0].metadata == {STATEMENT_CYCLE: cycle}
        assert transactions[3].metadata == {
            ORIGINAL_AMOUNT: Decimal("-230.00"),
            ORIGINAL_CURRENCY: "CNY",
            STATEMENT_CYCLE: cycle,
        }
        assert transactions[4].metadata[ORIGINAL_AMOUNT] == Decimal("6.00")
        assert transactions[4].metadata[ORIGINAL_CURRENCY] == "USD"
        assert {t.statement_period for t in transactions} == {cycle}

    @pytest.mark.parametrize(
        ("month", "day", "cycle"),
        [
            ("203001", "20", (date(2029, 12, 21), date(2030, 1, 20))),
            ("203003", "31", (date(2030, 3, 1), date(2030, 3, 31))),
            ("203003", "30", (date(2030, 3, 1), date(2030, 3, 30))),
            ("203002", "30", (date(2030, 1, 31), date(2030, 2, 28))),
        ],
    )
    def test_cycle_ends_on_billing_day(
        self, tmp_path: Path, month: str, day: str, cycle: tuple[date, date]
    ) -> None:
        [transaction] = SPDBCreditProvider().parse(one_row_pdf(tmp_path, month, day))

        assert transaction.metadata[STATEMENT_CYCLE] == cycle

    @pytest.mark.parametrize(
        ("days", "start"),
        [(("10", "25"), date(2030, 2, 11)), (("25", "10"), date(2030, 2, 26))],
    )
    @pytest.mark.parametrize("reverse", [False, True])
    def test_cycle_starts_after_the_previous_statement_of_the_run(
        self, tmp_path: Path, days: tuple[str, str], start: date, reverse: bool
    ) -> None:
        """A moved billing day joins the cycle to the previous statement's end."""
        paths = [
            one_row_pdf(tmp_path, "203002", days[0]),
            one_row_pdf(tmp_path, "203003", days[1]),
        ]
        provider = SPDBCreditProvider()
        parsed = {p: provider.parse(p) for p in (paths[::-1] if reverse else paths)}

        [coverage] = provider.coverage(paths[1], parsed[paths[1]])

        assert (coverage.start, coverage.end) == (start, date(2030, 3, int(days[1])))

    def test_row_without_card_or_amount_is_skipped_with_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = create_spdb_pdf(
            tmp_path / "s.pdf",
            "203002",
            [
                [
                    *("20300203", "20300203", "消费 payee-a"),
                    *("20300204", "20300204", "消费 payee-b", "1234", "￥33.00"),
                    *("20300205", "20300205", "消费 payee-c", "1234", "HK$6.00"),
                    *("20300206", "20300206", "消费 payee-d", "8888", "￥47.00"),
                ]
            ],
        )

        with caplog.at_level(logging.WARNING):
            transactions = SPDBCreditProvider().parse(path)

        assert [(t.date, t.description, t.amount) for t in transactions] == [
            (date(2030, 2, 4), "消费 payee-b", Decimal("33.00")),
            (date(2030, 2, 6), "消费 payee-d", Decimal("47.00")),
        ]
        skipped = [r for r in caplog.records if "skipped an SPDB PDF row" in r.message]
        assert len(skipped) == 2
        assert all("s.pdf" in r.message for r in skipped)

    def test_rows_without_cycle_have_no_cycle_metadata(self, tmp_path: Path) -> None:
        path = create_spdb_pdf(
            tmp_path / "s.pdf",
            "",
            [["20300125", "20300126", "消费 payee-a", "8888", "￥33.00"]],
        )

        [transaction] = SPDBCreditProvider().parse(path)
        assert STATEMENT_CYCLE not in transaction.metadata


class TestSPDBCoverage:
    """An XLS report and a PDF of the same cycle compete; the XLS wins."""

    def test_pdf_covers_cycle_per_card(self, spdb_pdf_file: Path) -> None:
        provider = SPDBCreditProvider()
        coverages = provider.coverage(spdb_pdf_file, provider.parse(spdb_pdf_file))

        assert [
            (c.account, c.start, c.end, len(c.rows), c.source) for c in coverages
        ] == [
            ("8888", date(2030, 1, 21), date(2030, 2, 20), 3, "reissue"),
            ("1234", date(2030, 1, 21), date(2030, 2, 20), 2, "reissue"),
        ]

    def test_xls_covers_booked_dates(self, spdb_xls_file: Path) -> None:
        provider = SPDBCreditProvider()
        coverages = provider.coverage(spdb_xls_file, provider.parse(spdb_xls_file))

        assert [(c.account, c.start, c.end, c.source) for c in coverages] == [
            ("8888", date(2030, 1, 3), date(2030, 2, 1), "statement"),
            ("1234", date(2030, 1, 18), date(2030, 1, 18), "statement"),
        ]

    @pytest.mark.parametrize("pdf_first", [True, False])
    def test_xls_wins_over_pdf_of_same_month(
        self, tmp_path: Path, pdf_first: bool
    ) -> None:
        from bean_sieve.api import full_reconcile

        xls = create_spdb_xls(
            [["20300205", "20300206", "消费 payee-a", "8888", "主卡", "人民币", "33.00", "-"]],
            tmp_path / "report.xls",
        )  # fmt: skip
        same_month = create_spdb_pdf(
            tmp_path / "reissue.pdf",
            "203002",
            [["20300205", "20300206", "消费 payee-a", "8888", "￥33.00"]],
        )
        pdf_only = create_spdb_pdf(
            tmp_path / "earlier.pdf",
            "203001",
            [["20300110", "20300110", "消费 payee-b", "8888", "￥47.00"]],
        )
        (tmp_path / "bean-sieve.yaml").write_text(
            "providers:\n  spdb_credit:\n    accounts:\n"
            '      "8888": Liabilities:CreditCard:SPDB\n',
            encoding="utf-8",
        )
        (tmp_path / "main.bean").write_text(
            "2030-01-01 open Liabilities:CreditCard:SPDB CNY\n", encoding="utf-8"
        )
        paths = (
            [same_month, xls, pdf_only] if pdf_first else [xls, same_month, pdf_only]
        )

        result = full_reconcile(
            statement_paths=paths,
            ledger_path=tmp_path / "main.bean",
            config_path=tmp_path / "bean-sieve.yaml",
        )

        selection = result.coverage
        assert selection is not None
        assert sorted(e.path.name for e in selection.adopted) == [
            "earlier.pdf",
            "report.xls",
        ]
        assert [d.entry.path.name for d in selection.drops] == ["reissue.pdf"]
        assert selection.mismatches == []
        assert sorted(t.amount for t in result.match_result.missing) == [
            Decimal("33.00"),
            Decimal("47.00"),
        ]
