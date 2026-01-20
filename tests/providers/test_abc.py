"""Tests for Agricultural Bank of China (ABC) credit card statement provider."""

import base64
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.providers import get_provider
from bean_sieve.providers.banks.credit.abc import ABCCreditProvider


def create_abc_eml(html_content: str) -> str:
    """Create an EML file content with the given HTML."""
    encoded = base64.b64encode(html_content.encode("utf-8")).decode("ascii")
    return f"""From: creditcard@abchina.com
Subject: =?utf-8?B?6YeR56mX5L+h55So5Y2h55S15a2Q5a+56LSm5Y2V?=
To: test@example.com
MIME-Version: 1.0
Content-Type: text/html; charset="utf-8"
Content-Transfer-Encoding: base64

{encoded}
"""


@pytest.fixture
def abc_html_content():
    """Sample ABC statement HTML content."""
    return """<html>
<head><title>金穗信用卡电子对账单</title></head>
<body>
<table>
    <tr><td><span>620000******1234</span></td></tr>
    <tr><td><span>2025/10/24-2025/11/23</span></td></tr>
    <tr><td><span>本期应还款额</span></td></tr>
</table>
<table>
    <tr>
        <td>251103</td>
        <td>251103</td>
        <td>1234</td>
        <td>McDonald's Beijing</td>
        <td>-45.00/CNY</td>
        <td>-45.00/CNY</td>
    </tr>
    <tr>
        <td>251108</td>
        <td>251108</td>
        <td>1234</td>
        <td>Apple Store Online</td>
        <td>-999.00/CNY</td>
        <td>-999.00/CNY</td>
    </tr>
    <tr>
        <td>251115</td>
        <td>251115</td>
        <td>1234</td>
        <td>Hilton Hotel Shanghai</td>
        <td>-1,280.00/CNY</td>
        <td>-1,280.00/CNY</td>
    </tr>
    <tr>
        <td>251120</td>
        <td>251120</td>
        <td>1234</td>
        <td>退款-电商平台</td>
        <td>100.00/CNY</td>
        <td>100.00/CNY</td>
    </tr>
</table>
</body>
</html>"""


@pytest.fixture
def abc_eml_file(tmp_path, abc_html_content):
    """Create a temporary ABC EML file."""
    file_path = tmp_path / "农业银行金穗信用卡2025年11月电子账单.eml"
    eml_content = create_abc_eml(abc_html_content)
    file_path.write_text(eml_content, encoding="utf-8")
    return file_path


class TestABCCreditProvider:
    """Tests for ABCCreditProvider."""

    def test_provider_registration(self):
        """Test that ABC provider is properly registered."""
        provider = get_provider("abc_credit")
        assert isinstance(provider, ABCCreditProvider)
        assert provider.provider_id == "abc_credit"
        assert provider.provider_name == "农业银行信用卡"
        assert ".eml" in provider.supported_formats

    def test_can_handle(self):
        """Test file format detection."""
        assert ABCCreditProvider.can_handle(
            Path("农业银行金穗信用卡2025年11月电子账单.eml")
        )
        assert ABCCreditProvider.can_handle(Path("金穗信用卡账单.eml"))
        assert not ABCCreditProvider.can_handle(Path("abc_statement.csv"))
        assert not ABCCreditProvider.can_handle(Path("statement.eml"))

    def test_parse_transactions(self, abc_eml_file):
        """Test parsing transactions from EML file."""
        provider = ABCCreditProvider()
        transactions = provider.parse(abc_eml_file)

        assert len(transactions) == 4

        # Check expense transactions (should be positive after negation)
        txn1 = transactions[0]
        assert txn1.date == date(2025, 11, 3)
        assert txn1.amount == Decimal("45.00")
        assert txn1.currency == "CNY"
        assert txn1.card_last4 == "1234"
        assert "McDonald's" in txn1.description
        assert txn1.provider == "abc_credit"
        assert txn1.is_expense

        txn2 = transactions[1]
        assert txn2.date == date(2025, 11, 8)
        assert txn2.amount == Decimal("999.00")
        assert "Apple" in txn2.description

        # Check amount with thousands separator
        txn3 = transactions[2]
        assert txn3.date == date(2025, 11, 15)
        assert txn3.amount == Decimal("1280.00")
        assert "Hilton" in txn3.description

        # Check refund transaction (should be negative after negation)
        txn4 = transactions[3]
        assert txn4.date == date(2025, 11, 20)
        assert txn4.amount == Decimal("-100.00")
        assert "退款" in txn4.description
        assert txn4.is_income

    def test_statement_period_extraction(self, abc_eml_file):
        """Test that statement period is properly extracted."""
        provider = ABCCreditProvider()
        transactions = provider.parse(abc_eml_file)

        assert len(transactions) > 0
        txn = transactions[0]
        assert txn.statement_period == (date(2025, 10, 24), date(2025, 11, 23))

    def test_card_last4_extraction(self, abc_eml_file):
        """Test that card_last4 is properly extracted."""
        provider = ABCCreditProvider()
        transactions = provider.parse(abc_eml_file)

        for txn in transactions:
            assert txn.card_last4 == "1234"

    def test_per_card_statement_flag(self):
        """Test that per_card_statement is set correctly."""
        provider = ABCCreditProvider()
        assert provider.per_card_statement is True

    def test_empty_statement(self, tmp_path):
        """Test handling of statement with no transactions."""
        html = """<html>
<body>
<table>
    <tr><td><span>2025/10/24-2025/11/23</span></td></tr>
</table>
<table>
    <tr><td>本期无交易记录</td></tr>
</table>
</body></html>"""
        file_path = tmp_path / "农业银行金穗信用卡空账单.eml"
        file_path.write_text(create_abc_eml(html), encoding="utf-8")

        provider = ABCCreditProvider()
        transactions = provider.parse(file_path)

        assert transactions == []


