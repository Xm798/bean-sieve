"""Tests for Bank of Shanghai (BOSC) credit card statement provider."""

import quopri
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.providers import get_provider
from bean_sieve.providers.banks.credit.bosc import BOSCCreditProvider


def create_bosc_eml(html_content: str) -> str:
    """Create an EML file content with the given HTML (quoted-printable encoded)."""
    encoded = quopri.encodestring(html_content.encode("utf-8")).decode("ascii")
    return f"""From: creditcard@bosc.cn
Subject: =?utf-8?B?5LiK5rW36ZO26KGM5L+h55So5Y2h55S15a2Q5a+56LSm5Y2V?=
To: test@example.com
MIME-Version: 1.0
Content-Type: text/html; charset="utf-8"
Content-Transfer-Encoding: quoted-printable

{encoded}
"""


@pytest.fixture
def bosc_html_content():
    """Sample BOSC statement HTML content."""
    return """<html>
<head><title>上海银行信用卡电子对账单</title></head>
<body>
<table>
    <tr><td>对账周期：2025年11月19日-2025年12月18日</td></tr>
</table>
<table>
    <tr loop2="1">
        <td>2025年11月25日</td>
        <td>2025年11月25日</td>
        <td>McDonald's Shanghai</td>
        <td>45.50+</td>
        <td>1234</td>
    </tr>
    <tr loop2="2">
        <td>2025年12月01日</td>
        <td>2025年12月01日</td>
        <td>Apple Store Online</td>
        <td>999.00+</td>
        <td>1234</td>
    </tr>
    <tr loop2="3">
        <td>2025年12月05日</td>
        <td>2025年12月05日</td>
        <td>Hilton International</td>
        <td>2,580.00+</td>
        <td>1234</td>
    </tr>
    <tr loop2="4">
        <td>2025年12月10日</td>
        <td>2025年12月10日</td>
        <td>退款-电商平台</td>
        <td>100.00-</td>
        <td>1234</td>
    </tr>
</table>
</body>
</html>"""


@pytest.fixture
def bosc_eml_file(tmp_path, bosc_html_content):
    """Create a temporary BOSC EML file."""
    file_path = tmp_path / "上海银行信用卡2025年12月电子对账单.eml"
    eml_content = create_bosc_eml(bosc_html_content)
    file_path.write_text(eml_content, encoding="utf-8")
    return file_path


class TestBOSCCreditProvider:
    """Tests for BOSCCreditProvider."""

    def test_provider_registration(self):
        """Test that BOSC provider is properly registered."""
        provider = get_provider("bosc_credit")
        assert isinstance(provider, BOSCCreditProvider)
        assert provider.provider_id == "bosc_credit"
        assert provider.provider_name == "上海银行信用卡"
        assert ".eml" in provider.supported_formats

    def test_can_handle(self):
        """Test file format detection."""
        assert BOSCCreditProvider.can_handle(
            Path("上海银行信用卡2025年12月电子对账单.eml")
        )
        assert BOSCCreditProvider.can_handle(Path("上海银行信用卡账单.eml"))
        assert not BOSCCreditProvider.can_handle(Path("bosc_statement.csv"))
        assert not BOSCCreditProvider.can_handle(Path("statement.eml"))

    def test_parse_transactions(self, bosc_eml_file):
        """Test parsing transactions from EML file."""
        provider = BOSCCreditProvider()
        transactions = provider.parse(bosc_eml_file)

        assert len(transactions) == 4

        # Check expense transaction
        txn1 = transactions[0]
        assert txn1.date == date(2025, 11, 25)
        assert txn1.amount == Decimal("45.50")
        assert txn1.currency == "CNY"
        assert txn1.card_last4 == "1234"
        assert "McDonald's" in txn1.description
        assert txn1.provider == "bosc_credit"
        assert txn1.is_expense

        txn2 = transactions[1]
        assert txn2.date == date(2025, 12, 1)
        assert txn2.amount == Decimal("999.00")
        assert "Apple" in txn2.description

        # Check amount with thousands separator
        txn3 = transactions[2]
        assert txn3.date == date(2025, 12, 5)
        assert txn3.amount == Decimal("2580.00")
        assert "Hilton" in txn3.description

        # Check refund transaction (negative)
        txn4 = transactions[3]
        assert txn4.date == date(2025, 12, 10)
        assert txn4.amount == Decimal("-100.00")
        assert "退款" in txn4.description
        assert txn4.is_income

    def test_statement_period_extraction(self, bosc_eml_file):
        """Test that statement period is properly extracted."""
        provider = BOSCCreditProvider()
        transactions = provider.parse(bosc_eml_file)

        assert len(transactions) > 0
        txn = transactions[0]
        assert txn.statement_period == (date(2025, 11, 19), date(2025, 12, 18))

    def test_post_date_extraction(self, bosc_eml_file):
        """Test that post_date is properly extracted."""
        provider = BOSCCreditProvider()
        transactions = provider.parse(bosc_eml_file)

        txn = transactions[0]
        assert txn.post_date == date(2025, 11, 25)

    def test_card_last4_extraction(self, bosc_eml_file):
        """Test that card_last4 is properly extracted."""
        provider = BOSCCreditProvider()
        transactions = provider.parse(bosc_eml_file)

        for txn in transactions:
            assert txn.card_last4 == "1234"

    def test_empty_statement(self, tmp_path):
        """Test handling of statement with no transactions."""
        html = """<html>
<body>
<table>
    <tr><td>对账周期：2025年11月19日-2025年12月18日</td></tr>
</table>
<table>
    <tr><td>本期无交易记录</td></tr>
</table>
</body></html>"""
        file_path = tmp_path / "上海银行信用卡空账单.eml"
        file_path.write_text(create_bosc_eml(html), encoding="utf-8")

        provider = BOSCCreditProvider()
        transactions = provider.parse(file_path)

        assert transactions == []


