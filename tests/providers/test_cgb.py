"""Tests for China Guangfa Bank (CGB) credit card statement provider."""

import base64
from datetime import date
from decimal import Decimal
from pathlib import Path

import fitz
import pytest

from bean_sieve.core.coverage import CoverageEntry, select
from bean_sieve.core.metadata_keys import (
    ORIGINAL_AMOUNT,
    ORIGINAL_CURRENCY,
    STATEMENT_CYCLE,
)
from bean_sieve.providers import PROVIDERS, get_provider
from bean_sieve.providers.banks.credit.cgb import (
    _ISSUER,
    _ISSUER_SHORT,
    CGBCreditProvider,
)

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def create_cgb_eml(html_content: str, subject: str = "statement-subject") -> str:
    """Create an EML file content with the given HTML."""
    encoded = base64.b64encode(html_content.encode("gbk")).decode("ascii")
    encoded_subject = base64.b64encode(subject.encode("utf-8")).decode("ascii")
    return f"""From: billing@example.com
Subject: =?utf-8?B?{encoded_subject}?=
To: test@example.com
MIME-Version: 1.0
Content-Type: text/html; charset="gbk"
Content-Transfer-Encoding: base64

{encoded}
"""


@pytest.fixture
def cgb_html_content():
    """Sample CGB statement HTML content."""
    return f"""<!DOCTYPE html>
<html>
<head><title>{_ISSUER}信用卡电子账单</title></head>
<body>
<table>
    <tr><td>感谢您使用{_ISSUER}信用卡，以下是您2026年01月的信用卡账单：</td></tr>
    <tr><td>账单周期:2025/12/26-2026/01/25</td></tr>
    <tr><td>账单日:2026/01/25</td></tr>
</table>
<table>
    <tr><td>卡号末四位</td><td>本期账单金额</td></tr>
    <tr><td>1234</td><td>1,234.56</td></tr>
    <tr><td>5678</td><td>500.00</td></tr>
</table>
<table>
    卡号：6200********1234
    <tr><td>交易日期</td><td>入账日期</td><td>交易摘要</td><td>交易金额</td><td>交易货币</td><td>入账金额</td><td>入账货币</td></tr>
    <tr><td>2026/01/15 2026/01/16 (消费)支付宝-测试商户 100.50 人民币 100.50 人民币</td></tr>
    <tr><td>2026/01/10 2026/01/11 (消费)财付通-餐饮店 50.00 人民币 50.00 人民币</td></tr>
    <tr><td>2026/01/05 2026/01/05 (还款)支付宝-还款 -1,000.00 人民币 -1,000.00 人民币</td></tr>
    <tr><td>2026/01/03 2026/01/03 (赠送)bonus-a -20.00 人民币 -20.00 人民币</td></tr>
</table>
<table>
    卡号：6200********5678
    <tr><td>交易日期</td><td>入账日期</td><td>交易摘要</td><td>交易金额</td><td>交易货币</td><td>入账金额</td><td>入账货币</td></tr>
    <tr><td>2025/12/28 2025/12/29 (消费)美团支付-外卖订单 30.00 人民币 30.00 人民币</td></tr>
</table>
</body>
</html>"""


@pytest.fixture
def cgb_eml_file(tmp_path, cgb_html_content):
    """Create a temporary CGB EML file."""
    file_path = tmp_path / f"{_ISSUER_SHORT}信用卡 2026年01月电子账单.eml"
    eml_content = create_cgb_eml(cgb_html_content)
    file_path.write_text(eml_content, encoding="utf-8")
    return file_path


