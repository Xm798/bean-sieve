"""Tests for China Guangfa Bank (CGB) credit card statement provider."""

import base64
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.providers import get_provider
from bean_sieve.providers.banks.credit.cgb import CGBCreditProvider


def create_cgb_eml(html_content: str) -> str:
    """Create an EML file content with the given HTML."""
    encoded = base64.b64encode(html_content.encode("gbk")).decode("ascii")
    return f"""From: billing@cgbchina.com.cn
Subject: =?gbk?B?usPUy9DFv6jP4M2oztLDx77J0LY=?=
To: test@example.com
MIME-Version: 1.0
Content-Type: text/html; charset="gbk"
Content-Transfer-Encoding: base64

{encoded}
"""


@pytest.fixture
def cgb_html_content():
    """Sample CGB statement HTML content."""
    return """<!DOCTYPE html>
<html>
<head><title>广发银行信用卡电子账单</title></head>
<body>
<table>
    <tr><td>感谢您使用广发银行信用卡，以下是您2026年01月的信用卡账单：</td></tr>
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
    <tr><td>2026/01/03 2026/01/03 (赠送)广发返利金 -20.00 人民币 -20.00 人民币</td></tr>
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
    file_path = tmp_path / "广发信用卡 2026年01月电子账单.eml"
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
        assert provider.provider_name == "广发银行信用卡"
        assert ".eml" in provider.supported_formats

    def test_can_handle(self):
        """Test file format detection."""
        assert CGBCreditProvider.can_handle(Path("广发信用卡 2026年01月电子账单.eml"))
        assert CGBCreditProvider.can_handle(Path("广发银行信用卡电子账单.eml"))
        assert not CGBCreditProvider.can_handle(Path("cgb_statement.csv"))
        assert not CGBCreditProvider.can_handle(Path("statement.eml"))

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
        file_path = tmp_path / "广发信用卡.eml"
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
        file_path = tmp_path / "广发信用卡.eml"
        file_path.write_text(create_cgb_eml(html), encoding="utf-8")

        provider = CGBCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("100.50")
        assert transactions[0].currency == "USD"

    def test_empty_statement(self, tmp_path):
        """Test handling of statement with no transactions."""
        html = """<html>
<body>
<table><tr><td>账单周期:2025/12/26-2026/01/25</td></tr></table>
<table><tr><td>本期无交易记录</td></tr></table>
</body></html>"""
        file_path = tmp_path / "广发信用卡.eml"
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
        file_path = tmp_path / "广发信用卡.eml"
        file_path.write_text(create_cgb_eml(html), encoding="utf-8")

        provider = CGBCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 2
        assert transactions[0].date == date(2025, 12, 28)
        assert transactions[1].date == date(2026, 1, 2)