class TestBOSCDateParsing:
    """Tests for BOSC date parsing edge cases."""

    def test_chinese_date_format(self, tmp_path):
        """Test parsing Chinese date format (YYYY年MM月DD日)."""
        html = """<html>
<body>
<table><tr><td>对账周期：2025年1月1日-2025年1月31日</td></tr></table>
<table>
    <tr loop2="1">
        <td>2025年1月15日</td>
        <td>2025年1月16日</td>
        <td>Google Cloud</td>
        <td>100.00+</td>
        <td>5678</td>
    </tr>
</table>
</body></html>"""
        file_path = tmp_path / "上海银行信用卡.eml"
        file_path.write_text(create_bosc_eml(html), encoding="utf-8")

        provider = BOSCCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].date == date(2025, 1, 15)
        assert transactions[0].post_date == date(2025, 1, 16)

    def test_single_digit_month_day(self, tmp_path):
        """Test parsing dates with single-digit month/day."""
        html = """<html>
<body>
<table><tr><td>对账周期：2025年2月1日-2025年2月28日</td></tr></table>
<table>
    <tr loop2="1">
        <td>2025年2月5日</td>
        <td>2025年2月5日</td>
        <td>Amazon</td>
        <td>88.88+</td>
        <td>9999</td>
    </tr>
</table>
</body></html>"""
        file_path = tmp_path / "上海银行信用卡.eml"
        file_path.write_text(create_bosc_eml(html), encoding="utf-8")

        provider = BOSCCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].date == date(2025, 2, 5)


class TestBOSCAmountParsing:
    """Tests for BOSC amount parsing edge cases."""

    def test_amount_with_plus_sign(self, tmp_path):
        """Test parsing amounts with + sign (expense)."""
        html = """<html>
<body>
<table><tr><td>对账周期：2025年12月1日-2025年12月31日</td></tr></table>
<table>
    <tr loop2="1">
        <td>2025年12月15日</td>
        <td>2025年12月15日</td>
        <td>Marriott International</td>
        <td>3,500.00+</td>
        <td>1234</td>
    </tr>
</table>
</body></html>"""
        file_path = tmp_path / "上海银行信用卡.eml"
        file_path.write_text(create_bosc_eml(html), encoding="utf-8")

        provider = BOSCCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("3500.00")
        assert transactions[0].is_expense

    def test_amount_with_minus_sign(self, tmp_path):
        """Test parsing amounts with - sign (refund/credit)."""
        html = """<html>
<body>
<table><tr><td>对账周期：2025年12月1日-2025年12月31日</td></tr></table>
<table>
    <tr loop2="1">
        <td>2025年12月20日</td>
        <td>2025年12月20日</td>
        <td>退款-Apple Store</td>
        <td>299.00-</td>
        <td>1234</td>
    </tr>
</table>
</body></html>"""
        file_path = tmp_path / "上海银行信用卡.eml"
        file_path.write_text(create_bosc_eml(html), encoding="utf-8")

        provider = BOSCCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("-299.00")
        assert transactions[0].is_income

    def test_small_amount(self, tmp_path):
        """Test parsing small amounts."""
        html = """<html>
<body>
<table><tr><td>对账周期：2025年12月1日-2025年12月31日</td></tr></table>
<table>
    <tr loop2="1">
        <td>2025年12月25日</td>
        <td>2025年12月25日</td>
        <td>刷卡金返还</td>
        <td>0.01-</td>
        <td>1234</td>
    </tr>
</table>
</body></html>"""
        file_path = tmp_path / "上海银行信用卡.eml"
        file_path.write_text(create_bosc_eml(html), encoding="utf-8")

        provider = BOSCCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("-0.01")
