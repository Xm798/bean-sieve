"""Tests for China Merchants Bank (CMB) credit card statement provider."""

import base64
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.providers import get_provider
from bean_sieve.providers.banks.credit.cmb import CMBCreditProvider


def create_cmb_eml(html_content: str) -> str:
    """Create an EML file content with the given HTML (base64 encoded)."""
    encoded = base64.b64encode(html_content.encode("utf-8")).decode("ascii")
    return f"""From: creditcard@cmbchina.com
Subject: =?utf-8?B?5oub5ZWG6ZO26KGM5L+h55So5Y2h55S15a2Q6LSm5Y2V?=
To: test@example.com
MIME-Version: 1.0
Content-Type: text/html; charset="utf-8"
Content-Transfer-Encoding: base64

{encoded}
"""


def create_cmb_html_with_transactions(
    transactions: list[dict], statement_month: str = "2025年12月"
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

    statement_month is the 账单 header CMB derives the billing cycle from.
    """
    rows = []
    for txn in transactions:
        original_amount = txn.get("original_amount", txn["amount"])
        country = txn.get("country", "")
        # Create 9-cell row structure matching CMB format
        row = f"""<tr>
            <td>{txn["trans_date"]}{txn["post_date"]}{txn["description"]}¥ {txn["amount"]}{txn["card_last4"]}{txn["amount"]}</td>
            <td></td>
            <td>{txn["trans_date"]}</td>
            <td>{txn["post_date"]}</td>
            <td>{txn["description"]}</td>
            <td>¥ {txn["amount"]}</td>
            <td>{txn["card_last4"]}</td>
            <td>{original_amount}</td>
            <td>{country}</td>
        </tr>"""
        rows.append(row)

    return f"""<!DOCTYPE HTML>
<html>
<head><meta charset="UTF-8"></head>
<body>
<div>您{statement_month}信用卡账单已出</div>
<table class="bgTable">
    {"".join(rows)}
</table>
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
                "amount": "-5000.00",
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
        html = """<!DOCTYPE HTML>
<html>
<head><meta charset="UTF-8"></head>
<body>
<div>您2025年12月信用卡账单已出</div>
<table class="bgTable">
</table>
</body>
</html>"""
        file_path = tmp_path / "招商银行信用卡电子账单.eml"
        file_path.write_text(create_cmb_eml(html), encoding="utf-8")

        provider = CMBCreditProvider()
        transactions = provider.parse(file_path)

        assert transactions == []


class TestCMBStatementPeriod:
    """Tests for per-card statement period assignment."""

    def test_cycle_widens_only_for_the_card_dated_outside_it(self, tmp_path):
        """Test that an out-of-cycle row widens its own card's period."""
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
        assert periods["2468"] == (date(2030, 2, 27), date(2030, 3, 31))
        assert periods["1357"] == (date(2030, 3, 1), date(2030, 3, 31))


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
                        ("03/10", "03/11", "payee-b", "71.90", "8765", "71.90(CN)"),
                        ("03/12", "03/13", "payee-c", "17.20", "8765", "24.30(HK)"),
                        ("退款", "", "", "", "", ""),
                        ("03/14", "03/15", "payee-b", "-71.90", "8765", "-71.90"),
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
            (date(2030, 3, 10), Decimal("71.90"), "CNY", "8765"),
            (date(2030, 3, 12), Decimal("17.20"), "CNY", "8765"),
            (date(2030, 3, 14), Decimal("-71.90"), "CNY", "8765"),
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
            ("0310", "0311", "payee-b", "71.90", "8765", "71.90", "CN"),
            ("0312", "0313", "payee-c", "17.20", "8765", "24.30", "HK"),
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
            [(_RMB, [("03/10", "03/11", "payee-b", "71.90", "8765", "71.90")])],
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
