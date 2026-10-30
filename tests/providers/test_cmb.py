"""Tests for China Merchants Bank (CMB) credit card statement provider."""

import base64
from datetime import date
from decimal import Decimal
from email.header import Header
from pathlib import Path

import pytest

from bean_sieve.providers import get_provider
from bean_sieve.providers.banks.credit.cmb import CMBCreditProvider


def create_cmb_eml(html_content: str, subject: str = "招商银行信用卡电子账单") -> str:
    """Create an EML file content with the given HTML (base64 encoded)."""
    encoded = base64.b64encode(html_content.encode("utf-8")).decode("ascii")
    return f"""From: creditcard@cmbchina.com
Subject: {Header(subject, "utf-8").encode()}
To: test@example.com
MIME-Version: 1.0
Content-Type: text/html; charset="utf-8"
Content-Transfer-Encoding: base64

{encoded}
"""


_TEXT_HEADER = (
    "",
    "交易日",
    "记账日",
    "交易摘要",
    "人民币金额",
    "卡号末四位",
    "交易地金额",
    "交易地",
)


def _cells(values) -> str:
    return "".join(f"<td>{v}</td>" for v in values)


def create_cmb_html_with_transactions(
    transactions: list[dict],
    statement_month: str = "2025年12月",
    *,
    template: str = "text",
    usd_transactions: list[dict] | None = None,
) -> str:
    """Create CMB statement HTML with transaction rows.

    Each transaction dict should have:
    - trans_date: MMDD or empty string
    - post_date: MMDD
    - description: str
    - card_last4: str
    - amount: str (e.g., "102.10" or "-102.10")
    - original_amount: optional 交易地金额, defaults to amount
    - country: optional 交易地, defaults to empty

    template "text" is the current mail: a row of column labels over one
    bgTable. "image" is the older mail: the header is an image, each row a
    table of its own with 交易地 before 交易地金额, and the month only shows
    as the billing cycle, which statement_month places from the 1st to the
    28th. usd_transactions adds a 美元账户 section to a "text" mail.
    """

    def fields(txn: dict) -> tuple:
        return (
            txn["trans_date"],
            txn["post_date"],
            txn["description"],
            txn["amount"],
            txn["card_last4"],
            txn.get("original_amount", txn["amount"]),
            txn.get("country", ""),
        )

    year, month = (int(x) for x in statement_month.rstrip("月").split("年"))
    if template == "image":
        rows = "".join(
            f"""<span id="fixBand15"><table><tr>
            {_cells(["", t, p, d, f"¥ {a}", c, n, o])}
            </tr></table></span><span id="fixBand2"><img src="line.jpg"></span>"""
            for t, p, d, a, c, o, n in map(fields, transactions)
        )
        return f"""<html><body>
<div>以下是您的招商银行信用卡账单</div>
<table><tr><td>{year}/{month:02d}/01-{year}/{month:02d}/28</td></tr></table>
<span id="loopBand2">
<span id="loopHeader2"><span id="fixBand14"><img src="header.jpg"></span></span>
{rows}
</span>
<div>您本期账单所查询到的积分值截至{year - 1}年01月底，敬请留意。</div>
</body></html>"""

    def section(header: tuple, txns: list[dict], symbol: str) -> str:
        body = "".join(
            f"""<tr><td><table><tr>
            {_cells(["", t, p, d, f"{symbol} {a}", c, o, n])}
            </tr></table></td></tr>"""
            for t, p, d, a, c, o, n in map(fields, txns)
        )
        return f"""<span id="loopBand2">
<span id="loopHeader2"><span id="fixBand14"><table><tr>{_cells(header)}</tr></table></span></span>
<span id="fixBand15"><table class="bgTable">{body}</table></span>
</span>"""

    sections = section(_TEXT_HEADER, transactions, "¥")
    if usd_transactions is not None:
        usd_header = tuple("美元金额" if h == "人民币金额" else h for h in _TEXT_HEADER)
        sections += section(usd_header, usd_transactions, "$")
    return f"""<!DOCTYPE HTML>
<html>
<head><meta charset="UTF-8"></head>
<body>
<div>您{statement_month}信用卡账单已出</div>
{sections}
</body>
</html>"""


@pytest.fixture
def cmb_html_content():
    """Sample CMB statement HTML content."""
    transactions = [
        # Repayment (no trans_date)
        {
            "trans_date": "",
            "post_date": "1204",
            "description": "银联在线网络还款",
            "card_last4": "2468",
            "amount": "-2,993.80",
        },
        # Expense with trans_date and post_date
        {
            "trans_date": "1210",
            "post_date": "1211",
            "description": "支付宝-测试商户",
            "card_last4": "2468",
            "amount": "2,993.60",
        },
        # Expense with different card
        {
            "trans_date": "1217",
            "post_date": "1218",
            "description": "餐饮消费",
            "card_last4": "1357",
            "amount": "102.30",
        },
        # Refund
        {
            "trans_date": "1128",
            "post_date": "1129",
            "description": "优惠退款",
            "card_last4": "1357",
            "amount": "-11.10",
        },
    ]
    return create_cmb_html_with_transactions(transactions)


