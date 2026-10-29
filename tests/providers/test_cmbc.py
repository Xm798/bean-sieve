"""Tests for China Minsheng Bank (CMBC) credit card statement provider."""

import base64
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.providers import get_provider
from bean_sieve.providers.banks.credit.cmbc import CMBCCreditProvider


def create_cmbc_eml(html_content: str) -> str:
    """Create an EML file content with the given HTML (base64 encoded)."""
    encoded = base64.b64encode(html_content.encode("utf-8")).decode("ascii")
    return f"""From: creditcard@cmbc.com.cn
Subject: =?utf-8?B?5rCR55Sf5L+h55So5Y2h55S15a2Q5a+56LSm5Y2V?=
To: test@example.com
MIME-Version: 1.0
Content-Type: text/html; charset="utf-8"
Content-Transfer-Encoding: base64

{encoded}
"""


def create_cmbc_html_with_transactions(
    transactions: list[dict], statement_date: str = "2025/12/10"
) -> str:
    """Create CMBC statement HTML with transaction rows.

    Each transaction dict should have:
    - trans_date: MM/DD format
    - post_date: MM/DD format
    - description: str
    - amount: str (e.g., "100.00" or "-100.00")
    - card_last4: str

    statement_date (账单日, YYYY/MM/DD) is what CMBC derives the cycle from.
    """
    year, month, _ = statement_date.split("/")
    rows = []
    for txn in transactions:
        full_text = f"{txn['trans_date']}{txn['post_date']}{txn['description']}{txn['amount'].replace(',', '').lstrip('-')}{txn['card_last4']}"
        row = f"""<span id='fixBand1'>
            <span id='fixBand8'>{full_text}</span>
            <span id='fixBand82'>{txn["description"]}</span>
            <span id='fixBand22'>{txn["amount"]}</span>
            <span id='fixBand9'>{txn["card_last4"]}</span>
        </span>"""
        rows.append(row)

    return f"""<!DOCTYPE HTML>
<html>
<head><meta charset="UTF-8"></head>
<body>
<div>{year}年{int(month)}月对账单</div>
<div>Statement Date</div><div>{statement_date}</div>
<span id='loopBand3'>
    <span id='loopBand1'>
        {"".join(rows)}
    </span>
</span>
</body>
</html>"""


@pytest.fixture
def cmbc_html_content():
    """Sample CMBC statement HTML content."""
    transactions = [
        {
            "trans_date": "11/21",
            "post_date": "11/21",
            "description": "银联入账还款",
            "amount": "-1,182.17",
            "card_last4": "3579",
        },
        {
            "trans_date": "12/04",
            "post_date": "12/04",
            "description": "支付宝-沃尔玛",
            "amount": "60.90",
            "card_last4": "3579",
        },
        {
            "trans_date": "12/04",
            "post_date": "12/04",
            "description": "支付宝退款",
            "amount": "-60.90",
            "card_last4": "3579",
        },
    ]
    return create_cmbc_html_with_transactions(transactions)


@pytest.fixture
def cmbc_eml_file(tmp_path, cmbc_html_content):
    """Create a temporary CMBC EML file."""
    file_path = tmp_path / "民生信用卡2025年12月电子对账单.eml"
    eml_content = create_cmbc_eml(cmbc_html_content)
    file_path.write_text(eml_content, encoding="utf-8")
    return file_path


class TestCMBCCreditProvider:
    """Tests for CMBCCreditProvider."""

    def test_provider_registration(self):
        """Test that CMBC provider is properly registered."""
        provider = get_provider("cmbc_credit")
        assert isinstance(provider, CMBCCreditProvider)
        assert provider.provider_id == "cmbc_credit"
        assert provider.provider_name == "民生银行信用卡"
        assert ".eml" in provider.supported_formats

    def test_can_handle(self):
        """Test file format detection."""
        assert CMBCCreditProvider.can_handle(Path("民生信用卡2025年12月电子对账单.eml"))
        assert CMBCCreditProvider.can_handle(Path("民生银行信用卡账单.eml"))
        assert not CMBCCreditProvider.can_handle(Path("cmbc_statement.csv"))
        assert not CMBCCreditProvider.can_handle(Path("statement.eml"))

    def test_parse_transactions(self, cmbc_eml_file):
        """Test parsing transactions from EML file."""
        provider = CMBCCreditProvider()
        transactions = provider.parse(cmbc_eml_file)

        assert len(transactions) == 3

        # Check repayment transaction
        txn1 = transactions[0]
        assert txn1.date == date(2025, 11, 21)
        assert txn1.amount == Decimal("-1182.17")
        assert txn1.currency == "CNY"
        assert txn1.card_last4 == "3579"
        assert "还款" in txn1.description
        assert txn1.provider == "cmbc_credit"
        assert txn1.is_income

        # Check expense
        txn2 = transactions[1]
        assert txn2.date == date(2025, 12, 4)
        assert txn2.amount == Decimal("60.90")
        assert "支付宝" in txn2.description
        assert txn2.is_expense

        # Check refund
        txn3 = transactions[2]
        assert txn3.amount == Decimal("-60.90")
        assert txn3.is_income

    def test_statement_period_extraction(self, cmbc_eml_file):
        """Test that statement period is properly extracted."""
        provider = CMBCCreditProvider()
        transactions = provider.parse(cmbc_eml_file)

        assert len(transactions) > 0
        txn = transactions[0]
        # CMBC statement period: prev statement day + 1 to current statement day
        assert txn.statement_period == (date(2025, 11, 11), date(2025, 12, 10))


