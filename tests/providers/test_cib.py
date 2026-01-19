"""Tests for Industrial Bank (CIB) credit card statement provider."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.providers import get_provider
from bean_sieve.providers.banks.credit.cib import CIBCreditProvider


def create_cib_eml(html_content: str) -> str:
    """Create an EML file content with the given HTML."""
    return f"""From: creditcard@message.cib.com.cn
Subject: =?utf-8?B?5YW05Lia6ZO26KGM5L+h55So5Y2hMjAyNuW5tDAxJeaciOeUteWtkOi0puWNlQ==?=
To: test@example.com
Content-Type: text/html; charset="utf-8"

{html_content}
"""


@pytest.fixture
def cib_html_content():
    """Sample CIB statement HTML content."""
    return """<html>
<head><title>兴业银行信用卡对账单</title></head>
<body>
<table>
    <tr><td>卡片名称：<span id="card_name">测试白金卡</span></td></tr>
    <tr><td>卡号末四位：<span id="cardno_last4">1983</span></td></tr>
    <tr><td>账单月份：<span id="period">2026年01月</span></td></tr>
</table>
<table id="detail_table_156" width="100%">
    <tbody>
        <tr id="detail_table_head_156" style="background:#B4CBEB;">
            <td>交易日期<br>Trans Date</td>
            <td>记账日期<br>Post Date</td>
            <td>交易摘要<br>Trans Description</td>
            <td>交易地金额<br>Trans Amount</td>
            <td>记账币金额<br>Amount(RMB)</td>
        </tr>
        <td colspan="20" name="masterMsg">**** 本卡明细(卡号末四位 1983) ****</td>
        <tr id="detail_tr_156">
            <td>&nbsp;<span id="detail_tdate_156">2025-12-18</span></td>
            <td><span id="detail_adate_156">2025-12-18</span></td>
            <td><span id="detail_desc1_156">支付宝快捷--测试商户</span></td>
            <td align="right"></td>
            <td align="right"><span id="detail_tamt_156">15.84</span></td>
        </tr>
        <tr id="detail_tr_156">
            <td>&nbsp;<span id="detail_tdate_156">2025-12-20</span></td>
            <td><span id="detail_adate_156">2025-12-20</span></td>
            <td><span id="detail_desc1_156">支付宝还款</span></td>
            <td align="right"></td>
            <td align="right"><span id="detail_tamt_156">-599.73</span></td>
        </tr>
        <tr id="detail_tr_156">
            <td>&nbsp;<span id="detail_tdate_156">2025-12-22</span></td>
            <td><span id="detail_adate_156">2025-12-22</span></td>
            <td><span id="detail_desc1_156">兴业生活平台活动刷卡金</span></td>
            <td align="right"></td>
            <td align="right"><span id="detail_tamt_156">-0.01</span></td>
        </tr>
        <tr id="detail_tr_156">
            <td>&nbsp;<span id="detail_tdate_156">2026-01-16</span></td>
            <td><span id="detail_adate_156">2026-01-16</span></td>
            <td><span id="detail_desc1_156">抖音支付快捷--餐饮店</span></td>
            <td align="right"></td>
            <td align="right"><span id="detail_tamt_156">24.50</span></td>
        </tr>
    </tbody>
</table>
</body>
</html>"""


@pytest.fixture
def cib_eml_file(tmp_path, cib_html_content):
    """Create a temporary CIB EML file."""
    file_path = tmp_path / "兴业银行信用卡2026年01月电子账单.eml"
    eml_content = create_cib_eml(cib_html_content)
    file_path.write_text(eml_content, encoding="utf-8")
    return file_path


class TestCIBCreditProvider:
    """Tests for CIBCreditProvider."""

    def test_provider_registration(self):
        """Test that CIB provider is properly registered."""
        provider = get_provider("cib_credit")
        assert isinstance(provider, CIBCreditProvider)
        assert provider.provider_id == "cib_credit"
        assert provider.provider_name == "兴业银行信用卡"
        assert ".eml" in provider.supported_formats

    def test_can_handle(self):
        """Test file format detection."""
        assert CIBCreditProvider.can_handle(
            Path("兴业银行信用卡2026年01月电子账单.eml")
        )
        assert CIBCreditProvider.can_handle(Path("兴业信用卡账单.eml"))
        assert not CIBCreditProvider.can_handle(Path("cib_statement.csv"))
        assert not CIBCreditProvider.can_handle(Path("statement.eml"))

    def test_parse_transactions(self, cib_eml_file):
        """Test parsing transactions from EML file."""
        provider = CIBCreditProvider()
        transactions = provider.parse(cib_eml_file)

        assert len(transactions) == 4

        # Check expense transaction
        expense1 = transactions[0]
        assert expense1.date == date(2025, 12, 18)
        assert expense1.amount == Decimal("15.84")
        assert expense1.currency == "CNY"
        assert expense1.card_last4 == "1983"
        assert "支付宝" in expense1.description
        assert expense1.provider == "cib_credit"
        assert expense1.is_expense

        # Check payment transaction (negative amount)
        payment = transactions[1]
        assert payment.date == date(2025, 12, 20)
        assert payment.amount == Decimal("-599.73")
        assert "还款" in payment.description
        assert payment.is_income

        # Check rebate transaction (negative small amount)
        rebate = transactions[2]
        assert rebate.date == date(2025, 12, 22)
        assert rebate.amount == Decimal("-0.01")
        assert "刷卡金" in rebate.description
        assert rebate.is_income

        # Check cross-year transaction
        expense2 = transactions[3]
        assert expense2.date == date(2026, 1, 16)
        assert expense2.amount == Decimal("24.50")
        assert "抖音" in expense2.description

    def test_card_last4_extraction(self, cib_eml_file):
        """Test that card_last4 is properly extracted."""
        provider = CIBCreditProvider()
        transactions = provider.parse(cib_eml_file)

        for txn in transactions:
            assert txn.card_last4 == "1983"

    def test_post_date_extraction(self, cib_eml_file):
        """Test that post_date is properly extracted."""
        provider = CIBCreditProvider()
        transactions = provider.parse(cib_eml_file)

        txn = transactions[0]
        assert txn.post_date == date(2025, 12, 18)

    def test_empty_statement(self, tmp_path):
        """Test handling of statement with no transactions."""
        html = """<html>