@pytest.fixture
def cmb_eml_file(tmp_path, cmb_html_content):
    """Create a temporary CMB EML file."""
    file_path = tmp_path / "招商银行信用卡电子账单.eml"
    eml_content = create_cmb_eml(cmb_html_content)
    file_path.write_text(eml_content, encoding="utf-8")
    return file_path


class TestCMBCreditProvider:
    """Tests for CMBCreditProvider."""

    def test_provider_registration(self):
        """Test that CMB provider is properly registered."""
        provider = get_provider("cmb_credit")
        assert isinstance(provider, CMBCreditProvider)
        assert provider.provider_id == "cmb_credit"
        assert provider.provider_name == "招商银行信用卡"
        assert ".eml" in provider.supported_formats

    def test_can_handle(self):
        """Test file format detection."""
        assert CMBCreditProvider.can_handle(Path("招商银行信用卡电子账单.eml"))
        assert CMBCreditProvider.can_handle(Path("招行信用卡账单.eml"))
        assert not CMBCreditProvider.can_handle(Path("cmb_statement.csv"))
        assert not CMBCreditProvider.can_handle(Path("statement.eml"))

    def test_parse_transactions(self, cmb_eml_file):
        """Test parsing transactions from EML file."""
        provider = CMBCreditProvider()
        transactions = provider.parse(cmb_eml_file)

        assert len(transactions) == 4

        # Check repayment transaction (no trans_date, uses post_date)
        txn1 = transactions[0]
        assert txn1.date == date(2025, 12, 4)
        assert txn1.amount == Decimal("-2993.80")
        assert txn1.currency == "CNY"
        assert txn1.card_last4 == "2468"
        assert "还款" in txn1.description
        assert txn1.provider == "cmb_credit"
        assert txn1.is_income

        # Check expense with trans_date
        txn2 = transactions[1]
        assert txn2.date == date(2025, 12, 10)
        assert txn2.amount == Decimal("2993.60")
        assert "支付宝" in txn2.description
        assert txn2.is_expense

        # Check different card
        txn3 = transactions[2]
        assert txn3.card_last4 == "1357"
        assert txn3.date == date(2025, 12, 17)

        # Check refund
        txn4 = transactions[3]
        assert txn4.amount == Decimal("-11.10")
        assert txn4.is_income

    def test_statement_period_extraction(self, cmb_eml_file):
        """Test that statement period is properly extracted."""
        provider = CMBCreditProvider()
        transactions = provider.parse(cmb_eml_file)

        assert len(transactions) > 0
        txn = transactions[0]
        # CMB statement covers full month
        assert txn.statement_period == (date(2025, 12, 1), date(2025, 12, 31))


class TestCMBDateParsing:
    """Tests for CMB date parsing edge cases."""

    def test_date_after_the_statement_month_is_from_the_previous_year(self, tmp_path):
        """An instalment row keeps the purchase date, months before the cycle."""
        transactions = [
            {
                "trans_date": "0115",
                "post_date": "1210",
                "description": "消费分期-store-f",
                "card_last4": "4321",
                "amount": "31.40",
            },
        ]
        html = create_cmb_html_with_transactions(transactions)
        file_path = tmp_path / "招商银行信用卡电子账单.eml"
        file_path.write_text(create_cmb_eml(html), encoding="utf-8")

        provider = CMBCreditProvider()
        txns = provider.parse(file_path)

        assert len(txns) == 1
        assert txns[0].date == date(2025, 1, 15)

    def test_november_transactions_in_december_statement(self, tmp_path):
        """Test November transactions in December statement."""
        transactions = [
            {
                "trans_date": "1128",
                "post_date": "1129",
                "description": "消费",
                "card_last4": "2468",
                "amount": "102.10",
            },
        ]
        html = create_cmb_html_with_transactions(transactions)
        file_path = tmp_path / "招商银行信用卡电子账单.eml"
        file_path.write_text(create_cmb_eml(html), encoding="utf-8")

        provider = CMBCreditProvider()
        txns = provider.parse(file_path)

        assert len(txns) == 1
        assert txns[0].date == date(2025, 11, 28)

    def test_repayment_uses_post_date(self, tmp_path):
        """Test that repayments without trans_date use post_date."""
        transactions = [
            {
                "trans_date": "",  # Empty trans_date
                "post_date": "1215",
                "description": "支付宝还款",
                "card_last4": "2468",
                "amount": "-4900.00",
            },
        ]
        html = create_cmb_html_with_transactions(transactions)
        file_path = tmp_path / "招商银行信用卡电子账单.eml"
        file_path.write_text(create_cmb_eml(html), encoding="utf-8")

        provider = CMBCreditProvider()
        txns = provider.parse(file_path)

        assert len(txns) == 1
        assert txns[0].date == date(2025, 12, 15)


