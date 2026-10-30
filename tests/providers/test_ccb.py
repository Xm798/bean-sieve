"""Tests for China Construction Bank (CCB) credit card statement provider."""

import base64
from datetime import date
from decimal import Decimal
from pathlib import Path

import fitz
import pytest

from bean_sieve.api import _provider_account
from bean_sieve.config.schema import Config
from bean_sieve.core.coverage import CoverageEntry, select
from bean_sieve.core.metadata_keys import (
    ORIGINAL_AMOUNT,
    ORIGINAL_CURRENCY,
    STATEMENT_CYCLE,
)
from bean_sieve.providers import PROVIDERS, get_provider
from bean_sieve.providers.banks.credit.ccb import CCBCreditProvider

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def create_ccb_eml(html_content: str) -> str:
    """Create an EML file content with the given HTML."""
    encoded = base64.b64encode(html_content.encode("utf-8")).decode("ascii")
    return f"""From: service@vip.ccb.com
Subject: =?utf-8?B?5Lit5Zu95bu66K6+6ZO26KGM5L+h55So5Y2h55S15a2Q6LSm5Y2V?=
To: test@example.com
MIME-Version: 1.0
Content-Type: text/html; charset="utf-8"
Content-Transfer-Encoding: base64

{encoded}
"""


@pytest.fixture
def ccb_html_content():
    """Sample CCB statement HTML content."""
    return """<html>
<head><title>龙卡信用卡对账单</title></head>
<body>
<table>
    <tr><td>账单周期Statement Cycle</td><td>2025/11/24-2025/12/23</td></tr>
</table>
<table>
    <tr><td>【交易明细】</td></tr>
    <tr>
        <td>交易日</td>
        <td>银行记账日</td>
        <td>卡号后四位</td>
        <td>交易描述</td>
        <td>交易币/金额</td>
        <td>结算币/金额</td>
    </tr>
    <tr>
        <td>[人民币账户] RMB Account</td>
        <td>上期账单余额(Previous Balance)</td>
        <td>14,052.90</td>
    </tr>
    <tr>
        <td>2025-11-25</td>
        <td>2025-11-25</td>
        <td>4680</td>
        <td>支付宝-测试商户</td>
        <td>CNY</td>
        <td>3,874.90</td>
        <td>CNY</td>
        <td>3,874.90</td>
    </tr>
    <tr>
        <td>2025-11-27</td>
        <td>2025-11-27</td>
        <td>4680</td>
        <td>积分兑换年费</td>
        <td>CNY</td>
        <td>-1,800.00</td>
        <td>CNY</td>
        <td>-1,800.00</td>
    </tr>
    <tr>
        <td>2025-12-04</td>
        <td>2025-12-05</td>
        <td>4680</td>
        <td>银联入账 还款</td>
        <td>CNY</td>
        <td>-12,252.90</td>
        <td>CNY</td>
        <td>-12,252.90</td>
    </tr>
    <tr>
        <td>2025-12-09</td>
        <td>2025-12-09</td>
        <td>4680</td>
        <td>财付通-餐饮消费</td>
        <td>CNY</td>
        <td>99.00</td>
        <td>CNY</td>
        <td>99.00</td>
    </tr>
</table>
</body>
</html>"""


@pytest.fixture
def ccb_eml_file(tmp_path, ccb_html_content):
    """Create a temporary CCB EML file."""
    file_path = tmp_path / "中国建设银行信用卡电子账单.eml"
    eml_content = create_ccb_eml(ccb_html_content)
    file_path.write_text(eml_content, encoding="utf-8")
    return file_path