<body>
<table id="detail_table_156">
    <tbody>
        <tr id="detail_table_head_156">
            <td>交易日期</td><td>记账日期</td><td>交易摘要</td><td>金额</td>
        </tr>
    </tbody>
</table>
</body></html>"""
        file_path = tmp_path / "兴业银行信用卡空账单.eml"
        file_path.write_text(create_cib_eml(html), encoding="utf-8")

        provider = CIBCreditProvider()
        transactions = provider.parse(file_path)

        assert transactions == []

    def test_per_card_statement_flag(self):
        """Test that per_card_statement is set correctly."""
        provider = CIBCreditProvider()
        assert provider.per_card_statement is True


class TestCIBAmountParsing:
    """Tests for CIB amount parsing edge cases."""

    def test_amount_with_thousands_separator(self, tmp_path):
        """Test parsing amounts with comma separators."""
        html = """<html>
<body>
<table id="detail_table_156">
    <tbody>
        <td name="masterMsg">**** 本卡明细(卡号末四位 1234) ****</td>
        <tr id="detail_tr_156">
            <td><span id="detail_tdate_156">2025-12-18</span></td>
            <td><span id="detail_adate_156">2025-12-18</span></td>
            <td><span id="detail_desc1_156">大额消费</span></td>
            <td></td>
            <td><span id="detail_tamt_156">12,345.67</span></td>
        </tr>
    </tbody>
</table>
</body></html>"""
        file_path = tmp_path / "兴业银行信用卡.eml"
        file_path.write_text(create_cib_eml(html), encoding="utf-8")

        provider = CIBCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("12345.67")

    def test_negative_amount(self, tmp_path):
        """Test parsing negative amounts (payments/refunds)."""
        html = """<html>
<body>
<table id="detail_table_156">
    <tbody>
        <td name="masterMsg">**** 本卡明细(卡号末四位 5678) ****</td>
        <tr id="detail_tr_156">
            <td><span id="detail_tdate_156">2025-12-20</span></td>
            <td><span id="detail_adate_156">2025-12-20</span></td>
            <td><span id="detail_desc1_156">还款</span></td>
            <td></td>
            <td><span id="detail_tamt_156">-1,000.00</span></td>
        </tr>
    </tbody>
</table>
</body></html>"""
        file_path = tmp_path / "兴业银行信用卡.eml"
        file_path.write_text(create_cib_eml(html), encoding="utf-8")

        provider = CIBCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("-1000.00")
        assert transactions[0].is_income


class TestCIBMultipleCards:
    """Tests for statements with multiple cards."""

    def test_multiple_card_sections(self, tmp_path):
        """Test parsing statement with transactions from multiple cards."""
        html = """<html>
<body>
<table id="detail_table_156">
    <tbody>
        <td name="masterMsg">**** 本卡明细(卡号末四位 1111) ****</td>
        <tr id="detail_tr_156">
            <td><span id="detail_tdate_156">2025-12-18</span></td>
            <td><span id="detail_adate_156">2025-12-18</span></td>
            <td><span id="detail_desc1_156">卡1消费</span></td>
            <td></td>
            <td><span id="detail_tamt_156">100.00</span></td>
        </tr>
    </tbody>
</table>
<table id="detail_table_157">
    <tbody>
        <td name="masterMsg">**** 本卡明细(卡号末四位 2222) ****</td>
        <tr id="detail_tr_157">
            <td><span id="detail_tdate_157">2025-12-19</span></td>
            <td><span id="detail_adate_157">2025-12-19</span></td>
            <td><span id="detail_desc1_157">卡2消费</span></td>
            <td></td>
            <td><span id="detail_tamt_157">200.00</span></td>
        </tr>
    </tbody>
</table>
</body></html>"""
        file_path = tmp_path / "兴业银行信用卡.eml"
        file_path.write_text(create_cib_eml(html), encoding="utf-8")

        provider = CIBCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 2
        assert transactions[0].card_last4 == "1111"
        assert transactions[0].amount == Decimal("100.00")
        assert transactions[1].card_last4 == "2222"
        assert transactions[1].amount == Decimal("200.00")