class TestCMBAmountParsing:
    """Tests for CMB amount parsing edge cases."""

    def test_amount_with_thousands_separator(self, tmp_path):
        """Test parsing amounts with thousands separator."""
        transactions = [
            {
                "trans_date": "1210",
                "post_date": "1211",
                "description": "大额消费",
                "card_last4": "2468",
                "amount": "12,345.67",
            },
        ]
        html = create_cmb_html_with_transactions(transactions)
        file_path = tmp_path / "招商银行信用卡电子账单.eml"
        file_path.write_text(create_cmb_eml(html), encoding="utf-8")

        provider = CMBCreditProvider()
        txns = provider.parse(file_path)

        assert len(txns) == 1
        assert txns[0].amount == Decimal("12345.67")

    def test_fullwidth_dollar_sign(self, tmp_path):
        usd = {
            "trans_date": "1210",
            "post_date": "1211",
            "description": "payee-usd",
            "card_last4": "0001",
            "amount": "11.30",
        }
        html = create_cmb_html_with_transactions([], usd_transactions=[usd]).replace(
            "$ 11.30", "＄11.30"
        )
        file_path = tmp_path / "招商银行信用卡电子账单.eml"
        file_path.write_text(create_cmb_eml(html), encoding="utf-8")

        (txn,) = CMBCreditProvider().parse(file_path)

        assert (txn.amount, txn.currency) == (Decimal("11.30"), "USD")

    def test_foreign_transaction_takes_rmb_amount(self, tmp_path):
        """人民币金额 is the amount; 交易地金额 and 交易地 are kept as metadata."""
        transactions = [
            {
                "trans_date": "1210",
                "post_date": "1211",
                "description": "OVERSEAS MERCHANT",
                "card_last4": "0001",
                "amount": "39.10",
                "original_amount": "39.60",
                "country": "HK",
            },
            {
                "trans_date": "1211",
                "post_date": "1212",
                "description": "境内商户",
                "card_last4": "0001",
                "amount": "46.10",
                "country": "CN",
            },
        ]
        html = create_cmb_html_with_transactions(transactions)
        file_path = tmp_path / "招商银行信用卡电子账单.eml"
        file_path.write_text(create_cmb_eml(html), encoding="utf-8")

        txns = CMBCreditProvider().parse(file_path)

        assert [t.amount for t in txns] == [Decimal("39.10"), Decimal("46.10")]
        assert txns[0].currency == "CNY"
        assert txns[0].metadata["original_amount"] == Decimal("39.60")
        assert txns[0].metadata["country"] == "HK"
        assert "original_currency" not in txns[0].metadata
        assert "original_amount" not in txns[1].metadata
        assert "country" not in txns[1].metadata

    def test_negative_amount(self, tmp_path):
        """Test parsing negative amounts (refunds/repayments)."""
        transactions = [
            {
                "trans_date": "1210",
                "post_date": "1211",
                "description": "退款",
                "card_last4": "2468",
                "amount": "-999.99",
            },
        ]
        html = create_cmb_html_with_transactions(transactions)
        file_path = tmp_path / "招商银行信用卡电子账单.eml"
        file_path.write_text(create_cmb_eml(html), encoding="utf-8")

        provider = CMBCreditProvider()
        txns = provider.parse(file_path)

        assert len(txns) == 1
        assert txns[0].amount == Decimal("-999.99")
        assert txns[0].is_income


class TestCMBMultipleCards:
    """Tests for CMB multiple card support."""

    def test_multiple_cards_in_statement(self, tmp_path):
        """Test that transactions from different cards are parsed correctly."""
        transactions = [
            {
                "trans_date": "1210",
                "post_date": "1211",
                "description": "消费1",
                "card_last4": "2468",
                "amount": "102.10",
            },
            {
                "trans_date": "1212",
                "post_date": "1213",
                "description": "消费2",
                "card_last4": "1357",
                "amount": "200.10",
            },
            {
                "trans_date": "1214",
                "post_date": "1215",
                "description": "消费3",
                "card_last4": "3579",
                "amount": "298.80",
            },
        ]
        html = create_cmb_html_with_transactions(transactions)
        file_path = tmp_path / "招商银行信用卡电子账单.eml"
        file_path.write_text(create_cmb_eml(html), encoding="utf-8")

        provider = CMBCreditProvider()
        txns = provider.parse(file_path)

        assert len(txns) == 3

        # Verify different cards
        cards = {txn.card_last4 for txn in txns}
        assert cards == {"2468", "1357", "3579"}


class TestCMBEmptyStatement:
    """Tests for empty statement handling."""

    def test_empty_statement(self, tmp_path):
        """Test handling of statement with no transactions."""
        html = create_cmb_html_with_transactions([])
        file_path = tmp_path / "招商银行信用卡电子账单.eml"
        file_path.write_text(create_cmb_eml(html), encoding="utf-8")

        provider = CMBCreditProvider()
        transactions = provider.parse(file_path)

        assert transactions == []


