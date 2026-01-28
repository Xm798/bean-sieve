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


def create_cmb_html_with_transactions(transactions: list[dict]) -> str:
    """Create CMB statement HTML with transaction rows.

    Each transaction dict should have:
    - trans_date: MMDD or empty string
    - post_date: MMDD
    - description: str
    - card_last4: str
    - amount: str (e.g., "100.00" or "-100.00")
    """
    rows = []
    for txn in transactions:
        # Create 9-cell row structure matching CMB format
        row = f"""<tr>
            <td>{txn["trans_date"]}{txn["post_date"]}{txn["description"]}¥ {txn["amount"]}{txn["card_last4"]}{txn["amount"]}</td>
            <td></td>
            <td>{txn["trans_date"]}</td>
            <td>{txn["post_date"]}</td>
            <td>{txn["description"]}</td>
            <td>¥ {txn["amount"]}</td>
            <td>{txn["card_last4"]}</td>
            <td>{txn["amount"]}</td>
            <td></td>
        </tr>"""
        rows.append(row)

    return f"""<!DOCTYPE HTML>
<html>
<head><meta charset="UTF-8"></head>
<body>
<div>您2025年12月信用卡账单已出</div>
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
            "card_last4": "8715",
            "amount": "-7,177.43",
        },
        # Expense with trans_date and post_date
        {
            "trans_date": "1210",
            "post_date": "1211",
            "description": "支付宝-测试商户",
            "card_last4": "8715",
            "amount": "3,680.00",
        },
        # Expense with different card
        {
            "trans_date": "1217",
            "post_date": "1218",
            "description": "餐饮消费",
            "card_last4": "9774",
            "amount": "102.00",
        },
        # Refund
        {
            "trans_date": "1128",
            "post_date": "1129",
            "description": "优惠退款",
            "card_last4": "9774",
            "amount": "-1.00",
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
        assert txn1.amount == Decimal("-7177.43")
        assert txn1.currency == "CNY"
        assert txn1.card_last4 == "8715"
        assert "还款" in txn1.description
        assert txn1.provider == "cmb_credit"
        assert txn1.is_income

        # Check expense with trans_date
        txn2 = transactions[1]
        assert txn2.date == date(2025, 12, 10)
        assert txn2.amount == Decimal("3680.00")
        assert "支付宝" in txn2.description
        assert txn2.is_expense

        # Check different card
        txn3 = transactions[2]
        assert txn3.card_last4 == "9774"
        assert txn3.date == date(2025, 12, 17)

        # Check refund
        txn4 = transactions[3]
        assert txn4.amount == Decimal("-1.00")
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

    def test_cross_year_january_in_december_statement(self, tmp_path):
        """Test January transactions in December statement."""
        transactions = [
            {
                "trans_date": "0102",  # January 2
                "post_date": "0102",
                "description": "分期还款",
                "card_last4": "8715",
                "amount": "25.00",
            },
        ]
        html = create_cmb_html_with_transactions(transactions)
        file_path = tmp_path / "招商银行信用卡电子账单.eml"
        file_path.write_text(create_cmb_eml(html), encoding="utf-8")

        provider = CMBCreditProvider()
        txns = provider.parse(file_path)

        assert len(txns) == 1
        # January in December statement should be next year
        assert txns[0].date == date(2026, 1, 2)

    def test_november_transactions_in_december_statement(self, tmp_path):
        """Test November transactions in December statement."""
        transactions = [
            {
                "trans_date": "1128",
                "post_date": "1129",
                "description": "消费",
                "card_last4": "8715",
                "amount": "100.00",
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
                "card_last4": "8715",
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
                "card_last4": "8715",
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

    def test_negative_amount(self, tmp_path):
        """Test parsing negative amounts (refunds/repayments)."""
        transactions = [
            {
                "trans_date": "1210",
                "post_date": "1211",
                "description": "退款",
                "card_last4": "8715",
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
                "card_last4": "8715",
                "amount": "100.00",
            },
            {
                "trans_date": "1212",
                "post_date": "1213",
                "description": "消费2",
                "card_last4": "9774",
                "amount": "200.00",
            },
            {
                "trans_date": "1214",
                "post_date": "1215",
                "description": "消费3",
                "card_last4": "0241",
                "amount": "300.00",
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
        assert cards == {"8715", "9774", "0241"}


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