class TestCMBCDateParsing:
    """Tests for CMBC date parsing edge cases."""

    def test_cross_year_january_in_december_statement(self, tmp_path):
        """Test January transactions in December statement."""
        transactions = [
            {
                "trans_date": "01/02",
                "post_date": "01/02",
                "description": "新年消费",
                "amount": "100.00",
                "card_last4": "3579",
            },
        ]
        html = create_cmbc_html_with_transactions(transactions)
        file_path = tmp_path / "民生信用卡2025年12月电子对账单.eml"
        file_path.write_text(create_cmbc_eml(html), encoding="utf-8")

        provider = CMBCCreditProvider()
        txns = provider.parse(file_path)

        assert len(txns) == 1
        # January in December statement should be next year
        assert txns[0].date == date(2026, 1, 2)

    def test_november_transactions_in_december_statement(self, tmp_path):
        """Test November transactions in December statement."""
        transactions = [
            {
                "trans_date": "11/28",
                "post_date": "11/29",
                "description": "消费",
                "amount": "100.00",
                "card_last4": "3579",
            },
        ]
        html = create_cmbc_html_with_transactions(transactions)
        file_path = tmp_path / "民生信用卡2025年12月电子对账单.eml"
        file_path.write_text(create_cmbc_eml(html), encoding="utf-8")

        provider = CMBCCreditProvider()
        txns = provider.parse(file_path)

        assert len(txns) == 1
        assert txns[0].date == date(2025, 11, 28)


class TestCMBCAmountParsing:
    """Tests for CMBC amount parsing edge cases."""

    def test_amount_with_thousands_separator(self, tmp_path):
        """Test parsing amounts with thousands separator."""
        transactions = [
            {
                "trans_date": "12/10",
                "post_date": "12/11",
                "description": "大额消费",
                "amount": "12,345.67",
                "card_last4": "3579",
            },
        ]
        html = create_cmbc_html_with_transactions(transactions)
        file_path = tmp_path / "民生信用卡2025年12月电子对账单.eml"
        file_path.write_text(create_cmbc_eml(html), encoding="utf-8")

        provider = CMBCCreditProvider()
        txns = provider.parse(file_path)

        assert len(txns) == 1
        assert txns[0].amount == Decimal("12345.67")

    def test_negative_amount(self, tmp_path):
        """Test parsing negative amounts (refunds/repayments)."""
        transactions = [
            {
                "trans_date": "12/10",
                "post_date": "12/11",
                "description": "退款",
                "amount": "-999.99",
                "card_last4": "3579",
            },
        ]
        html = create_cmbc_html_with_transactions(transactions)
        file_path = tmp_path / "民生信用卡2025年12月电子对账单.eml"
        file_path.write_text(create_cmbc_eml(html), encoding="utf-8")

        provider = CMBCCreditProvider()
        txns = provider.parse(file_path)

        assert len(txns) == 1
        assert txns[0].amount == Decimal("-999.99")
        assert txns[0].is_income


class TestCMBCEmptyStatement:
    """Tests for empty statement handling."""

    def test_empty_statement(self, tmp_path):
        """Test handling of statement with no transactions."""
        html = """<!DOCTYPE HTML>
<html>
<head><meta charset="UTF-8"></head>
<body>
<div>2025年12月对账单</div>
<span id='loopBand3'>
</span>
</body>
</html>"""
        file_path = tmp_path / "民生信用卡2025年12月电子对账单.eml"
        file_path.write_text(create_cmbc_eml(html), encoding="utf-8")

        provider = CMBCCreditProvider()
        transactions = provider.parse(file_path)

        assert transactions == []

    def test_no_loop_band(self, tmp_path):
        """Test handling of statement without loopBand3."""
        html = """<!DOCTYPE HTML>
<html>
<head><meta charset="UTF-8"></head>
<body>
<div>2025年12月对账单</div>
</body>
</html>"""
        file_path = tmp_path / "民生信用卡2025年12月电子对账单.eml"
        file_path.write_text(create_cmbc_eml(html), encoding="utf-8")

        provider = CMBCCreditProvider()
        transactions = provider.parse(file_path)

        assert transactions == []