class TestCMBStatementPeriod:
    """Tests for per-card statement period assignment."""

    def test_cycle_bounds_a_card_dated_outside_it(self, tmp_path):
        """Test that an out-of-cycle row leaves its card's period at the cycle."""
        transactions = [
            {
                "trans_date": "0227",
                "post_date": "0302",
                "description": "merchant-a",
                "card_last4": "2468",
                "amount": "11.30",
            },
            {
                "trans_date": "0310",
                "post_date": "0310",
                "description": "merchant-b",
                "card_last4": "1357",
                "amount": "18.30",
            },
        ]
        html = create_cmb_html_with_transactions(
            transactions, statement_month="2030年3月"
        )
        file_path = tmp_path / "招商银行信用卡电子账单.eml"
        file_path.write_text(create_cmb_eml(html), encoding="utf-8")

        txns = CMBCreditProvider().parse(file_path)

        periods = {txn.card_last4: txn.statement_period for txn in txns}
        assert periods["2468"] == (date(2030, 3, 1), date(2030, 3, 31))
        assert periods["1357"] == (date(2030, 3, 1), date(2030, 3, 31))


_TEMPLATE_ROWS = [
    {
        "trans_date": "",
        "post_date": "0305",
        "description": "银联在线网络还款",
        "card_last4": "4321",
        "amount": "-652.40",
    },
    {
        "trans_date": "0225",
        "post_date": "0306",
        "description": "消费分期-payee-a 002-006",
        "card_last4": "4321",
        "amount": "45.50",
    },
    {
        "trans_date": "0310",
        "post_date": "0311",
        "description": "payee-b",
        "card_last4": "8765",
        "amount": "31.60",
        "original_amount": "38.20",
        "country": "HK",
    },
    {
        "trans_date": "0314",
        "post_date": "0315",
        "description": "退款-payee-c",
        "card_last4": "8765",
        "amount": "-13.40",
        "country": "CN",
    },
]


class TestCMBMailTemplates:
    """Tests for the two mail layouts of the transaction table."""

    def _parse(self, tmp_path: Path, html: str, name: str = "statement.eml"):
        path = tmp_path / name
        path.write_text(create_cmb_eml(html), encoding="utf-8")
        provider = CMBCreditProvider()
        txns = provider.parse(path)
        return txns, provider.coverage(path, txns)

    @staticmethod
    def _shape(txns) -> list[tuple]:
        return [
            (
                t.date,
                t.amount,
                t.currency,
                t.description,
                t.card_last4,
                t.metadata,
                t.statement_period,
                t.source_line,
            )
            for t in txns
        ]

    def test_both_templates_yield_the_same_transactions(self, tmp_path):
        text, _ = self._parse(
            tmp_path,
            create_cmb_html_with_transactions(_TEMPLATE_ROWS, "2030年03月"),
            "text.eml",
        )
        image, _ = self._parse(
            tmp_path,
            create_cmb_html_with_transactions(
                _TEMPLATE_ROWS, "2030年03月", template="image"
            ),
            "image.eml",
        )

        assert len(text) == 4
        assert self._shape(image) == self._shape(text)
        assert text[2].metadata["original_amount"] == Decimal("38.20")
        assert text[2].metadata["country"] == "HK"

    def test_image_header_mail_is_itemized_for_the_cycle_month(self, tmp_path):
        txns, (cov,) = self._parse(
            tmp_path,
            create_cmb_html_with_transactions(
                _TEMPLATE_ROWS, "2030年03月", template="image"
            ),
        )

        assert cov.itemized
        assert (cov.start, cov.end) == (date(2030, 3, 1), date(2030, 3, 31))
        assert cov.rows == txns

    def test_amount_with_a_gap_after_the_sign(self, tmp_path):
        html = create_cmb_html_with_transactions(
            [{**_TEMPLATE_ROWS[0], "amount": "- 652.40"}],
            "2030年03月",
            template="image",
        ).replace("¥", "￥")

        (txn,), _ = self._parse(tmp_path, html)

        assert txn.amount == Decimal("-652.40")

    def test_usd_section_is_a_usd_account(self, tmp_path):
        html = create_cmb_html_with_transactions(
            _TEMPLATE_ROWS[:1],
            "2030年03月",
            usd_transactions=[
                {**_TEMPLATE_ROWS[2], "amount": "4.70", "original_amount": "5.30"},
                {**_TEMPLATE_ROWS[3], "amount": "-4.70"},
            ],
        )

        txns, covs = self._parse(tmp_path, html)

        assert [(t.currency, t.amount, t.card_last4) for t in txns] == [
            ("CNY", Decimal("-652.40"), "4321"),
            ("USD", Decimal("4.70"), "8765"),
            ("USD", Decimal("-4.70"), "8765"),
        ]
        assert txns[1].metadata == {
            "posting_date": "2030-03-11",
            "original_amount": Decimal("5.30"),
            "country": "HK",
        }
        assert [t.source_line for t in txns] == [1, 2, 3]
        assert [(c.account, c.source, c.itemized) for c in covs] == [
            ("CNY", "statement", True),
            ("USD", "statement", True),
        ]
        assert covs[1].rows == txns[1:]

    def test_header_without_rows_is_an_itemized_quiet_month(self, tmp_path):
        txns, (cov,) = self._parse(
            tmp_path, create_cmb_html_with_transactions([], "2030年03月")
        )

        assert txns == []
        assert cov.itemized

    def test_unrecognised_layout_warns_and_is_not_itemized(self, tmp_path, caplog):
        html = create_cmb_html_with_transactions(_TEMPLATE_ROWS, "2030年03月").replace(
            "<td>记账日</td>", "<td>入账日</td>"
        )

        txns, (cov,) = self._parse(tmp_path, html, "changed.eml")

        assert txns == []
        assert not cov.itemized
        assert "Unrecognised CMB statement layout" in caplog.text
        assert "changed.eml" in caplog.text

    def test_rows_that_fit_no_column_order_warn_and_are_not_itemized(
        self, tmp_path, caplog
    ):
        html = create_cmb_html_with_transactions(
            _TEMPLATE_ROWS, "2030年03月", template="image"
        ).replace("<tr>", "<tr><td></td>")

        txns, (cov,) = self._parse(tmp_path, html, "changed.eml")

        assert txns == []
        assert not cov.itemized
        assert "changed.eml" in caplog.text

    def test_summary_row_of_a_quiet_month_is_not_a_layout_change(
        self, tmp_path, caplog
    ):
        summary = (
            "<table><tr><td>卡号末四位</td><td>8765</td>"
            "<td>本期应还</td><td>¥ 0.00</td></tr></table>"
        )
        html = create_cmb_html_with_transactions([], "2030年03月").replace(
            "</body>", f"{summary}</body>"
        )

        txns, (cov,) = self._parse(tmp_path, html)

        assert txns == []
        assert cov.itemized
        assert "Unrecognised" not in caplog.text

    def test_section_ends_with_its_band(self, tmp_path):
        trailer = "<table><tr>{}</tr></table>".format(
            "".join(
                f"<td>{v}</td>"
                for v in ["", "0320", "0321", "payee-z", "¥ 2.30", "4321", "", ""]
            )
        )
        html = create_cmb_html_with_transactions(
            _TEMPLATE_ROWS, "2030年03月", template="image"
        ).replace("</body>", f"{trailer}</body>")

        txns, _ = self._parse(tmp_path, html)

        assert [t.description for t in txns] == [
            r["description"] for r in _TEMPLATE_ROWS
        ]


