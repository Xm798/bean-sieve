"""Tests for Huaxia Bank (HXB) credit card statement provider."""

import base64
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.providers import get_provider
from bean_sieve.providers.banks.credit.hxb import HXBCreditProvider


def create_hxb_eml(html_content: str) -> str:
    """Create an EML file content with the given HTML (base64 encoded)."""
    encoded = base64.b64encode(html_content.encode("utf-8")).decode("ascii")
    return f"""From: creditcard@hxb.com.cn
Subject: =?utf-8?B?5Y2O5aSP5L+h55So5Y2h5a+56LSm5Y2V?=
To: test@example.com
MIME-Version: 1.0
Content-Type: text/html; charset="utf-8"
Content-Transfer-Encoding: base64

{encoded}
"""


@pytest.fixture
def hxb_html_content():
    """Sample HXB statement HTML content.

    HXB uses a text-based format after HTML stripping. The parser looks for:
    - "交易日" marker to start parsing
    - MM/DD date format
    - 4-digit card number
    - Amount with ￥ or ＄ prefix
    """
    return """<html>
<head><title>华夏信用卡对账单</title></head>
<body>
<div>账单周期：2025/11/01-2025/11/30</div>
<table>
<tr><td>交易日</td></tr>
<tr><td>记账日</td></tr>
<tr><td>交易描述</td></tr>
<tr><td>卡号后四位</td></tr>
<tr><td>金额</td></tr>
</table>
<table>
<tr><td>11/05</td></tr>
<tr><td>11/05</td></tr>
<tr><td>McDonald's Beijing</td></tr>
<tr><td>1234</td></tr>
<tr><td>￥45.00</td></tr>
</table>
<table>
<tr><td>11/10</td></tr>
<tr><td>11/10</td></tr>
<tr><td>Apple Store Online</td></tr>
<tr><td>1234</td></tr>
<tr><td>￥999.00</td></tr>
</table>
<table>
<tr><td>11/15</td></tr>
<tr><td>11/15</td></tr>
<tr><td>Hilton Hotel Shanghai</td></tr>
<tr><td>1234</td></tr>
<tr><td>￥1,280.00</td></tr>
</table>
<table>
<tr><td>11/20</td></tr>
<tr><td>11/20</td></tr>
<tr><td>退款-电商平台</td></tr>
<tr><td>1234</td></tr>
<tr><td>-￥100.00</td></tr>
</table>
<div>美元账务信息</div>
</body>
</html>"""


@pytest.fixture
def hxb_eml_file(tmp_path, hxb_html_content):
    """Create a temporary HXB EML file."""
    file_path = tmp_path / "华夏信用卡-电子账单2025年11月.eml"
    eml_content = create_hxb_eml(hxb_html_content)
    file_path.write_text(eml_content, encoding="utf-8")
    return file_path