class TestCMBCStatementPeriod:
    """Tests for per-card statement period assignment."""

    def test_cycle_widens_only_for_the_card_dated_outside_it(self, tmp_path):
        """Test that an out-of-cycle row widens its own card's period."""
        transactions = [
            {
                "trans_date": "02/27",
                "post_date": "03/02",
                "description": "merchant-a",
                "amount": "10.00",
                "card_last4": "3579",
            },
            {
                "trans_date": "03/10",
                "post_date": "03/10",
                "description": "merchant-b",
                "amount": "20.00",
                "card_last4": "6626",
            },
        ]
        html = create_cmbc_html_with_transactions(
            transactions, statement_date="2030/03/28"
        )
        file_path = tmp_path / "民生信用卡电子对账单.eml"
        file_path.write_text(create_cmbc_eml(html), encoding="utf-8")

        txns = CMBCCreditProvider().parse(file_path)

        periods = {txn.card_last4: txn.statement_period for txn in txns}
        assert periods["3579"] == (date(2030, 2, 27), date(2030, 3, 28))
        assert periods["6626"] == (date(2030, 3, 1), date(2030, 3, 28))


# Section loop id -> (row, date wrapper, summary, amount, card) span ids.
_SECTIONED_IDS = {
    "loopBand7": ("fixBand39", "fixBand40", "fixBand65", "fixBand64", "fixBand41"),
    "loopBand5": ("fixBand15", "fixBand16", "fixBand19", "fixBand18", "fixBand17"),
    "loopBand9": ("fixBand77", "fixBand78", "fixBand81", "fixBand80", "fixBand79"),
}


def create_sectioned_html(sections: dict[str, list[tuple[str, ...]]]) -> str:
    """Build a sectioned statement whose non-purchase sections hold the rows.

    Each row is (trans_date, post_date, summary, amount, card_last4).
    """
    blocks = []
    for loop_id, rows in sections.items():
        row_id, wrap_id, desc_id, amt_id, card_id = _SECTIONED_IDS[loop_id]
        spans = "".join(
            f"""<span id='{row_id}'><span id='{wrap_id}'>
                <font>{t}</font><font>{p}</font>
                <span id='{desc_id}'><font>{d}</font></span>
                <span id='{amt_id}'><font>{a}</font></span>
                <span id='{card_id}'><font>{c}</font></span>
            </span></span>"""
            for t, p, d, a, c in rows
        )
        blocks.append(f"<span id='{loop_id}'>{spans}</span>")
    return f"""<!DOCTYPE HTML>
<html>
<head><meta charset="UTF-8"></head>
<body>
<div>Statement Date</div><div>2030/03/20</div>
<span id='loopBand3'>{"".join(blocks)}</span>
</body>
</html>"""


def parse_html(tmp_path: Path, html: str) -> list:
    file_path = tmp_path / "民生信用卡2030年03月电子对账单.eml"
    file_path.write_text(create_cmbc_eml(html), encoding="utf-8")
    return CMBCCreditProvider().parse(file_path)


class TestCMBCSectionedTemplate:
    """Tests for the non-purchase sections of the sectioned template."""

    def test_every_refund_and_payment_row_is_parsed(self, tmp_path):
        sections = {
            "loopBand7": [
                ("03/04", "03/05", "退货-payee-a", "-37.00", "1111"),
                ("03/08", "03/08", "退货-payee-b", "-43.00", "2222"),
            ],
            "loopBand5": [
                ("03/10", "03/10", "还款-a", "-500.00", "1111"),
                ("03/12", "03/13", "还款-b", "-700.00", "1111"),
            ],
        }

        txns = parse_html(tmp_path, create_sectioned_html(sections))

        assert [(t.description, t.amount, t.card_last4) for t in txns] == [
            ("退货-payee-a", Decimal("-37.00"), "1111"),
            ("退货-payee-b", Decimal("-43.00"), "2222"),
            ("还款-a", Decimal("-500.00"), "1111"),
            ("还款-b", Decimal("-700.00"), "1111"),
        ]
        assert [t.date for t in txns] == [
            date(2030, 3, 4),
            date(2030, 3, 8),
            date(2030, 3, 10),
            date(2030, 3, 12),
        ]
        assert [t.source_line for t in txns] == [1, 2, 3, 4]


