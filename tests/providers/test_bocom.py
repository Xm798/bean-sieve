"""Tests for Bank of Communications (BOCOM) credit card statement provider."""

import base64
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.providers import get_provider
from bean_sieve.providers.banks.credit.bocom import BOCOMCreditProvider


def create_bocom_eml(html_content: str) -> str:
    """Create an EML file content with the given HTML."""
    encoded = base64.b64encode(html_content.encode("gbk")).decode("ascii")
    return f"""From: test@bocomcc.com
Subject: =?gbk?B?vbvNqNL40NC49sjL0MXTw7+o?=
To: test@example.com
MIME-Version: 1.0
Content-Type: text/html; charset="gbk"
Content-Transfer-Encoding: base64

{encoded}
"""


@pytest.fixture
def bocom_html_content():
    """Sample BOCOM statement HTML content."""
    return """<html>
<head><title>交通银行信用卡电子账单</title></head>
<body>
<table>
    <tr><td>交通银行个人信用卡 622200******1234</td></tr>
    <tr><td>账单周期 2025/10/14-2025/11/13</td></tr>
</table>
<table>
    <tr><td>还款、退货、费用返还明细</td></tr>
    <tr>
        <td>
            <table>
                <tr>
                    <td></td>
                    <td>交易日期</td>
                    <td>记账日期</td>
                    <td>卡末四位</td>
                    <td>交易说明</td>
                    <td>交易金额</td>
                    <td>入账金额</td>
                </tr>
                <tr>
                    <td></td>
                    <td>11/04</td>
                    <td>11/04</td>
                    <td>1234</td>
                    <td>信用卡还款 转账还款-银联</td>
                    <td>CNY20000.00</td>
                    <td>CNY20000.00</td>
                </tr>
                <tr>
                    <td></td>
                    <td>11/12</td>
                    <td>11/12</td>
                    <td>1234</td>
                    <td>刷卡金返还 活动抵扣</td>
                    <td>CNY100.00</td>
                    <td>CNY100.00</td>
                </tr>
            </table>
        </td>
    </tr>
    <tr><td>消费、取现、其他费用明细</td></tr>
    <tr>
        <td>
            <table>
                <tr>
                    <td></td>
                    <td>交易日期</td>
                    <td>记账日期</td>
                    <td>卡末四位</td>
                    <td>交易说明</td>
                    <td>交易金额</td>
                    <td>入账金额</td>
                </tr>
                <tr>
                    <td></td>
                    <td>10/22</td>
                    <td>10/22</td>
                    <td>1234</td>
                    <td>消费 支付宝-测试商户</td>
                    <td>CNY1234.56</td>
                    <td>CNY1234.56</td>
                </tr>
                <tr>
                    <td></td>
                    <td>11/01</td>
                    <td>11/01</td>
                    <td>1234</td>
                    <td>消费 财付通-餐饮店</td>
                    <td>CNY99.00</td>
                    <td>CNY99.00</td>
                </tr>
            </table>
        </td>
    </tr>
</table>
</body>
</html>"""


@pytest.fixture
def bocom_eml_file(tmp_path, bocom_html_content):
    """Create a temporary BOCOM EML file."""
    file_path = tmp_path / "交通银行个人信用卡2025年11月电子账单.eml"
    eml_content = create_bocom_eml(bocom_html_content)
    file_path.write_text(eml_content, encoding="utf-8")
    return file_path