class TestCGBCreditProvider:
    """Tests for CGBCreditProvider."""

    def test_provider_registration(self):
        """Test that CGB provider is properly registered."""
        provider = get_provider("cgb_credit")
        assert isinstance(provider, CGBCreditProvider)
        assert provider.provider_id == "cgb_credit"
        assert provider.provider_name == f"{_ISSUER}信用卡"
        assert ".eml" in provider.supported_formats

    def test_can_handle(self):
        """Test file format detection."""
        assert CGBCreditProvider.can_handle(
            Path(f"{_ISSUER_SHORT}信用卡 2026年01月电子账单.eml")
        )
        assert CGBCreditProvider.can_handle(Path(f"{_ISSUER}信用卡电子账单.eml"))
        assert not CGBCreditProvider.can_handle(Path("cgb_statement.csv"))
        assert not CGBCreditProvider.can_handle(Path("statement.eml"))

    def test_can_handle_by_short_name_subject(self, tmp_path):
        """Older statements name the issuer only by its short name."""
        path = tmp_path / "2030-01_statement.eml"
        path.write_text(
            create_cgb_eml(
                "<html><body>账单周期:2030/01/01-2030/01/31</body></html>",
                subject=f"{_ISSUER_SHORT}信用卡 2030年01月电子账单",
            ),
            encoding="utf-8",
        )
        assert CGBCreditProvider.can_handle(path)

    def test_rejects_other_bank_credit_card_eml(self, tmp_path):
        path = tmp_path / "2030-01_statement.eml"
        path.write_text(
            create_cgb_eml(
                "<html><body>招商银行信用卡电子账单</body></html>",
                subject="招商银行信用卡 2030年01月电子账单",
            ),
            encoding="utf-8",
        )
        assert not CGBCreditProvider.can_handle(path)

    def test_parse_transactions(self, cgb_eml_file):
        """Test parsing transactions from EML file."""
        provider = CGBCreditProvider()
        transactions = provider.parse(cgb_eml_file)

        assert len(transactions) == 5

        # Check first spending transaction
        spending1 = transactions[0]
        assert spending1.date == date(2026, 1, 15)
        assert spending1.post_date == date(2026, 1, 16)
        assert spending1.amount == Decimal("100.50")
        assert spending1.currency == "CNY"
        assert spending1.card_last4 == "1234"
        assert "(消费)" in spending1.description
        assert "支付宝" in spending1.description
        assert spending1.provider == "cgb_credit"
        assert spending1.is_expense

        # Check payment transaction (should be negative)
        payment = transactions[2]
        assert payment.date == date(2026, 1, 5)
        assert payment.amount == Decimal("-1000.00")
        assert "(还款)" in payment.description
        assert payment.is_income

        # Check bonus transaction (should be negative)
        bonus = transactions[3]
        assert bonus.date == date(2026, 1, 3)
        assert bonus.amount == Decimal("-20.00")
        assert "(赠送)" in bonus.description

        # Check transaction from second card
        card2_txn = transactions[4]
        assert card2_txn.card_last4 == "5678"
        assert card2_txn.date == date(2025, 12, 28)

    def test_statement_period(self, cgb_eml_file):
        """Test that statement period is properly extracted."""
        provider = CGBCreditProvider()
        transactions = provider.parse(cgb_eml_file)

        assert len(transactions) > 0
        for txn in transactions:
            assert txn.statement_period == (date(2025, 12, 26), date(2026, 1, 25))

    def test_metadata_extraction(self, cgb_eml_file):
        """Test that metadata is properly extracted."""
        provider = CGBCreditProvider()
        transactions = provider.parse(cgb_eml_file)

        txn = transactions[0]
        assert "trans_type" in txn.metadata
        assert txn.metadata["trans_type"] == "消费"

        payment = transactions[2]
        assert payment.metadata["trans_type"] == "还款"

    def test_per_card_statement_flag(self):
        """Test that per_card_statement flag is set correctly."""
        provider = CGBCreditProvider()
        assert provider.per_card_statement is True