class TestCMBMailMonth:
    """Tests for reading the statement month of a mail."""

    def _month(
        self,
        tmp_path: Path,
        html: str,
        name="statement.eml",
        subject="招商银行信用卡电子账单",
    ):
        path = tmp_path / name
        path.write_text(create_cmb_eml(html, subject), encoding="utf-8")
        (cov,) = CMBCreditProvider().coverage(path, [])
        return cov.start, cov.end

    def test_cycle_end_gives_the_month(self, tmp_path):
        html = create_cmb_html_with_transactions([], "2030年03月", template="image")

        assert self._month(tmp_path, html) == (date(2030, 3, 1), date(2030, 3, 31))

    def test_cycle_end_bounds_the_row_year(self, tmp_path):
        """A row posted after the closing date belongs to the year before."""
        rows = [{**_TEMPLATE_ROWS[0], "post_date": "0330"}]
        html = create_cmb_html_with_transactions(rows, "2030年03月", template="image")
        path = tmp_path / "statement.eml"
        path.write_text(create_cmb_eml(html), encoding="utf-8")

        (txn,) = CMBCreditProvider().parse(path)

        assert txn.date == date(2029, 3, 30)

    def test_summary_only_mail_takes_the_subject_month(self, tmp_path):
        html = "<html><body><div>本期应还金额 ¥ 52.10</div></body></html>"

        assert self._month(
            tmp_path, html, subject="招商银行信用卡电子账单2030年4月"
        ) == (date(2030, 4, 1), date(2030, 4, 30))

    def test_falls_back_to_the_file_name(self, tmp_path):
        html = "<html><body><div>本期应还金额 ¥ 52.10</div></body></html>"

        assert self._month(tmp_path, html, "账单2030年05月.eml") == (
            date(2030, 5, 1),
            date(2030, 5, 31),
        )

    def test_no_month_anywhere_warns(self, tmp_path, caplog):
        html = "<html><body><div>本期应还金额 ¥ 52.10</div></body></html>"

        start, _ = self._month(tmp_path, html, "statement.eml")

        assert (start.year, start.month) == (date.today().year, date.today().month)
        assert "No statement month in" in caplog.text


# x of each table column in the reissued PDF, as the bank's template places them.
_PDF_COLUMNS = {
    "trans": 43,
    "post": 92,
    "desc": 140,
    "amount": 369,
    "card": 455,
    "original": 542,
}