class TestHXBCreditProvider:
    """Tests for HXBCreditProvider."""

    def test_provider_registration(self):
        """Test that HXB provider is properly registered."""
        provider = get_provider("hxb_credit")
        assert isinstance(provider, HXBCreditProvider)
        assert provider.provider_id == "hxb_credit"
        assert provider.provider_name == "华夏银行信用卡"
        assert ".eml" in provider.supported_formats

    def test_can_handle(self):
        """Test file format detection."""
        assert HXBCreditProvider.can_handle(Path("华夏信用卡-电子账单2025年11月.eml"))
        assert HXBCreditProvider.can_handle(Path("华夏信用卡账单.eml"))
        assert not HXBCreditProvider.can_handle(Path("hxb_statement.csv"))
        assert not HXBCreditProvider.can_handle(Path("statement.eml"))

    def test_parse_transactions(self, hxb_eml_file):
        """Test parsing transactions from EML file."""
        provider = HXBCreditProvider()
        transactions = provider.parse(hxb_eml_file)

        assert len(transactions) == 4

        # Check expense transaction
        txn1 = transactions[0]
        assert txn1.date == date(2025, 11, 5)
        assert txn1.amount == Decimal("45.00")
        assert txn1.currency == "CNY"
        assert txn1.card_last4 == "1234"
        assert "McDonald's" in txn1.description
        assert txn1.provider == "hxb_credit"
        assert txn1.is_expense

        txn2 = transactions[1]
        assert txn2.date == date(2025, 11, 10)
        assert txn2.amount == Decimal("999.00")
        assert "Apple" in txn2.description

        # Check amount with thousands separator
        txn3 = transactions[2]
        assert txn3.date == date(2025, 11, 15)
        assert txn3.amount == Decimal("1280.00")
        assert "Hilton" in txn3.description

        # Check refund transaction (negative)
        txn4 = transactions[3]
        assert txn4.date == date(2025, 11, 20)
        assert txn4.amount == Decimal("-100.00")
        assert "退款" in txn4.description
        assert txn4.is_income

    def test_statement_period_extraction(self, hxb_eml_file):
        """Test that statement period is properly extracted."""
        provider = HXBCreditProvider()
        transactions = provider.parse(hxb_eml_file)

        assert len(transactions) > 0
        txn = transactions[0]
        assert txn.statement_period == (date(2025, 11, 1), date(2025, 11, 30))

    def test_year_extraction_from_filename(self, tmp_path):
        """Test year extraction from filename."""
        html = """<html><body>
<table><tr><td>交易日</td></tr></table>
<table>
<tr><td>12/25</td></tr>
<tr><td>12/25</td></tr>
<tr><td>Google Cloud</td></tr>
<tr><td>5678</td></tr>
<tr><td>￥100.00</td></tr>
</table>
<div>美元账务信息</div>
</body></html>"""
        file_path = tmp_path / "华夏信用卡-电子账单2026年01月.eml"
        file_path.write_text(create_hxb_eml(html), encoding="utf-8")

        provider = HXBCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        # Year should be extracted from filename (2026)
        assert transactions[0].date == date(2026, 12, 25)

    def test_card_last4_extraction(self, hxb_eml_file):
        """Test that card_last4 is properly extracted."""
        provider = HXBCreditProvider()
        transactions = provider.parse(hxb_eml_file)

        for txn in transactions:
            assert txn.card_last4 == "1234"

    def test_empty_statement(self, tmp_path):
        """Test handling of statement with no transactions."""
        html = """<html>
<body>
<table><tr><td>交易日</td></tr></table>
<div>美元账务信息</div>
</body></html>"""
        file_path = tmp_path / "华夏信用卡空账单2025年11月.eml"
        file_path.write_text(create_hxb_eml(html), encoding="utf-8")

        provider = HXBCreditProvider()
        transactions = provider.parse(file_path)

        assert transactions == []


class TestHXBDateParsing:
    """Tests for HXB date parsing edge cases."""

    def test_mm_dd_format(self, tmp_path):
        """Test parsing MM/DD date format."""
        html = """<html><body>
<table><tr><td>交易日</td></tr></table>
<table>
<tr><td>01/15</td></tr>
<tr><td>01/16</td></tr>
<tr><td>Amazon Purchase</td></tr>
<tr><td>9999</td></tr>
<tr><td>￥88.88</td></tr>
</table>
<div>美元账务信息</div>
</body></html>"""
        file_path = tmp_path / "华夏信用卡-电子账单2025年01月.eml"
        file_path.write_text(create_hxb_eml(html), encoding="utf-8")

        provider = HXBCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].date == date(2025, 1, 15)

    def test_cross_year_transaction(self, tmp_path):
        """Test transactions that cross year boundary."""
        # Statement is for January 2026, but has transactions from Dec 2025
        html = """<html><body>
<div>账单周期：2025/12/01-2025/12/31</div>
<table><tr><td>交易日</td></tr></table>
<table>
<tr><td>12/28</td></tr>
<tr><td>12/28</td></tr>
<tr><td>Year End Sale</td></tr>
<tr><td>1234</td></tr>
<tr><td>￥500.00</td></tr>
</table>
<div>美元账务信息</div>
</body></html>"""
        file_path = tmp_path / "华夏信用卡-电子账单2025年12月.eml"
        file_path.write_text(create_hxb_eml(html), encoding="utf-8")

        provider = HXBCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].date == date(2025, 12, 28)


