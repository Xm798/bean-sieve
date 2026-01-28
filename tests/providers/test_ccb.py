"""Tests for China Construction Bank (CCB) credit card statement provider."""

import base64
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.providers import get_provider
from bean_sieve.providers.banks.credit.ccb import CCBCreditProvider


def create_ccb_eml(html_content: str) -> str:
    """Create an EML file content with the given HTML."""
    encoded = base64.b64encode(html_content.encode("utf-8")).decode("ascii")
    return f"""From: service@vip.ccb.com
Subject: =?utf-8?B?5Lit5Zu95bu66K6+6ZO26KGM5L+h55So5Y2h55S15a2Q6LSm5Y2V?=
To: test@example.com
MIME-Version: 1.0
Content-Type: text/html; charset="utf-8"
Content-Transfer-Encoding: base64

{encoded}
"""


@pytest.fixture
def ccb_html_content():
    """Sample CCB statement HTML content."""
    return """<html>
<head><title>龙卡信用卡对账单</title></head>
<body>
<table>
    <tr><td>账单周期Statement Cycle</td><td>2025/11/24-2025/12/23</td></tr>
</table>
<table>
    <tr><td>【交易明细】</td></tr>
    <tr>
        <td>交易日</td>
        <td>银行记账日</td>
        <td>卡号后四位</td>
        <td>交易描述</td>
        <td>交易币/金额</td>
        <td>结算币/金额</td>
    </tr>
    <tr>
        <td>[人民币账户] RMB Account</td>
        <td>上期账单余额(Previous Balance)</td>
        <td>14,052.90</td>
    </tr>
    <tr>
        <td>2025-11-25</td>
        <td>2025-11-25</td>
        <td>0800</td>
        <td>支付宝-测试商户</td>
        <td>CNY</td>
        <td>3,874.90</td>
        <td>CNY</td>
        <td>3,874.90</td>
    </tr>
    <tr>
        <td>2025-11-27</td>
        <td>2025-11-27</td>
        <td>0800</td>
        <td>积分兑换年费</td>
        <td>CNY</td>
        <td>-1,800.00</td>
        <td>CNY</td>
        <td>-1,800.00</td>
    </tr>
    <tr>
        <td>2025-12-04</td>
        <td>2025-12-05</td>
        <td>0800</td>
        <td>银联入账 还款</td>
        <td>CNY</td>
        <td>-12,252.90</td>
        <td>CNY</td>
        <td>-12,252.90</td>
    </tr>
    <tr>
        <td>2025-12-09</td>
        <td>2025-12-09</td>
        <td>0800</td>
        <td>财付通-餐饮消费</td>
        <td>CNY</td>
        <td>99.00</td>
        <td>CNY</td>
        <td>99.00</td>
    </tr>
</table>
</body>
</html>"""


@pytest.fixture
def ccb_eml_file(tmp_path, ccb_html_content):
    """Create a temporary CCB EML file."""
    file_path = tmp_path / "中国建设银行信用卡电子账单.eml"
    eml_content = create_ccb_eml(ccb_html_content)
    file_path.write_text(eml_content, encoding="utf-8")
    return file_path


class TestCCBCreditProvider:
    """Tests for CCBCreditProvider."""

    def test_provider_registration(self):
        """Test that CCB provider is properly registered."""
        provider = get_provider("ccb_credit")
        assert isinstance(provider, CCBCreditProvider)
        assert provider.provider_id == "ccb_credit"
        assert provider.provider_name == "建设银行信用卡"
        assert ".eml" in provider.supported_formats

    def test_can_handle(self):
        """Test file format detection."""
        assert CCBCreditProvider.can_handle(Path("中国建设银行信用卡电子账单.eml"))
        assert CCBCreditProvider.can_handle(Path("建设银行信用卡账单.eml"))
        assert not CCBCreditProvider.can_handle(Path("ccb_statement.csv"))
        assert not CCBCreditProvider.can_handle(Path("statement.eml"))

    def test_per_card_statement(self):
        """Test that per_card_statement is True for CCB."""
        provider = CCBCreditProvider()
        assert provider.per_card_statement is True

    def test_parse_transactions(self, ccb_eml_file):
        """Test parsing transactions from EML file."""
        provider = CCBCreditProvider()
        transactions = provider.parse(ccb_eml_file)

        assert len(transactions) == 4

        # Check first spending transaction
        txn1 = transactions[0]
        assert txn1.date == date(2025, 11, 25)
        assert txn1.post_date == date(2025, 11, 25)
        assert txn1.amount == Decimal("3874.90")
        assert txn1.currency == "CNY"
        assert txn1.card_last4 == "0800"
        assert "支付宝" in txn1.description
        assert txn1.provider == "ccb_credit"
        assert txn1.is_expense

        # Check negative amount transaction (fee refund)
        txn2 = transactions[1]
        assert txn2.date == date(2025, 11, 27)
        assert txn2.amount == Decimal("-1800.00")
        assert "年费" in txn2.description
        assert txn2.is_income

        # Check payment transaction
        txn3 = transactions[2]
        assert txn3.date == date(2025, 12, 4)
        assert txn3.post_date == date(2025, 12, 5)
        assert txn3.amount == Decimal("-12252.90")
        assert "还款" in txn3.description
        assert txn3.is_income

        # Check small expense
        txn4 = transactions[3]
        assert txn4.date == date(2025, 12, 9)
        assert txn4.amount == Decimal("99.00")
        assert "财付通" in txn4.description

    def test_statement_period_extraction(self, ccb_eml_file):
        """Test that statement period is properly extracted."""
        provider = CCBCreditProvider()
        transactions = provider.parse(ccb_eml_file)

        assert len(transactions) > 0
        txn = transactions[0]
        assert txn.statement_period is not None
        assert txn.statement_period[0] == date(2025, 11, 24)
        assert txn.statement_period[1] == date(2025, 12, 23)

    def test_empty_statement(self, tmp_path):
        """Test handling of statement with no transactions."""
        html = """<html>
<body>
<table><tr><td>账单周期Statement Cycle</td><td>2025/11/24-2025/12/23</td></tr></table>
<table>
    <tr><td>【交易明细】</td></tr>
    <tr><td>本期无交易记录</td></tr>
</table>
</body></html>"""
        file_path = tmp_path / "中国建设银行信用卡电子账单.eml"
        file_path.write_text(create_ccb_eml(html), encoding="utf-8")

        provider = CCBCreditProvider()
        transactions = provider.parse(file_path)

        assert transactions == []