def write_cmb_pdf(
    path: Path,
    sections: list[tuple[str, list[tuple[str, ...]]]],
    month: str = "2030年03月",
    closing: str = "2030年03月22日",
    rows_per_page: int = 40,
) -> Path:
    """Draw a reissued CMB statement PDF.

    sections holds (account heading, rows); a row is (trans, post, description,
    amount, card, original), with "" for an empty cell.
    """
    import fitz

    doc = fitz.open()

    def new_page() -> fitz.Page:
        page = doc.new_page(width=624, height=863)
        page.insert_text(
            (40, 820), f"招商银行信用卡对账单（{month}）（补）", fontname="china-s"
        )
        return page

    def put(page: fitz.Page, x: float, y: float, text: str) -> None:
        page.insert_text((x, y), text, fontname="china-s", fontsize=8)

    page = new_page()
    put(page, 45, 60, "账单日")
    put(page, 45, 72, closing)
    y, count = 120.0, 0
    for heading, rows in sections:
        put(page, 42, y, heading)
        y += 14
        headers = [
            "交易日",
            "记账日",
            "交易摘要",
            "人民币金额",
            "卡号末四位",
            "交易地金额",
        ]
        if heading.startswith("美元"):
            headers[3] = "美元金额"
        for x, label in zip(_PDF_COLUMNS.values(), headers, strict=True):
            put(page, x, y, label)
        y += 14
        for row in rows:
            if count == rows_per_page:
                page, y, count = new_page(), 60.0, 0
            for x, cell in zip(_PDF_COLUMNS.values(), row, strict=True):
                if cell:
                    put(page, x, y, cell)
            y += 13
            count += 1
        y += 14
    doc.save(path)
    doc.close()
    return path


_RMB = "人民币账户 RMB A/C"


class TestCMBPdfReissue:
    """Tests for the PDF reissue (补发账单)."""

    def test_parses_every_row_kind(self, tmp_path):
        pdf = write_cmb_pdf(
            tmp_path / "reissue.pdf",
            [
                (
                    _RMB,
                    [
                        (
                            "还款",
                            "03/05",
                            "银联在线网络还款",
                            "-613.00",
                            "4321",
                            "-613.00",
                        ),
                        ("分期", "", "", "", "", ""),
                        (
                            "11/20",
                            "03/08",
                            "payee-a 004-012",
                            "63.10",
                            "4321",
                            "63.10",
                        ),
                        ("消费", "", "", "", "", ""),
                        ("03/10", "03/11", "payee-b", "73.40", "8765", "73.40(CN)"),
                        ("03/12", "03/13", "payee-c", "18.70", "8765", "24.30(HK)"),
                        ("退款", "", "", "", "", ""),
                        ("03/14", "03/15", "payee-b", "-73.40", "8765", "-73.40"),
                    ],
                ),
                (
                    "美元账户 USD A/C",
                    [("03/16", "03/17", "payee-d", "9.30", "8765", "9.30(US)")],
                ),
            ],
        )

        txns = CMBCreditProvider().parse(pdf)

        assert [(t.date, t.amount, t.currency, t.card_last4) for t in txns] == [
            (date(2030, 3, 5), Decimal("-613.00"), "CNY", "4321"),
            (date(2029, 11, 20), Decimal("63.10"), "CNY", "4321"),
            (date(2030, 3, 10), Decimal("73.40"), "CNY", "8765"),
            (date(2030, 3, 12), Decimal("18.70"), "CNY", "8765"),
            (date(2030, 3, 14), Decimal("-73.40"), "CNY", "8765"),
            (date(2030, 3, 16), Decimal("9.30"), "USD", "8765"),
        ]
        assert [t.description for t in txns][:3] == [
            "银联在线网络还款",
            "payee-a 004-012",
            "payee-b",
        ]
        assert "posting_date" not in txns[0].metadata
        assert txns[1].metadata["posting_date"] == "2030-03-08"
        assert "original_amount" not in txns[2].metadata
        assert "country" not in txns[2].metadata
        assert txns[3].metadata["original_amount"] == Decimal("24.30")
        assert txns[3].metadata["country"] == "HK"
        assert all(t.provider == "cmb_credit" for t in txns)

    def test_matches_the_eml_row_for_row(self, tmp_path):
        """Both formats of one statement yield the same transactions."""
        rows = [
            ("", "0305", "银联在线网络还款", "-613.00", "4321", "-613.00", ""),
            ("0310", "0311", "payee-b", "73.40", "8765", "73.40", "CN"),
            ("0312", "0313", "payee-c", "18.70", "8765", "24.30", "HK"),
            ("0325", "0226", "payee-e 012-012", "35.60", "4321", "35.60", ""),
        ]
        html = create_cmb_html_with_transactions(
            [
                {
                    "trans_date": t,
                    "post_date": p,
                    "description": d,
                    "amount": a,
                    "card_last4": c,
                    "original_amount": o,
                    "country": n,
                }
                for t, p, d, a, c, o, n in rows
            ],
            statement_month="2030年03月",
        )
        eml = tmp_path / "招商银行信用卡电子账单.eml"
        eml.write_text(create_cmb_eml(html), encoding="utf-8")
        pdf = write_cmb_pdf(
            tmp_path / "reissue.pdf",
            [
                (
                    _RMB,
                    [
                        (
                            f"{t[:2]}/{t[2:]}" if t else "",
                            f"{p[:2]}/{p[2:]}",
                            d,
                            a,
                            c,
                            f"{o}({n})" if n else o,
                        )
                        for t, p, d, a, c, o, n in rows
                    ],
                )
            ],
        )

        def shape(path: Path) -> list[tuple]:
            return [
                (
                    t.date,
                    t.amount,
                    t.currency,
                    t.description,
                    t.card_last4,
                    t.metadata,
                    t.statement_period,
                )
                for t in CMBCreditProvider().parse(path)
            ]

        assert shape(pdf) == shape(eml)

    def test_rows_continue_across_pages(self, tmp_path):
        rows = [
            (f"03/{d:02d}", f"03/{d:02d}", f"payee-{d}", "8.70", "4321", "8.70")
            for d in range(1, 21)
        ]
        pdf = write_cmb_pdf(tmp_path / "reissue.pdf", [(_RMB, rows)], rows_per_page=7)

        txns = CMBCreditProvider().parse(pdf)

        assert [t.description for t in txns] == [f"payee-{d}" for d in range(1, 21)]

    def test_detected_by_content(self, tmp_path):
        pdf = write_cmb_pdf(
            tmp_path / "statement.pdf",
            [(_RMB, [("03/10", "03/11", "payee-b", "73.40", "8765", "73.40")])],
        )

        assert CMBCreditProvider.can_handle(pdf)
        assert not get_provider("boc_credit").can_handle(pdf)

    def test_boc_pdf_is_not_detected(self):
        examples = Path(__file__).resolve().parents[2] / "examples"
        boc = next((examples / "boc_credit").glob("*.pdf"))

        assert not CMBCreditProvider.can_handle(boc)