class TestHXBAmountParsing:
    """Tests for HXB amount parsing edge cases."""

    def test_cny_amount(self, tmp_path):
        """Test parsing CNY amounts with ￥ symbol."""
        html = """<html><body>
<table><tr><td>交易日</td></tr></table>
<table>
<tr><td>11/15</td></tr>
<tr><td>11/15</td></tr>
<tr><td>Marriott International</td></tr>
<tr><td>1234</td></tr>
<tr><td>￥3,500.00</td></tr>
</table>
<div>美元账务信息</div>
</body></html>"""
        file_path = tmp_path / "华夏信用卡-电子账单2025年11月.eml"
        file_path.write_text(create_hxb_eml(html), encoding="utf-8")

        provider = HXBCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("3500.00")
        assert transactions[0].currency == "CNY"

    def test_usd_amount(self, tmp_path):
        """Test parsing USD amounts with ＄ symbol."""
        html = """<html><body>
<table><tr><td>交易日</td></tr></table>
<table>
<tr><td>11/20</td></tr>
<tr><td>11/20</td></tr>
<tr><td>Amazon.com USA</td></tr>
<tr><td>1234</td></tr>
<tr><td>＄99.99</td></tr>
</table>
<div>美元账务信息</div>
</body></html>"""
        file_path = tmp_path / "华夏信用卡-电子账单2025年11月.eml"
        file_path.write_text(create_hxb_eml(html), encoding="utf-8")

        provider = HXBCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("99.99")
        assert transactions[0].currency == "USD"

    def test_negative_amount_refund(self, tmp_path):
        """Test parsing negative amounts (refunds)."""
        html = """<html><body>
<table><tr><td>交易日</td></tr></table>
<table>
<tr><td>11/25</td></tr>
<tr><td>11/25</td></tr>
<tr><td>退款-Apple Store</td></tr>
<tr><td>1234</td></tr>
<tr><td>-￥299.00</td></tr>
</table>
<div>美元账务信息</div>
</body></html>"""
        file_path = tmp_path / "华夏信用卡-电子账单2025年11月.eml"
        file_path.write_text(create_hxb_eml(html), encoding="utf-8")

        provider = HXBCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("-299.00")
        assert transactions[0].is_income


class TestHXBStatementPeriod:
    """Tests for HXB statement period extraction."""

    def test_period_from_html_slash_format(self, tmp_path):
        """Test extracting period from HTML with YYYY/MM/DD format."""
        html = """<html><body>
<div>账单周期：2025/11/01-2025/11/30</div>
<table><tr><td>交易日</td></tr></table>
<table>
<tr><td>11/15</td></tr>
<tr><td>11/15</td></tr>
<tr><td>Test</td></tr>
<tr><td>1234</td></tr>
<tr><td>￥100.00</td></tr>
</table>
<div>美元账务信息</div>
</body></html>"""
        file_path = tmp_path / "华夏信用卡-电子账单2025年11月.eml"
        file_path.write_text(create_hxb_eml(html), encoding="utf-8")

        provider = HXBCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].statement_period == (
            date(2025, 11, 1),
            date(2025, 11, 30),
        )

    def test_period_from_filename_fallback(self, tmp_path):
        """Test fallback to filename when period not in HTML."""
        html = """<html><body>
<table><tr><td>交易日</td></tr></table>
<table>
<tr><td>11/15</td></tr>
<tr><td>11/15</td></tr>
<tr><td>Test</td></tr>
<tr><td>1234</td></tr>
<tr><td>￥100.00</td></tr>
</table>
<div>美元账务信息</div>
</body></html>"""
        file_path = tmp_path / "华夏信用卡-电子账单2025年12月.eml"
        file_path.write_text(create_hxb_eml(html), encoding="utf-8")

        provider = HXBCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        # Should fall back to full month from filename
        assert transactions[0].statement_period == (
            date(2025, 12, 1),
            date(2025, 12, 31),
        )