class TestCGBAmountParsing:
    """Tests for CGB amount parsing edge cases."""

    def test_amount_with_thousands_separator(self, tmp_path):
        """Test parsing amounts with comma separators."""
        html = """<html>
<body>
<table><tr><td>账单周期:2025/12/26-2026/01/25</td></tr></table>
<table>
    卡号：6200********1234
    2026/01/15 2026/01/16 (消费)大额消费 12,345.67 人民币 12,345.67 人民币
</table>
</body></html>"""
        file_path = tmp_path / f"{_ISSUER_SHORT}信用卡.eml"
        file_path.write_text(create_cgb_eml(html), encoding="utf-8")

        provider = CGBCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("12345.67")

    def test_usd_amount(self, tmp_path):
        """Test parsing USD amounts."""
        html = """<html>
<body>
<table><tr><td>账单周期:2025/12/26-2026/01/25</td></tr></table>
<table>
    卡号：6200********1234
    2026/01/15 2026/01/16 (消费)海外消费 100.50 美元 100.50 美元
</table>
</body></html>"""
        file_path = tmp_path / f"{_ISSUER_SHORT}信用卡.eml"
        file_path.write_text(create_cgb_eml(html), encoding="utf-8")

        provider = CGBCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("100.50")
        assert transactions[0].currency == "USD"
        assert transactions[0].original_amount is None

    def test_third_currency_on_usd_card(self, tmp_path):
        """Test that HKD spending billed in USD keeps the HKD original."""
        html = """<html>
<body>
<table><tr><td>账单周期:2030/01/01-2030/01/31</td></tr></table>
<table>
    卡号：6200********1234
    2030/01/02 2030/01/03 (消费)merchant-a 80.00 港币 10.00 美元
    2030/01/04 2030/01/04 (还款)rebate-a -2.00 美元 -2.00 美元
</table>
</body></html>"""
        file_path = tmp_path / f"{_ISSUER_SHORT}信用卡.eml"
        file_path.write_text(create_cgb_eml(html), encoding="utf-8")

        transactions = CGBCreditProvider().parse(file_path)

        assert len(transactions) == 2
        spending, rebate = transactions
        assert spending.amount == Decimal("10.00")
        assert spending.currency == "USD"
        assert spending.original_amount == Decimal("80.00")
        assert spending.original_currency == "HKD"
        assert rebate.amount == Decimal("-2.00")
        assert rebate.original_amount is None

    def test_empty_statement(self, tmp_path):
        """Test handling of statement with no transactions."""
        html = """<html>
<body>
<table><tr><td>账单周期:2025/12/26-2026/01/25</td></tr></table>
<table><tr><td>本期无交易记录</td></tr></table>
</body></html>"""
        file_path = tmp_path / f"{_ISSUER_SHORT}信用卡.eml"
        file_path.write_text(create_cgb_eml(html), encoding="utf-8")

        provider = CGBCreditProvider()
        transactions = provider.parse(file_path)

        assert transactions == []


class TestCGBCrossYearDates:
    """Tests for cross-year date handling."""

    def test_cross_year_statement_period(self, tmp_path):
        """Test that transactions in cross-year period are parsed correctly."""
        html = """<html>
<body>
<table><tr><td>账单周期:2025/12/26-2026/01/25</td></tr></table>
<table>
    卡号：6200********1234
    2025/12/28 2025/12/29 (消费)年末消费 100.00 人民币 100.00 人民币
    2026/01/02 2026/01/03 (消费)年初消费 200.00 人民币 200.00 人民币
</table>
</body></html>"""
        file_path = tmp_path / f"{_ISSUER_SHORT}信用卡.eml"
        file_path.write_text(create_cgb_eml(html), encoding="utf-8")

        provider = CGBCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 2
        assert transactions[0].date == date(2025, 12, 28)
        assert transactions[1].date == date(2026, 1, 2)