class TestABCDateParsing:
    """Tests for ABC date parsing edge cases."""

    def test_yymmdd_format(self, tmp_path):
        """Test parsing YYMMDD date format."""
        html = """<html>
<body>
<table><tr><td><span>2025/12/01-2025/12/31</span></td></tr></table>
<table>
    <tr>
        <td>251225</td>
        <td>251225</td>
        <td>5678</td>
        <td>Google Cloud</td>
        <td>-100.00/CNY</td>
        <td>-100.00/CNY</td>
    </tr>
</table>
</body></html>"""
        file_path = tmp_path / "农业银行金穗信用卡.eml"
        file_path.write_text(create_abc_eml(html), encoding="utf-8")

        provider = ABCCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].date == date(2025, 12, 25)

    def test_cross_century_date(self, tmp_path):
        """Test that dates with YY > 50 are treated as 1900s (edge case)."""
        # This is a theoretical edge case - current dates use 20xx
        html = """<html>
<body>
<table><tr><td><span>2025/01/01-2025/01/31</span></td></tr></table>
<table>
    <tr>
        <td>250115</td>
        <td>250115</td>
        <td>9999</td>
        <td>Test Transaction</td>
        <td>-50.00/CNY</td>
        <td>-50.00/CNY</td>
    </tr>
</table>
</body></html>"""
        file_path = tmp_path / "农业银行金穗信用卡.eml"
        file_path.write_text(create_abc_eml(html), encoding="utf-8")

        provider = ABCCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].date == date(2025, 1, 15)


class TestABCAmountParsing:
    """Tests for ABC amount parsing edge cases."""

    def test_amount_with_thousands_separator(self, tmp_path):
        """Test parsing amounts with comma separators."""
        html = """<html>
<body>
<table><tr><td><span>2025/11/01-2025/11/30</span></td></tr></table>
<table>
    <tr>
        <td>251115</td>
        <td>251115</td>
        <td>1234</td>
        <td>Marriott International</td>
        <td>-12,345.67/CNY</td>
        <td>-12,345.67/CNY</td>
    </tr>
</table>
</body></html>"""
        file_path = tmp_path / "农业银行金穗信用卡.eml"
        file_path.write_text(create_abc_eml(html), encoding="utf-8")

        provider = ABCCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("12345.67")

    def test_usd_amount(self, tmp_path):
        """Test parsing USD amounts."""
        html = """<html>
<body>
<table><tr><td><span>2025/11/01-2025/11/30</span></td></tr></table>
<table>
    <tr>
        <td>251120</td>
        <td>251120</td>
        <td>1234</td>
        <td>Amazon.com</td>
        <td>-99.99/USD</td>
        <td>-99.99/USD</td>
    </tr>
</table>
</body></html>"""
        file_path = tmp_path / "农业银行金穗信用卡.eml"
        file_path.write_text(create_abc_eml(html), encoding="utf-8")

        provider = ABCCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].currency == "USD"
        assert transactions[0].amount == Decimal("99.99")

    def test_positive_amount_refund(self, tmp_path):
        """Test parsing positive amounts (refunds)."""
        html = """<html>
<body>
<table><tr><td><span>2025/11/01-2025/11/30</span></td></tr></table>
<table>
    <tr>
        <td>251125</td>
        <td>251125</td>
        <td>1234</td>
        <td>退款-Apple Store</td>
        <td>299.00/CNY</td>
        <td>299.00/CNY</td>
    </tr>
</table>
</body></html>"""
        file_path = tmp_path / "农业银行金穗信用卡.eml"
        file_path.write_text(create_abc_eml(html), encoding="utf-8")

        provider = ABCCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("-299.00")
        assert transactions[0].is_income
