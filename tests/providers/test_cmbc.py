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


def create_cmbc_html_with_transactions(transactions: list[dict]) -> str:
    """Create CMBC statement HTML with transaction rows.

    Each transaction dict should have:
    - trans_date: MM/DD format
    - post_date: MM/DD format
    - description: str
    - amount: str (e.g., "100.00" or "-100.00")
    - card_last4: str
    """
    rows = []
    for txn in transactions:
        row = f"""<span id='fixBand9'>
            <table><tr>
                <td>{txn["trans_date"]}</td>
                <td>{txn["post_date"]}</td>
                <td><span id='fixBand22'>{txn["description"]}</span></td>
                <td><span id='fixBand8'>{txn["amount"]}</span></td>
                <td><span id='fixBand2'>{txn["card_last4"]}</span></td>
            </tr></table>
        </span>"""
        rows.append(row)

    return f"""<!DOCTYPE HTML>
<html>
<head><meta charset="UTF-8"></head>
<body>
<div>2025年12月对账单</div>
<div>Statement Date</div><div>2025/12/10</div>
<span id='loopBand3'>
    {"".join(rows)}
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
            "card_last4": "5515",
        },
        {
            "trans_date": "12/04",
            "post_date": "12/04",
            "description": "支付宝-沃尔玛",
            "amount": "60.90",
            "card_last4": "5515",
        },
        {
            "trans_date": "12/04",
            "post_date": "12/04",
            "description": "支付宝退款",
            "amount": "-60.90",
            "card_last4": "5515",
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
        assert txn1.card_last4 == "5515"
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
                "card_last4": "5515",
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
                "card_last4": "5515",
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
                "card_last4": "5515",
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
                "card_last4": "5515",
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