class TestCGBStatementPeriod:
    """Tests for per-card statement period assignment."""

    def test_cycle_widens_only_for_the_card_dated_outside_it(self, tmp_path):
        """Test that an out-of-cycle row widens its own card's period."""
        html = """<!DOCTYPE html>
<html>
<body>
<table>
    <tr><td>账单周期:2030/03/01-2030/03/31</td></tr>
</table>
<table>
    卡号：6200********1234
    <tr><td>2030/02/27 2030/03/02 (消费)merchant-a 10.00 人民币 10.00 人民币</td></tr>
</table>
<table>
    卡号：6200********5678
    <tr><td>2030/03/10 2030/03/10 (消费)merchant-b 20.00 人民币 20.00 人民币</td></tr>
</table>
</body>
</html>"""
        file_path = tmp_path / f"{_ISSUER_SHORT}信用卡电子账单.eml"
        file_path.write_text(create_cgb_eml(html), encoding="utf-8")

        transactions = CGBCreditProvider().parse(file_path)

        periods = {txn.card_last4: txn.statement_period for txn in transactions}
        assert periods["1234"] == (date(2030, 2, 27), date(2030, 3, 31))
        assert periods["5678"] == (date(2030, 3, 1), date(2030, 3, 31))


# (trans date, post date, "(type)summary", amount, currency, booked, currency)
Row = tuple[str, str, str, str, str, str, str]

CARDS: dict[str, list[Row]] = {
    "2468": [
        (
            "2030/03/02",
            "2030/03/03",
            "(消费)支付宝-payee-a",
            "51.00",
            "人民币",
            "51.00",
            "人民币",
        ),
        (
            "2030/03/05",
            "2030/03/06",
            "(消费)shop-b",
            "13.50",
            "美元",
            "97.00",
            "人民币",
        ),
        (
            "2030/03/09",
            "2030/03/09",
            "(还款)(云闪付)银联入账",
            "-930.00",
            "人民币",
            "-930.00",
            "人民币",
        ),
    ],
    "1357": [
        (
            "2030/03/12",
            "2030/03/13",
            "(消费)payee-c",
            "64.00",
            "人民币",
            "64.00",
            "人民币",
        ),
        (
            "2030/03/14",
            "2030/03/14",
            "(赠送)bonus-d",
            "-15.50",
            "人民币",
            "-15.50",
            "人民币",
        ),
    ],
}
CYCLE = ("2030/03/01", "2030/03/31")


def write_cgb_eml(path: Path, cards: dict[str, list[Row]], cycle=CYCLE) -> Path:
    tables = "\n".join(
        f"<table>卡号：6200********{card}"
        + "".join(f"<tr><td>{' '.join(row)}</td></tr>" for row in rows)
        + "</table>"
        for card, rows in cards.items()
    )
    html = f"""<html><body>
<table><tr><td>账单周期:{cycle[0]}-{cycle[1]}</td></tr></table>
{tables}
</body></html>"""
    path.write_text(create_cgb_eml(html), encoding="utf-8")
    return path