class TestCCBAmountParsing:
    """Tests for CCB amount parsing edge cases."""

    def test_amount_with_thousands_separator(self, tmp_path):
        """Test parsing amounts with comma separators."""
        html = """<html>
<body>
<table><tr><td>账单周期Statement Cycle</td><td>2025/11/24-2025/12/23</td></tr></table>
<table>
    <tr><td>【交易明细】</td></tr>
    <tr>
        <td>2025-11-25</td>
        <td>2025-11-25</td>
        <td>0800</td>
        <td>大额消费</td>
        <td>CNY</td>
        <td>12,345.67</td>
        <td>CNY</td>
        <td>12,345.67</td>
    </tr>
</table>
</body></html>"""
        file_path = tmp_path / "中国建设银行信用卡电子账单.eml"
        file_path.write_text(create_ccb_eml(html), encoding="utf-8")

        provider = CCBCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("12345.67")

    def test_cross_year_statement_period(self, tmp_path):
        """Test statement period spanning across years."""
        html = """<html>
<body>
<table><tr><td>账单周期Statement Cycle</td><td>2025/12/24-2026/01/23</td></tr></table>
<table>
    <tr><td>【交易明细】</td></tr>
    <tr>
        <td>2025-12-26</td>
        <td>2025-12-26</td>
        <td>0800</td>
        <td>消费测试</td>
        <td>CNY</td>
        <td>100.00</td>
        <td>CNY</td>
        <td>100.00</td>
    </tr>
    <tr>
        <td>2026-01-06</td>
        <td>2026-01-07</td>
        <td>0800</td>
        <td>还款测试</td>
        <td>CNY</td>
        <td>-500.00</td>
        <td>CNY</td>
        <td>-500.00</td>
    </tr>
</table>
</body></html>"""
        file_path = tmp_path / "中国建设银行信用卡电子账单.eml"
        file_path.write_text(create_ccb_eml(html), encoding="utf-8")

        provider = CCBCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 2
        assert transactions[0].date == date(2025, 12, 26)
        assert transactions[1].date == date(2026, 1, 6)
        # Statement period should span years
        assert transactions[0].statement_period == (
            date(2025, 12, 24),
            date(2026, 1, 23),
        )


class TestCCBCrossYearDates:
    """Tests for CCB handling of dates in cross-year statements."""

    def test_dates_parsed_correctly(self, tmp_path):
        """Test that transaction dates are parsed correctly regardless of year."""
        html = """<html>
<body>
<table><tr><td>账单周期Statement Cycle</td><td>2025/12/24-2026/01/23</td></tr></table>
<table>
    <tr><td>【交易明细】</td></tr>
    <tr>
        <td>2025-12-31</td>
        <td>2025-12-31</td>
        <td>0800</td>
        <td>跨年消费</td>
        <td>CNY</td>
        <td>888.00</td>
        <td>CNY</td>
        <td>888.00</td>
    </tr>
    <tr>
        <td>2026-01-01</td>
        <td>2026-01-01</td>
        <td>0800</td>
        <td>新年消费</td>
        <td>CNY</td>
        <td>666.00</td>
        <td>CNY</td>
        <td>666.00</td>
    </tr>
</table>
</body></html>"""
        file_path = tmp_path / "中国建设银行信用卡电子账单.eml"
        file_path.write_text(create_ccb_eml(html), encoding="utf-8")

        provider = CCBCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 2
        # CCB provides full dates (YYYY-MM-DD), so parsing is straightforward
        assert transactions[0].date == date(2025, 12, 31)
        assert transactions[1].date == date(2026, 1, 1)