class TestCMBCoverage:
    """Tests for statement selection between the eml and the PDF reissue."""

    def _itemized_html(self) -> str:
        return create_cmb_html_with_transactions(
            [
                {
                    "trans_date": "0310",
                    "post_date": "0311",
                    "description": "payee-b",
                    "card_last4": "8765",
                    "amount": "52.10",
                }
            ],
            statement_month="2030年03月",
        )

    def _eml(self, tmp_path: Path, html: str, name: str = "statement.eml") -> Path:
        path = tmp_path / name
        path.write_text(create_cmb_eml(html), encoding="utf-8")
        return path

    def _pdf(self, tmp_path: Path, month: str = "2030年03月") -> Path:
        return write_cmb_pdf(
            tmp_path / f"reissue-{month}.pdf",
            [(_RMB, [("03/10", "03/11", "payee-b", "52.10", "8765", "52.10")])],
            month=month,
        )

    def _coverage(self, path: Path):
        provider = CMBCreditProvider()
        txns = provider.parse(path)
        return provider.coverage(path, txns), txns

    def test_itemized_eml_covers_its_month(self, tmp_path):
        (cov,), txns = self._coverage(self._eml(tmp_path, self._itemized_html()))

        assert (cov.start, cov.end) == (date(2030, 3, 1), date(2030, 3, 31))
        assert cov.itemized
        assert cov.source == "statement"
        assert cov.rows == txns

    def test_summary_only_eml_is_not_itemized(self, tmp_path):
        html = """<html><body><div>您2030年03月信用卡账单已出</div>
<table><tr><td>本期应还金额</td><td>¥ 52.10</td></tr></table></body></html>"""
        (cov,), txns = self._coverage(self._eml(tmp_path, html))

        assert txns == []
        assert not cov.itemized
        assert (cov.start, cov.end) == (date(2030, 3, 1), date(2030, 3, 31))

    def test_pdf_is_a_reissue_of_its_month(self, tmp_path):
        (cov,), txns = self._coverage(self._pdf(tmp_path))

        assert (cov.start, cov.end) == (date(2030, 3, 1), date(2030, 3, 31))
        assert cov.itemized
        assert cov.source == "reissue"
        assert cov.rows == txns

    def _select(self, paths: list[Path]):
        from bean_sieve.core.coverage import CoverageEntry, select

        provider = CMBCreditProvider()
        ranks = {kind: i for i, kind in enumerate(provider.coverage_priority)}
        parsed = {path: provider.parse(path) for path in paths}
        entries = [
            CoverageEntry("cmb_credit", path, cov, ranks[cov.source])
            for path in paths
            for cov in provider.coverage(path, parsed[path])
        ]
        selection = select(entries)
        kept = selection.keep([txn for path in paths for txn in parsed[path]])
        return selection, kept

    @pytest.mark.parametrize("eml_first", [True, False])
    def test_eml_wins_over_the_pdf_of_its_month(self, tmp_path, eml_first):
        eml = self._eml(tmp_path, self._itemized_html())
        reissue, other_month = self._pdf(tmp_path), self._pdf(tmp_path, "2030年02月")
        paths = (
            [eml, reissue, other_month] if eml_first else [reissue, other_month, eml]
        )

        selection, _ = self._select(paths)

        assert {e.path for e in selection.adopted} == {eml, other_month}
        assert [d.entry.path for d in selection.drops] == [reissue]

    def test_reissue_supplies_the_usd_account_of_a_mailed_month(self, tmp_path):
        eml = self._eml(tmp_path, self._itemized_html())
        reissue = write_cmb_pdf(
            tmp_path / "reissue.pdf",
            [
                (_RMB, [("03/10", "03/11", "payee-b", "52.10", "8765", "52.10")]),
                (
                    "美元账户 USD A/C",
                    [("03/16", "03/17", "payee-d", "9.30", "8765", "9.30(US)")],
                ),
            ],
        )

        selection, kept = self._select([eml, reissue])

        assert sorted((t.currency, t.source_file) for t in kept) == [
            ("CNY", eml),
            ("USD", reissue),
        ]
        assert [d.entry.coverage.account for d in selection.drops] == ["CNY"]

    def _usd_reissue(self, tmp_path: Path) -> Path:
        return write_cmb_pdf(
            tmp_path / "reissue.pdf",
            [
                (_RMB, [("03/10", "03/11", "payee-b", "52.10", "8765", "52.10")]),
                (
                    "美元账户 USD A/C",
                    [("03/16", "03/17", "payee-d", "26.10", "8765", "26.10(US)")],
                ),
            ],
        )

    def _eml_with_usd(self, tmp_path: Path, usd_transactions: list[dict]) -> Path:
        cny = {
            "trans_date": "0310",
            "post_date": "0311",
            "description": "payee-b",
            "card_last4": "8765",
            "amount": "52.10",
        }
        html = create_cmb_html_with_transactions(
            [cny], "2030年03月", usd_transactions=usd_transactions
        )
        return self._eml(
            tmp_path, html.replace("<td>$ 8.70</td>", "<td></td><td>$ 8.70</td>")
        )

    def test_unread_usd_row_leaves_the_usd_account_to_the_reissue(
        self, tmp_path, caplog
    ):
        usd = {
            "trans_date": "0316",
            "post_date": "0317",
            "description": "payee-d",
            "card_last4": "8765",
            "amount": "8.70",
        }
        eml = self._eml_with_usd(tmp_path, [usd])
        reissue = self._usd_reissue(tmp_path)

        covs, _ = self._coverage(eml)
        _, kept = self._select([eml, reissue])

        assert [(c.account, c.itemized) for c in covs] == [
            ("CNY", True),
            ("USD", False),
        ]
        assert "Unrecognised CMB statement layout" in caplog.text
        assert "USD account" in caplog.text
        assert sorted((t.currency, t.source_file) for t in kept) == [
            ("CNY", eml),
            ("USD", reissue),
        ]

    def test_unrecognised_section_header_warns(self, tmp_path, caplog):
        usd = {
            "trans_date": "0316",
            "post_date": "0317",
            "description": "payee-d",
            "card_last4": "8765",
            "amount": "8.70",
        }
        html = create_cmb_html_with_transactions(
            _TEMPLATE_ROWS[:1], "2030年03月", usd_transactions=[usd]
        ).replace("<td>美元金额</td>", "<td>外币金额</td>")

        covs, txns = self._coverage(self._eml(tmp_path, html, "changed.eml"))

        assert [t.currency for t in txns] == ["CNY"]
        assert [(c.account, c.itemized) for c in covs] == [("CNY", True)]
        assert "Unrecognised CMB statement section" in caplog.text
        assert "changed.eml" in caplog.text

    def test_empty_usd_section_is_an_itemized_quiet_month(self, tmp_path, caplog):
        eml = self._eml_with_usd(tmp_path, [])
        reissue = self._usd_reissue(tmp_path)

        covs, _ = self._coverage(eml)
        selection, kept = self._select([eml, reissue])

        assert [(c.account, c.itemized, len(c.rows)) for c in covs] == [
            ("CNY", True, 1),
            ("USD", True, 0),
        ]
        assert "Unrecognised" not in caplog.text
        assert [(t.currency, t.source_file) for t in kept] == [("CNY", eml)]
        assert sorted(d.entry.coverage.account for d in selection.drops) == [
            "CNY",
            "USD",
        ]

    def test_eml_usd_section_wins_over_the_reissue(self, tmp_path):
        eml = self._eml(
            tmp_path,
            create_cmb_html_with_transactions(
                [
                    {
                        "trans_date": "0310",
                        "post_date": "0311",
                        "description": "payee-b",
                        "card_last4": "8765",
                        "amount": "52.10",
                    }
                ],
                statement_month="2030年03月",
                usd_transactions=[
                    {
                        "trans_date": "0316",
                        "post_date": "0317",
                        "description": "payee-d",
                        "card_last4": "8765",
                        "amount": "9.30",
                        "country": "US",
                    }
                ],
            ),
        )
        reissue = write_cmb_pdf(
            tmp_path / "reissue.pdf",
            [
                (_RMB, [("03/10", "03/11", "payee-b", "52.10", "8765", "52.10")]),
                (
                    "美元账户 USD A/C",
                    [("03/16", "03/17", "payee-d", "9.30", "8765", "9.30(US)")],
                ),
            ],
        )

        selection, kept = self._select([reissue, eml])

        assert sorted((t.currency, t.source_file) for t in kept) == [
            ("CNY", eml),
            ("USD", eml),
        ]
        assert sorted(d.entry.coverage.account for d in selection.drops) == [
            "CNY",
            "USD",
        ]