def create_single_table_html(rows: list[tuple[str, ...]]) -> str:
    """Build a single-table statement.

    Each row is (trans_date, post_date, summary, signed amount, card_last4). The
    table sits under fixBand1 > loopBand1 > loopBand2 > loopBand3, and a
    fixBand9 outside loopBand3 stands for the summary block's same-id span.
    """
    spans = "".join(
        f"""<span id='fixBand9'>
            <font>{t}</font><font>{p}</font>
            <span id='fixBand22'><font>{d}</font></span>
            <span id='fixBand8'><font>{a}</font></span>
            <span id='fixBand2'><font>{c}</font></span>
        </span>"""
        for t, p, d, a, c in rows
    )
    return f"""<!DOCTYPE HTML>
<html>
<head><meta charset="UTF-8"></head>
<body>
<div>Statement Date</div><div>2030/03/20</div>
<span id='fixBand9'><font>01/01</font><font>01/01</font></span>
<span id='fixBand1'><span id='reportPanel1'>
<span id='loopBand1'><span id='loopBand2'><span id='loopBand3'>{spans}</span></span></span>
</span></span>
</body>
</html>"""


class TestCMBCSingleTableTemplate:
    """Tests for the single-table template of earlier statements."""

    def test_rows_of_the_single_table_are_parsed(self, tmp_path):
        html = create_single_table_html(
            [
                ("02/25", "02/26", "payee-a", "37.00", "1111"),
                ("03/02", "03/02", "退货-payee-a", "-37.00", "1111"),
                ("03/05", "03/05", "还款", "-800.00", "2222"),
            ]
        )
        txns = parse_html(tmp_path, html)

        assert [(t.date, t.description, t.amount, t.card_last4) for t in txns] == [
            (date(2030, 2, 25), "payee-a", Decimal("37.00"), "1111"),
            (date(2030, 3, 2), "退货-payee-a", Decimal("-37.00"), "1111"),
            (date(2030, 3, 5), "还款", Decimal("-800.00"), "2222"),
        ]
        assert txns[0].metadata == {"posting_date": "2030-02-26"}
        assert txns[1].metadata == {}
        assert [t.source_line for t in txns] == [1, 2, 3]
        assert txns[0].statement_period == (date(2030, 2, 21), date(2030, 3, 20))


class TestCMBCPostingDate:
    """Refund and payment rows carry the posting date like purchase rows."""

    def test_refund_row_with_later_posting_date(self, tmp_path):
        html = create_sectioned_html(
            {"loopBand7": [("03/04", "03/06", "退货-payee-c", "-15.00", "1111")]}
        )
        (txn,) = parse_html(tmp_path, html)

        assert txn.metadata == {"posting_date": "2030-03-06"}

    def test_reward_section_row_is_parsed(self, tmp_path):
        html = create_sectioned_html(
            {"loopBand9": [("03/09", "03/09", "奖励返还-a", "-5.00", "1111")]}
        )
        (txn,) = parse_html(tmp_path, html)

        assert (txn.date, txn.description, txn.amount) == (
            date(2030, 3, 9),
            "奖励返还-a",
            Decimal("-5.00"),
        )


def test_both_templates_produce_the_same_transactions(tmp_path):
    rows = [
        ("03/04", "03/05", "退货-payee-d", "-12.00", "1111"),
        ("03/07", "03/07", "还款-d", "-300.00", "2222"),
    ]
    single = parse_html(tmp_path, create_single_table_html(rows))
    sectioned = parse_html(tmp_path, create_sectioned_html({"loopBand5": rows}))

    assert [t.model_dump() for t in single] == [t.model_dump() for t in sectioned]


def test_unknown_layout_with_dated_rows_logs_a_warning(tmp_path, caplog):
    html = """<!DOCTYPE HTML>
<html><head><meta charset="UTF-8"></head><body>
<div>Statement Date</div><div>2030/03/20</div>
<span id='loopBand3'><span id='rowX'>
    <font>03/04</font><font>03/05</font><span id='cellX'>payee-x</span>
</span></span>
</body></html>"""

    with caplog.at_level("WARNING", logger="bean_sieve.providers.banks.credit.cmbc"):
        assert parse_html(tmp_path, html) == []

    assert "民生信用卡2030年03月电子对账单.eml" in caplog.text