class TestCCBCreditProvider:
    """Tests for CCBCreditProvider."""

    def test_provider_registration(self):
        """Test that CCB provider is properly registered."""
        provider = get_provider("ccb_credit")
        assert isinstance(provider, CCBCreditProvider)
        assert provider.provider_id == "ccb_credit"
        assert provider.provider_name == "建设银行信用卡"
        assert ".eml" in provider.supported_formats

    def test_can_handle(self):
        """Test file format detection."""
        assert CCBCreditProvider.can_handle(Path("中国建设银行信用卡电子账单.eml"))
        assert CCBCreditProvider.can_handle(Path("建设银行信用卡账单.eml"))
        assert not CCBCreditProvider.can_handle(Path("ccb_statement.csv"))
        assert not CCBCreditProvider.can_handle(Path("statement.eml"))

    def test_per_card_statement(self):
        """Test that per_card_statement is True for CCB."""
        provider = CCBCreditProvider()
        assert provider.per_card_statement is True

    def test_parse_transactions(self, ccb_eml_file):
        """Test parsing transactions from EML file."""
        provider = CCBCreditProvider()
        transactions = provider.parse(ccb_eml_file)

        assert len(transactions) == 4

        # Check first spending transaction
        txn1 = transactions[0]
        assert txn1.date == date(2025, 11, 25)
        assert txn1.post_date == date(2025, 11, 25)
        assert txn1.amount == Decimal("3874.90")
        assert txn1.currency == "CNY"
        assert txn1.card_last4 == "4680"
        assert "支付宝" in txn1.description
        assert txn1.provider == "ccb_credit"
        assert txn1.is_expense

        # Check negative amount transaction (fee refund)
        txn2 = transactions[1]
        assert txn2.date == date(2025, 11, 27)
        assert txn2.amount == Decimal("-1800.00")
        assert "年费" in txn2.description
        assert txn2.is_income

        # Check payment transaction
        txn3 = transactions[2]
        assert txn3.date == date(2025, 12, 4)
        assert txn3.post_date == date(2025, 12, 5)
        assert txn3.amount == Decimal("-12252.90")
        assert "还款" in txn3.description
        assert txn3.is_income

        # Check small expense
        txn4 = transactions[3]
        assert txn4.date == date(2025, 12, 9)
        assert txn4.amount == Decimal("99.00")
        assert "财付通" in txn4.description

    def test_statement_period_extraction(self, ccb_eml_file):
        """Test that statement period is properly extracted."""
        provider = CCBCreditProvider()
        transactions = provider.parse(ccb_eml_file)

        assert len(transactions) > 0
        txn = transactions[0]
        assert txn.statement_period is not None
        assert txn.statement_period[0] == date(2025, 11, 24)
        assert txn.statement_period[1] == date(2025, 12, 23)

    def test_empty_statement(self, tmp_path):
        """Test handling of statement with no transactions."""
        html = """<html>
<body>
<table><tr><td>账单周期Statement Cycle</td><td>2025/11/24-2025/12/23</td></tr></table>
<table>
    <tr><td>【交易明细】</td></tr>
    <tr><td>本期无交易记录</td></tr>
</table>
</body></html>"""
        file_path = tmp_path / "中国建设银行信用卡电子账单.eml"
        file_path.write_text(create_ccb_eml(html), encoding="utf-8")

        provider = CCBCreditProvider()
        transactions = provider.parse(file_path)

        assert transactions == []


class TestCCBAmountParsing:
    """Tests for CCB amount parsing edge cases."""

    def test_amount_with_thousands_separator(self, tmp_path):
        """Test parsing amounts with comma separators."""
        html = """<html>
<body>
<table><tr><td>账单周期Statement Cycle</td><td>2025/11/24-2025/12/23</td></tr></table>
<table>
    <tr><td>【交易明细】</td></tr>
    <tr>
        <td>2025-11-25</td>
        <td>2025-11-25</td>
        <td>4680</td>
        <td>大额消费</td>
        <td>CNY</td>
        <td>12,345.67</td>
        <td>CNY</td>
        <td>12,345.67</td>
    </tr>
</table>
</body></html>"""
        file_path = tmp_path / "中国建设银行信用卡电子账单.eml"
        file_path.write_text(create_ccb_eml(html), encoding="utf-8")

        provider = CCBCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("12345.67")

    def test_cross_year_statement_period(self, tmp_path):
        """Test statement period spanning across years."""
        html = """<html>
<body>
<table><tr><td>账单周期Statement Cycle</td><td>2025/12/24-2026/01/23</td></tr></table>
<table>
    <tr><td>【交易明细】</td></tr>
    <tr>
        <td>2025-12-26</td>
        <td>2025-12-26</td>
        <td>4680</td>
        <td>消费测试</td>
        <td>CNY</td>
        <td>100.00</td>
        <td>CNY</td>
        <td>100.00</td>
    </tr>
    <tr>
        <td>2026-01-06</td>
        <td>2026-01-07</td>
        <td>4680</td>
        <td>还款测试</td>
        <td>CNY</td>
        <td>-500.00</td>
        <td>CNY</td>
        <td>-500.00</td>
    </tr>
</table>
</body></html>"""
        file_path = tmp_path / "中国建设银行信用卡电子账单.eml"
        file_path.write_text(create_ccb_eml(html), encoding="utf-8")

        provider = CCBCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 2
        assert transactions[0].date == date(2025, 12, 26)
        assert transactions[1].date == date(2026, 1, 6)
        # Statement period should span years
        assert transactions[0].statement_period == (
            date(2025, 12, 24),
            date(2026, 1, 23),
        )