class TestBOCOMCreditProvider:
    """Tests for BOCOMCreditProvider."""

    def test_provider_registration(self):
        """Test that BOCOM provider is properly registered."""
        provider = get_provider("bocom_credit")
        assert isinstance(provider, BOCOMCreditProvider)
        assert provider.provider_id == "bocom_credit"
        assert provider.provider_name == "交通银行信用卡"
        assert ".eml" in provider.supported_formats

    def test_can_handle(self):
        """Test file format detection."""
        assert BOCOMCreditProvider.can_handle(
            Path("交通银行个人信用卡2025年11月电子账单.eml")
        )
        assert BOCOMCreditProvider.can_handle(
            Path("交通银行白金信用卡2026年01月电子账单.eml")
        )
        assert not BOCOMCreditProvider.can_handle(Path("bocom_statement.csv"))
        assert not BOCOMCreditProvider.can_handle(Path("statement.eml"))

    def test_parse_transactions(self, bocom_eml_file):
        """Test parsing transactions from EML file."""
        provider = BOCOMCreditProvider()
        transactions = provider.parse(bocom_eml_file)

        assert len(transactions) == 4

        # Check payment transactions (should be negative)
        payment1 = transactions[0]
        assert payment1.date == date(2025, 11, 4)
        assert payment1.amount == Decimal("-20000.00")
        assert payment1.currency == "CNY"
        assert payment1.card_last4 == "1234"
        assert "还款" in payment1.description
        assert payment1.provider == "bocom_credit"
        assert payment1.is_income

        payment2 = transactions[1]
        assert payment2.date == date(2025, 11, 12)
        assert payment2.amount == Decimal("-100.00")
        assert "返还" in payment2.description

        # Check spending transactions (should be positive)
        spending1 = transactions[2]
        assert spending1.date == date(2025, 10, 22)
        assert spending1.amount == Decimal("1234.56")
        assert spending1.currency == "CNY"
        assert "支付宝" in spending1.description
        assert spending1.is_expense

        spending2 = transactions[3]
        assert spending2.date == date(2025, 11, 1)
        assert spending2.amount == Decimal("99.00")
        assert "财付通" in spending2.description

    def test_metadata_extraction(self, bocom_eml_file):
        """Test that metadata is properly extracted."""
        provider = BOCOMCreditProvider()
        transactions = provider.parse(bocom_eml_file)

        txn = transactions[0]
        assert "section" in txn.metadata
        assert txn.metadata["section"] == "payment"

        spending_txn = transactions[2]
        assert spending_txn.metadata["section"] == "spending"
        assert "original_amount" not in spending_txn.metadata
        assert "original_currency" not in spending_txn.metadata

    def test_year_extraction_from_statement_cycle(self, tmp_path):
        """Test year extraction from statement cycle date."""
        html = """<html>
<body>
<table><tr><td>账单周期 2026/01/14-2026/02/13</td></tr></table>
<table>
    <tr><td>消费、取现、其他费用明细</td></tr>
    <tr><td>
        <table>
            <tr><td></td><td>交易日期</td><td>记账日期</td><td>卡末四位</td><td>交易说明</td><td>交易金额</td><td>入账金额</td></tr>
            <tr><td></td><td>01/15</td><td>01/15</td><td>1234</td><td>消费 测试</td><td>CNY100.00</td><td>CNY100.00</td></tr>
        </table>
    </td></tr>
</table>
</body></html>"""
        file_path = tmp_path / "交通银行信用卡2026年02月电子账单.eml"
        file_path.write_text(create_bocom_eml(html), encoding="utf-8")

        provider = BOCOMCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].date == date(2026, 1, 15)

    def test_row_before_a_same_year_period_takes_the_previous_year(self, tmp_path):
        """A December row on a February statement belongs to the previous year."""
        html = """<html>
<body>
<table><tr><td>账单周期 2030/01/14-2030/02/13</td></tr></table>
<table>
    <tr><td>消费、取现、其他费用明细</td></tr>
    <tr><td>
        <table>
            <tr><td></td><td>交易日期</td><td>记账日期</td><td>卡末四位</td><td>交易说明</td><td>交易金额</td><td>入账金额</td></tr>
            <tr><td></td><td>12/28</td><td>01/16</td><td>0007</td><td>消费 商户甲</td><td>CNY60.00</td><td>CNY60.00</td></tr>
        </table>
    </td></tr>
</table>
</body></html>"""
        file_path = tmp_path / "交通银行信用卡2030年02月电子账单.eml"
        file_path.write_text(create_bocom_eml(html), encoding="utf-8")

        provider = BOCOMCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].date == date(2029, 12, 28)
        assert transactions[0].post_date == date(2030, 1, 16)

    def test_row_a_year_before_a_cross_year_period_takes_the_earlier_year(
        self, tmp_path
    ):
        """A November row on a December-to-January statement is last November."""
        html = """<html>
<body>
<table><tr><td>账单周期 2029/12/14-2030/01/13</td></tr></table>
<table>
    <tr><td>消费、取现、其他费用明细</td></tr>
    <tr><td>
        <table>
            <tr><td></td><td>交易日期</td><td>记账日期</td><td>卡末四位</td><td>交易说明</td><td>交易金额</td><td>入账金额</td></tr>
            <tr><td></td><td>11/20</td><td>12/20</td><td>0007</td><td>消费 商户甲</td><td>CNY20.00</td><td>CNY20.00</td></tr>
        </table>
    </td></tr>
</table>
</body></html>"""
        file_path = tmp_path / "交通银行信用卡2030年01月电子账单.eml"
        file_path.write_text(create_bocom_eml(html), encoding="utf-8")

        provider = BOCOMCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].date == date(2029, 11, 20)
        assert transactions[0].post_date == date(2029, 12, 20)

    def test_empty_statement(self, tmp_path):
        """Test handling of statement with no transactions."""
        html = """<html>
<body>
<table><tr><td>账单周期 2025/10/14-2025/11/13</td></tr></table>
<table><tr><td>本期无交易记录</td></tr></table>
</body></html>"""
        file_path = tmp_path / "交通银行信用卡空账单.eml"
        file_path.write_text(create_bocom_eml(html), encoding="utf-8")

        provider = BOCOMCreditProvider()
        transactions = provider.parse(file_path)

        assert transactions == []