def write_cgb_pdf(
    path: Path, cards: dict[str, list[Row]], cycle=CYCLE, rows_per_page: int = 30
) -> Path:
    """Draw a reissued CGB statement; its issuer name is only in the logo."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)

    def put(x: float, y: float, text: str, size: float = 9) -> None:
        page.insert_text((x, y), text, fontname="china-s", fontsize=size)

    put(34, 278, "信用卡账户信息", 14)
    put(46, 314, "账单周期", 10)
    put(171, 314, f"{cycle[0]} - {cycle[1]}", 10)
    put(390, 314, "个人消费额度", 10)
    put(28, 387, "注：通过发现精彩查询账单详情。")
    xs = [32, 86, 138, 300, 400, 440, 540]
    for x, label in zip(
        xs,
        [
            "交易日期",
            "入账日期",
            "交易摘要",
            "交易金额",
            "交易货币",
            "入账金额",
            "入账货币",
        ],
        strict=True,
    ):
        put(x, 448, label, 10)
    put(28, 465, "交易明细")
    y = 477.0
    written = 0
    for card, rows in cards.items():
        put(28, y, f"卡号：6200********{card}")
        y += 11
        for row in rows:
            if written and written % rows_per_page == 0:
                page = doc.new_page(width=595, height=842)
                y = 80.0
            for x, cell in zip(xs, row, strict=True):
                put(x, y, cell)
            y += 11
            written += 1
    put(290, 814, "1/1", 10)
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


class TestCGBReissue:
    """Tests for the reissued PDF statement."""

    def test_rows(self, tmp_path):
        txns = CGBCreditProvider().parse(write_cgb_pdf(tmp_path / "r.pdf", CARDS))

        assert [(t.date, t.amount, t.card_last4) for t in txns] == [
            (date(2030, 3, 2), Decimal("51.00"), "2468"),
            (date(2030, 3, 5), Decimal("97.00"), "2468"),
            (date(2030, 3, 9), Decimal("-930.00"), "2468"),
            (date(2030, 3, 12), Decimal("64.00"), "1357"),
            (date(2030, 3, 14), Decimal("-15.50"), "1357"),
        ]
        assert txns[2].description == "(还款)(云闪付)银联入账"
        assert txns[1].metadata[ORIGINAL_AMOUNT] == Decimal("13.50")
        assert txns[1].metadata[ORIGINAL_CURRENCY] == "USD"
        assert txns[0].metadata[STATEMENT_CYCLE] == (
            date(2030, 3, 1),
            date(2030, 3, 31),
        )

    def test_same_transactions_as_mail(self, tmp_path):
        pdf = write_cgb_pdf(tmp_path / "r.pdf", CARDS)
        eml = write_cgb_eml(tmp_path / "s.eml", CARDS)

        provider = CGBCreditProvider()
        assert shape(provider.parse(pdf)) == shape(provider.parse(eml))

    def test_rows_across_pages(self, tmp_path):
        pdf = write_cgb_pdf(tmp_path / "r.pdf", CARDS, rows_per_page=2)
        assert len(CGBCreditProvider().parse(pdf)) == 5

    def test_only_this_provider_claims_it(self, tmp_path):
        pdf = write_cgb_pdf(tmp_path / "statement.pdf", CARDS)
        assert [p for p, c in PROVIDERS.items() if c.can_handle(pdf)] == ["cgb_credit"]

    def test_claims_no_other_example_statement(self):
        for path in sorted(EXAMPLES.glob("*/*")):
            if path.parent.name != "cgb_credit":
                assert not CGBCreditProvider.can_handle(path), path


class TestCGBCoverage:
    def test_one_entry_per_card_for_the_printed_cycle(self, tmp_path):
        provider = CGBCreditProvider()
        for path, source in (
            (write_cgb_pdf(tmp_path / "r.pdf", CARDS), "reissue"),
            (write_cgb_eml(tmp_path / "s.eml", CARDS), "statement"),
        ):
            covs = provider.coverage(path, provider.parse(path))
            assert [
                (c.account, c.start, c.end, len(c.rows), c.source) for c in covs
            ] == [
                ("2468", date(2030, 3, 1), date(2030, 3, 31), 3, source),
                ("1357", date(2030, 3, 1), date(2030, 3, 31), 2, source),
            ]

    @pytest.mark.parametrize("reverse", [False, True])
    def test_mail_outranks_its_reissue(self, tmp_path, reverse):
        provider = CGBCreditProvider()
        paths = [
            write_cgb_pdf(tmp_path / "r.pdf", CARDS),
            write_cgb_eml(tmp_path / "s.eml", CARDS),
            write_cgb_pdf(
                tmp_path / "next.pdf",
                {"2468": CARDS["2468"][:1]},
                ("2030/04/01", "2030/04/30"),
            ),
        ]
        if reverse:
            paths.reverse()
        ranks = {kind: i for i, kind in enumerate(provider.coverage_priority)}
        parsed = {path: provider.parse(path) for path in paths}
        entries = [
            CoverageEntry("cgb_credit", path, cov, ranks[cov.source])
            for path in paths
            for cov in provider.coverage(path, parsed[path])
        ]
        kept = select(entries).keep([t for rows in parsed.values() for t in rows])
        by_file = {
            path.name: sum(t.source_file == path for t in kept) for path in paths
        }
        assert by_file == {"s.eml": 5, "r.pdf": 0, "next.pdf": 1}