class TestCCBCrossYearDates:
    """Tests for CCB handling of dates in cross-year statements."""

    def test_dates_parsed_correctly(self, tmp_path):
        """Test that transaction dates are parsed correctly regardless of year."""
        html = """<html>
<body>
<table><tr><td>账单周期Statement Cycle</td><td>2025/12/24-2026/01/23</td></tr></table>
<table>
    <tr><td>【交易明细】</td></tr>
    <tr>
        <td>2025-12-31</td>
        <td>2025-12-31</td>
        <td>4680</td>
        <td>跨年消费</td>
        <td>CNY</td>
        <td>888.00</td>
        <td>CNY</td>
        <td>888.00</td>
    </tr>
    <tr>
        <td>2026-01-01</td>
        <td>2026-01-01</td>
        <td>4680</td>
        <td>新年消费</td>
        <td>CNY</td>
        <td>666.00</td>
        <td>CNY</td>
        <td>666.00</td>
    </tr>
</table>
</body></html>"""
        file_path = tmp_path / "中国建设银行信用卡电子账单.eml"
        file_path.write_text(create_ccb_eml(html), encoding="utf-8")

        provider = CCBCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 2
        # CCB provides full dates (YYYY-MM-DD), so parsing is straightforward
        assert transactions[0].date == date(2025, 12, 31)
        assert transactions[1].date == date(2026, 1, 1)


class TestCCBStatementPeriod:
    """Tests for per-card statement period assignment."""

    def test_cycle_bounds_a_row_dated_outside_it(self, tmp_path):
        """Test that an out-of-cycle row leaves the card's period at the cycle."""
        html = """<html>
<body>
<table>
    <tr><td>账单周期Statement Cycle</td><td>2030/03/01-2030/03/31</td></tr>
</table>
<table>
    <tr><td>【交易明细】</td></tr>
    <tr>
        <td>2030-02-27</td>
        <td>2030-03-02</td>
        <td>4680</td>
        <td>merchant-a</td>
        <td>CNY</td>
        <td>10.00</td>
        <td>CNY</td>
        <td>10.00</td>
    </tr>
    <tr>
        <td>2030-03-10</td>
        <td>2030-03-10</td>
        <td>4680</td>
        <td>merchant-b</td>
        <td>CNY</td>
        <td>20.00</td>
        <td>CNY</td>
        <td>20.00</td>
    </tr>
</table>
</body>
</html>"""
        file_path = tmp_path / "中国建设银行信用卡电子账单.eml"
        file_path.write_text(create_ccb_eml(html), encoding="utf-8")

        transactions = CCBCreditProvider().parse(file_path)

        assert len(transactions) == 2
        for txn in transactions:
            assert txn.statement_period == (date(2030, 3, 1), date(2030, 3, 31))


# x of each cell in the reissued PDF, spaced so every cell stays a span of its own.
_PDF_XS = [30, 85, 153, 192, 400, 430, 495, 525]

# (trans date, post date, card, description, currency, amount, currency, amount)
PdfRow = tuple[str, str, str, str, str, str, str, str]

ROWS: list[PdfRow] = [
    (
        "2030-03-02",
        "2030-03-02",
        "4680",
        "支付宝-payee-a",
        "CNY",
        "51.00",
        "CNY",
        "51.00",
    ),
    ("2030-03-05", "2030-03-06", "4680", "shop-b", "HKD", "62.00", "CNY", "57.00"),
    (
        "2030-03-08",
        "2030-03-08",
        "4680/扫码",
        "payee-c",
        "CNY",
        "64.00",
        "CNY",
        "64.00",
    ),
    (
        "2030-03-12",
        "2030-03-12",
        "4680",
        "退货-shop-b",
        "CNY",
        "-57.00",
        "CNY",
        "-57.00",
    ),
    (
        "2030-03-20",
        "2030-03-21",
        "4680",
        "银联入账 还款",
        "CNY",
        "-1,130.00",
        "CNY",
        "-1,130.00",
    ),
]


def write_ccb_eml(path: Path, rows: list[PdfRow], cycle: str) -> Path:
    body = "\n".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
    )
    html = f"""<html><body>
<table><tr><td>账单周期Statement Cycle</td><td>{cycle}</td></tr></table>
<table>
<tr><td>【交易明细】</td></tr>
<tr><td>[人民币账户] RMB Account</td><td>上期账单余额(Previous Balance)</td><td>0.00</td></tr>
{body}
</table></body></html>"""
    path.write_text(create_ccb_eml(html), encoding="utf-8")
    return path