class TestBOCOMAmountParsing:
    """Tests for BOCOM amount parsing edge cases."""

    def test_amount_with_thousands_separator(self, tmp_path):
        """Test parsing amounts with comma separators."""
        html = """<html>
<body>
<table><tr><td>账单周期 2025/10/14-2025/11/13</td></tr></table>
<table>
    <tr><td>消费、取现、其他费用明细</td></tr>
    <tr><td>
        <table>
            <tr><td></td><td>交易日期</td><td>记账日期</td><td>卡末四位</td><td>交易说明</td><td>交易金额</td><td>入账金额</td></tr>
            <tr><td></td><td>10/22</td><td>10/22</td><td>1234</td><td>消费 大额</td><td>CNY12,345.67</td><td>CNY12,345.67</td></tr>
        </table>
    </td></tr>
</table>
</body></html>"""
        file_path = tmp_path / "交通银行信用卡.eml"
        file_path.write_text(create_bocom_eml(html), encoding="utf-8")

        provider = BOCOMCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("12345.67")

    def test_foreign_transaction_takes_billed_amount(self, tmp_path):
        """The 入账金额 column is the amount; 交易金额 is kept as the original."""
        html = """<html>
<body>
<table><tr><td>账单周期 2025/10/14-2025/11/13</td></tr></table>
<table>
    <tr><td>消费、取现、其他费用明细</td></tr>
    <tr><td>
        <table>
            <tr><td></td><td>交易日期</td><td>记账日期</td><td>卡末四位</td><td>交易说明</td><td>交易金额</td><td>入账金额</td></tr>
            <tr><td></td><td>10/22</td><td>10/22</td><td>1234</td><td>消费 海外</td><td>HKD41.00</td><td>CNY37.00</td></tr>
        </table>
    </td></tr>
</table>
</body></html>"""
        file_path = tmp_path / "交通银行信用卡.eml"
        file_path.write_text(create_bocom_eml(html), encoding="utf-8")

        provider = BOCOMCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        txn = transactions[0]
        assert txn.amount == Decimal("37.00")
        assert txn.currency == "CNY"
        assert txn.metadata["original_amount"] == Decimal("41.00")
        assert txn.metadata["original_currency"] == "HKD"


class TestBOCOMStatementPeriod:
    """Tests for statement period stamping."""

    def test_statement_period(self, bocom_eml_file):
        """Test that the header billing cycle is stamped on every transaction."""
        provider = BOCOMCreditProvider()
        transactions = provider.parse(bocom_eml_file)

        assert transactions
        for txn in transactions:
            assert txn.statement_period == (date(2025, 10, 14), date(2025, 11, 13))


DETAIL_LAYOUT_HTML = """<html>
<head><title>交通银行信用卡电子账单</title></head>
<body>
<table><tr><td>
    <div>交通银行信用卡 卡号:622200******0009 账单周期：2030/01/05-2030/02/04</div>
    <div>本期账务明细</div>
    <div>以下是您的还款、退货及费用返还明细</div>
    <div><table>
        <thead><tr><th>交易日期</th><th>记账日期</th><th>交易说明</th><th>交易币种/金额</th><th>入账币种/金额</th></tr></thead>
        <tbody>
            <tr><td colspan="5">人民币账户明细</td></tr>
            <tr><td colspan="5">附属卡 卡号末四位 0008</td></tr>
            <tr><td>2030-01-10</td><td>2030-01-11</td><td>退货 商户甲</td><td>CNY 10.00</td><td>CNY 10.00</td></tr>
            <tr><td>2030-02-01</td><td>2030-02-01</td><td>信用卡还款 转账还款</td><td>CNY 1,000.00</td><td>CNY 1,000.00</td></tr>
        </tbody>
    </table></div>
    <div>以下是您的消费、取现及其他费用明细</div>
    <div><table>
        <thead><tr><th>交易日期</th><th>记账日期</th><th>交易说明</th><th>交易币种/金额</th><th>入账币种/金额</th></tr></thead>
        <tbody>
            <tr><td colspan="5">人民币账户明细</td></tr>
            <tr><td>2030-01-06</td><td>2030-01-07</td><td>消费 商户乙</td><td>CNY 20.00</td><td>CNY 20.00</td></tr>
            <tr><td>2030-01-08</td><td>2030-01-08</td><td>消费 商户丙</td><td>HKD 40.00</td><td>CNY 36.00</td></tr>
        </tbody>
    </table></div>
</td></tr></table>
</body>
</html>"""


class TestBOCOMDetailLayout:
    """Tests for the layout with YYYY-MM-DD dates and 卡号末四位 card rows."""

    @pytest.fixture
    def transactions(self, tmp_path):
        file_path = tmp_path / "交通银行信用卡2030年02月电子账单.eml"
        file_path.write_text(create_bocom_eml(DETAIL_LAYOUT_HTML), encoding="utf-8")
        return BOCOMCreditProvider().parse(file_path)

    def test_parses_rows_of_both_sections(self, transactions):
        assert [
            (t.date, t.post_date, t.amount, t.description) for t in transactions
        ] == [
            (date(2030, 1, 10), date(2030, 1, 11), Decimal("-10.00"), "退货 商户甲"),
            (
                date(2030, 2, 1),
                date(2030, 2, 1),
                Decimal("-1000.00"),
                "信用卡还款 转账还款",
            ),
            (date(2030, 1, 6), date(2030, 1, 7), Decimal("20.00"), "消费 商户乙"),
            (date(2030, 1, 8), date(2030, 1, 8), Decimal("36.00"), "消费 商户丙"),
        ]
        assert [t.metadata["section"] for t in transactions] == [
            "payment",
            "payment",
            "spending",
            "spending",
        ]

    def test_card_comes_from_card_row_else_statement_card(self, transactions):
        assert [t.card_last4 for t in transactions] == ["0008", "0008", "0009", "0009"]

    def test_foreign_transaction_keeps_original_amount(self, transactions):
        txn = transactions[3]
        assert txn.currency == "CNY"
        assert txn.metadata["original_amount"] == Decimal("40.00")
        assert txn.metadata["original_currency"] == "HKD"

    def test_statement_period(self, transactions):
        assert transactions
        for txn in transactions:
            assert txn.statement_period == (date(2030, 1, 5), date(2030, 2, 4))