def write_ccb_pdf(
    path: Path, rows: list[PdfRow], cycle: str, rows_per_page: int = 30
) -> Path:
    """Draw a reissued CCB statement: summary page, then the detail table."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)

    def put(x: float, y: float, text: str) -> None:
        page.insert_text((x, y), text, fontname="china-s", fontsize=8)

    # The notes name the previous cycle with Chinese dates; the summary
    # prints the cycle as a cell under a row of headers.
    put(332, 158, "我们已收到您上一账单周期（2030年01月01日-2030年01月31日）的还款。")
    put(38, 224, "龙卡信用卡对账单")
    put(41, 383, "本期账单日")
    put(245, 383, "账单周期")
    put(423, 383, "本期到期还款日")
    put(141, 387.5, "2030-03-31")
    put(310, 387.5, cycle)
    put(512, 387.5, "2030/04/20")
    for start in range(0, len(rows), rows_per_page):
        page = doc.new_page(width=595, height=842)
        put(27, 104, "【交易明细】")
        for x, label in zip(
            [40, 87, 142, 281, 427, 512],
            [
                "交易日",
                "银行记账日",
                "卡号后四位",
                "交易描述",
                "交易币/金额",
                "结算币/金额",
            ],
            strict=True,
        ):
            put(x, 117, label)
        put(27, 139, "[人民币账户] RMB Account")
        put(537, 139, "0.00")
        y = 151.0
        for row in rows[start : start + rows_per_page]:
            for x, cell in zip(_PDF_XS, row, strict=True):
                if cell:
                    put(x, y, cell)
            y += 12
        put(252, y, "***　结束　The　End　***")
        put(551, 785, "0001")
    doc.save(path)
    doc.close()
    return path


def shape(txns):
    return [
        (
            t.date,
            t.post_date,
            t.amount,
            t.currency,
            t.description,
            t.card_last4,
            t.statement_period,
            t.metadata,
        )
        for t in txns
    ]


class TestCCBReissue:
    """Tests for the reissued PDF statement."""

    def test_rows(self, tmp_path):
        txns = CCBCreditProvider().parse(
            write_ccb_pdf(tmp_path / "r.pdf", ROWS, "2030/03/01-2030/03/31")
        )

        assert [(t.date, t.amount, t.card_last4) for t in txns] == [
            (date(2030, 3, 2), Decimal("51.00"), "4680"),
            (date(2030, 3, 5), Decimal("57.00"), "4680"),
            (date(2030, 3, 8), Decimal("64.00"), "4680"),
            (date(2030, 3, 12), Decimal("-57.00"), "4680"),
            (date(2030, 3, 20), Decimal("-1130.00"), "4680"),
        ]
        assert txns[1].post_date == date(2030, 3, 6)
        assert txns[4].description == "银联入账 还款"
        assert txns[0].statement_period == (date(2030, 3, 1), date(2030, 3, 31))
        assert txns[0].metadata[STATEMENT_CYCLE] == (
            date(2030, 3, 1),
            date(2030, 3, 31),
        )

    def test_cycle_is_the_cell_under_its_header(self, tmp_path):
        provider = CCBCreditProvider()
        pdf = write_ccb_pdf(tmp_path / "r.pdf", ROWS[:1], "2030/03/01-2030/03/31")

        txns = provider.parse(pdf)

        assert txns[0].statement_period == (date(2030, 3, 1), date(2030, 3, 31))
        [cov] = provider.coverage(pdf, txns)
        assert (cov.account, cov.start, cov.end) == (
            "4680",
            date(2030, 3, 1),
            date(2030, 3, 31),
        )

    def test_same_transactions_as_mail(self, tmp_path):
        cycle = "2030/03/01-2030/03/31"
        pdf = write_ccb_pdf(tmp_path / "r.pdf", ROWS, cycle)
        eml = write_ccb_eml(tmp_path / "s.eml", ROWS, cycle)

        provider = CCBCreditProvider()
        assert shape(provider.parse(pdf)) == shape(provider.parse(eml))

    def test_foreign_currency_books_the_settlement_amount(self, tmp_path):
        for path in (
            write_ccb_pdf(tmp_path / "r.pdf", ROWS, "2030/03/01-2030/03/31"),
            write_ccb_eml(tmp_path / "s.eml", ROWS, "2030/03/01-2030/03/31"),
        ):
            txn = CCBCreditProvider().parse(path)[1]
            assert (txn.amount, txn.currency) == (Decimal("57.00"), "CNY")
            assert txn.metadata[ORIGINAL_AMOUNT] == Decimal("62.00")
            assert txn.metadata[ORIGINAL_CURRENCY] == "HKD"

    def test_rows_across_pages(self, tmp_path):
        pdf = write_ccb_pdf(
            tmp_path / "r.pdf", ROWS, "2030/03/01-2030/03/31", rows_per_page=2
        )
        assert len(CCBCreditProvider().parse(pdf)) == len(ROWS)

    def test_only_this_provider_claims_it(self, tmp_path):
        pdf = write_ccb_pdf(tmp_path / "statement.pdf", ROWS, "2030/03/01-2030/03/31")
        assert [p for p, c in PROVIDERS.items() if c.can_handle(pdf)] == ["ccb_credit"]

    def test_claims_no_other_example_statement(self):
        for path in sorted(EXAMPLES.glob("*/*")):
            if path.parent.name != "ccb_credit":
                assert not CCBCreditProvider.can_handle(path), path


class TestCCBCoverage:
    def test_one_entry_per_card_for_the_printed_cycle(self, tmp_path):
        provider = CCBCreditProvider()
        for path, source in (
            (
                write_ccb_pdf(tmp_path / "r.pdf", ROWS, "2030/03/01-2030/03/31"),
                "reissue",
            ),
            (
                write_ccb_eml(tmp_path / "s.eml", ROWS, "2030/03/01-2030/03/31"),
                "statement",
            ),
        ):
            covs = provider.coverage(path, provider.parse(path))
            assert [
                (c.account, c.start, c.end, len(c.rows), c.source) for c in covs
            ] == [
                ("4680", date(2030, 3, 1), date(2030, 3, 31), 5, source),
            ]

    @pytest.mark.parametrize("reverse", [False, True])
    def test_mail_outranks_its_reissue(self, tmp_path, reverse):
        provider = CCBCreditProvider()
        paths = [
            write_ccb_pdf(tmp_path / "r.pdf", ROWS, "2030/03/01-2030/03/31"),
            write_ccb_eml(tmp_path / "s.eml", ROWS, "2030/03/01-2030/03/31"),
            write_ccb_pdf(tmp_path / "next.pdf", ROWS[:1], "2030/04/01-2030/04/30"),
        ]
        if reverse:
            paths.reverse()
        ranks = {kind: i for i, kind in enumerate(provider.coverage_priority)}
        parsed = {path: provider.parse(path) for path in paths}
        entries = [
            CoverageEntry("ccb_credit", path, cov, ranks[cov.source])
            for path in paths
            for cov in provider.coverage(path, parsed[path])
        ]
        kept = select(entries).keep([t for rows in parsed.values() for t in rows])
        by_file = {
            path.name: sum(t.source_file == path for t in kept) for path in paths
        }
        assert by_file == {"s.eml": 5, "r.pdf": 0, "next.pdf": 1}


class TestCCBCardCell:
    """A card cell can add a second suffix or a channel to the card's suffix."""

    CELL_ROWS: list[PdfRow] = [
        ("2030-03-03", "2030-03-03", "1234", "payee-a", "CNY", "36.70", "CNY", "36.70"),
        (
            "2030-03-04",
            "2030-03-04",
            "1234/扫码",
            "payee-b",
            "CNY",
            "47.00",
            "CNY",
            "47.00",
        ),
        (
            "2030-03-05",
            "2030-03-05",
            "1234/5678",
            "payee-c",
            "CNY",
            "58.30",
            "CNY",
            "58.30",
        ),
    ]

    @pytest.mark.parametrize(
        ("writer", "name"), [(write_ccb_pdf, "r.pdf"), (write_ccb_eml, "s.eml")]
    )
    def test_rows_take_the_suffix_and_keep_the_cell(self, tmp_path, writer, name):
        path = writer(tmp_path / name, self.CELL_ROWS, "2030/03/01-2030/03/31")
        provider = CCBCreditProvider()

        txns = provider.parse(path)

        assert [(t.card_last4, t.metadata.get("card_cell")) for t in txns] == [
            ("1234", None),
            ("1234", "1234/扫码"),
            ("1234", "1234/5678"),
        ]
        [cov] = provider.coverage(path, txns)
        assert (cov.account, len(cov.rows)) == ("1234", 3)
        config = Config.from_dict(
            {"providers": {"ccb_credit": {"accounts": {"1234": "Liabilities:CCB"}}}}
        )
        assert {_provider_account(t, config, {}) for t in txns} == {"Liabilities:CCB"}
