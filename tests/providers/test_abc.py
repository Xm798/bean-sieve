"""Tests for Agricultural Bank of China (ABC) credit card statement provider."""

import base64
import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.config.schema import Config, ProviderConfig
from bean_sieve.core.types import (
    MatchResult,
    ReconcileContext,
    ReconcileResult,
)
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
    <tr><td>-2324.00</td></tr>
</table>
<table>
    <tr><td><span>本期账单金额</span></td></tr>
</table>
<table>
    <tr><td>2324.00</td></tr>
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

    def test_can_handle_detects_encoded_eml_content(self, tmp_path, abc_html_content):
        """A renamed statement is detected from its base64-encoded body."""
        file_path = tmp_path / "statement.eml"
        file_path.write_text(create_abc_eml(abc_html_content), encoding="utf-8")

        assert ABCCreditProvider.can_handle(file_path)

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

    def test_supplementary_card_row(self, tmp_path):
        """附卡 rows carry a 主/附 suffix on the card field (e.g. '5678附').

        Such rows must still parse, with card_last4 reduced to the 4 digits.
        """
        html = """<html>
<body>
<table><tr><td><span>2030/01/01-2030/01/31</span></td></tr></table>
<table>
    <tr>
        <td>300102</td>
        <td>300102</td>
        <td>5678附</td>
        <td>网上消费 财付通，payee-a</td>
        <td>-0.01/CNY</td>
        <td>-0.01/CNY</td>
    </tr>
    <tr>
        <td>300103</td>
        <td>300103</td>
        <td>1234主</td>
        <td>网上消费 支付宝，payee-b</td>
        <td>-20.00/CNY</td>
        <td>-20.00/CNY</td>
    </tr>
</table>
</body></html>"""
        file_path = tmp_path / "农业银行金穗信用卡.eml"
        file_path.write_text(create_abc_eml(html), encoding="utf-8")

        provider = ABCCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 2
        assert transactions[0].card_last4 == "5678"
        assert transactions[0].amount == Decimal("0.01")
        assert transactions[1].card_last4 == "1234"
        assert transactions[1].amount == Decimal("20.00")

    def test_repayment_row_without_card_number(self, tmp_path):
        """约定还款 rows leave the 卡号后四位 cell empty (account-level repayment).

        Such rows must still parse, falling back to the statement's own card.
        """
        html = """<html>
<body>
<table>
    <tr><td><span>620000******1234</span></td></tr>
    <tr><td><span>2030/01/01-2030/01/31</span></td></tr>
</table>
<table>
    <tr>
        <td>300110</td>
        <td>300110</td>
        <td>1234</td>
        <td>desc-a</td>
        <td>33.33/CNY</td>
        <td>33.33/CNY</td>
    </tr>
    <tr>
        <td>300113</td>
        <td>300113</td>
        <td></td>
        <td>约定还款</td>
        <td>22.22/CNY</td>
        <td>22.22/CNY</td>
    </tr>
    <tr>
        <td>300115</td>
        <td>300115</td>
        <td>1234</td>
        <td>desc-b</td>
        <td>-11.11/CNY</td>
        <td>-11.11/CNY</td>
    </tr>
</table>
</body></html>"""
        file_path = tmp_path / "农业银行金穗信用卡.eml"
        file_path.write_text(create_abc_eml(html), encoding="utf-8")

        provider = ABCCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 3
        repayment = transactions[1]
        assert repayment.date == date(2030, 1, 13)
        assert repayment.amount == Decimal("-22.22")
        assert repayment.card_last4 == "1234"
        assert repayment.description == "约定还款"
        assert repayment.is_income

    def test_repayment_row_without_statement_card(self, tmp_path):
        """A card-less row still parses when the statement card is unavailable."""
        html = """<html>
<body>
<table><tr><td><span>2030/01/01-2030/01/31</span></td></tr></table>
<table>
    <tr>
        <td>300113</td>
        <td>300113</td>
        <td></td>
        <td>约定还款</td>
        <td>22.22/CNY</td>
        <td>22.22/CNY</td>
    </tr>
</table>
</body></html>"""
        file_path = tmp_path / "农业银行金穗信用卡.eml"
        file_path.write_text(create_abc_eml(html), encoding="utf-8")

        provider = ABCCreditProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].card_last4 is None
        assert transactions[0].amount == Decimal("-22.22")

    def test_malformed_card_cell_is_warned(self, tmp_path, caplog):
        """A row with an unrecognisable card field is dropped, but never silently."""
        html = """<html>
<body>
<table><tr><td><span>2030/01/01-2030/01/31</span></td></tr></table>
<table>
    <tr>
        <td>300113</td>
        <td>300113</td>
        <td>not-a-card</td>
        <td>desc-a</td>
        <td>-44.44/CNY</td>
        <td>-44.44/CNY</td>
    </tr>
</table>
</body></html>"""
        file_path = tmp_path / "农业银行金穗信用卡.eml"
        file_path.write_text(create_abc_eml(html), encoding="utf-8")

        provider = ABCCreditProvider()
        with caplog.at_level(logging.WARNING):
            transactions = provider.parse(file_path)

        assert transactions == []
        assert "not-a-card" in caplog.text

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


REBATE_HTML = """<html>
<head><title>金穗信用卡电子对账单</title></head>
<body>
<table>
    <tr><td><span>620000******1234</span></td></tr>
    <tr><td><span>2030/01/01-2030/01/31</span></td></tr>
    <tr><td><span>本期应还款额</span></td></tr>
    <tr><td>-25.00</td></tr>
    <tr><td><span>本期使用刷卡金</span></td><td>5.00</td></tr>
</table>
<table>
    <tr><td><span>本期账单金额</span></td></tr>
</table>
<table>
    <tr><td>30.00</td></tr>
</table>
<table>
    <tr>
        <td>300102</td>
        <td>300102</td>
        <td>1234</td>
        <td>payee-a</td>
        <td>-10.00/CNY</td>
        <td>-10.00/CNY</td>
    </tr>
    <tr>
        <td>300103</td>
        <td>300103</td>
        <td>1234</td>
        <td>payee-b</td>
        <td>-20.00/CNY</td>
        <td>-20.00/CNY</td>
    </tr>
</table>
</body>
</html>"""


def formula_rebate_html(rebate_used: str, adjustment: str) -> str:
    """Statement laid out like the real 账务说明 formula and 刷卡金统计 tables."""
    return f"""<html>
<head><title>金穗信用卡电子对账单</title></head>
<body>
<table>
    <tr><td><span>620000******1234</span></td></tr>
    <tr><td><span>2030/01/01-2030/01/31</span></td></tr>
    <tr><td><span>本期应还款额</span></td></tr>
    <tr><td>-27.00</td></tr>
</table>
<div>
<table>
<tr>
<td><span>币种</span><span>Curr</span></td>
<td><span>本期应还金额</span><span>New Balance</span></td>
<td><span>-</span></td>
<td><span>本期账户&#xA;溢缴款</span><span>Deposit</span></td>
<td><span>=</span></td>
<td><span>上期账单&#xA;应还金额</span><span>Previous &#xA;Balance</span></td>
<td><span>-</span></td>
<td><span>上期账户&#xA;溢缴款</span><span>Previous &#xA;Deposit</span></td>
<td><span>+</span></td>
<td><span>本期账单金额</span><span>New Charge</span></td>
<td><span>-</span></td>
<td><span>本期还款、退货金额</span><span>Payments</span></td>
<td><span>-</span></td>
<td><span>本期调整金额</span><span>Adjustment</span></td>
</tr>
</table>
<table>
<tr>
<td><span>人民币(CNY)</span></td>
<td><span>27.00</span></td>
<td><span>0.00</span></td>
<td><span>100.00</span></td>
<td><span>0.00</span></td>
<td><span>30.00</span></td>
<td><span>100.00</span></td>
<td><span>{adjustment}</span></td>
</tr>
</table>
</div>
<table>
    <tr>
        <td>300102</td>
        <td>300102</td>
        <td>1234</td>
        <td>payee-a</td>
        <td>-10.00/CNY</td>
        <td>-10.00/CNY</td>
    </tr>
    <tr>
        <td>300103</td>
        <td>300103</td>
        <td>1234</td>
        <td>payee-b</td>
        <td>-20.00/CNY</td>
        <td>-20.00/CNY</td>
    </tr>
</table>
<table>
<tr><td><span>可用刷卡金余额</span></td><td><span>0.00</span></td></tr>
<tr><td><span>本期使用刷卡金</span></td><td><span>{rebate_used}</span></td></tr>
</table>
</body>
</html>"""


EXPECTED_BALANCED_REPORT = """
; ============================================================
; 农业银行信用卡 账单核对
; ============================================================
;
; 卡号: 620000******1234 (尾号 1234)
; 账单周期: 2025/10/24-2025/11/23
;
;   解析消费:            2324.00 CNY
;   账单消费:            2324.00 CNY
;   账单应还:            2324.00 CNY
;
;   状态: ✅ 平账
; ============================================================
"""


EXPECTED_REBATE_OUTPUT = """; base

; --- 刷卡金抵扣 ---
2030-01-31 * "农业银行" "刷卡金抵扣 (尾号1234)"
  Liabilities:Credit:ABC:1234  5.00 CNY
  Income:Rebate:ABC
; ============================================================
; 农业银行信用卡 账单核对
; ============================================================
;
; 卡号: 620000******1234 (尾号 1234)
; 账单周期: 2030/01/01-2030/01/31
;
;   解析消费:              30.00 CNY
;   账单消费:              30.00 CNY
;   账单应还:              25.00 CNY
;   刷卡金抵扣:             5.00 CNY
;
;   状态: ✅ 平账 (刷卡金 5.00)
; ============================================================
"""


def empty_result() -> ReconcileResult:
    return ReconcileResult(
        match_result=MatchResult(matched=[], missing=[], extra=[]),
        processed=[],
    )


class TestABCPostOutput:
    """Byte-level golden tests for the settlement report appended by post_output."""

    def test_post_output_golden_balanced(self, abc_eml_file):
        provider = ABCCreditProvider()

        output = provider.post_output(
            "",
            empty_result(),
            ReconcileContext(
                statement_paths=[abc_eml_file],
                provider_ids={abc_eml_file: "abc_credit"},
            ),
        )

        assert output == EXPECTED_BALANCED_REPORT

    def test_post_output_ignores_date_range(self, abc_eml_file):
        """The report counts the whole statement, not the reconciled slice.

        解析消费 is compared against the statement's own 本期账单金额, so it must
        stay the raw parse even when the run only reconciles part of the cycle.
        """
        provider = ABCCreditProvider()

        output = provider.post_output(
            "",
            empty_result(),
            ReconcileContext(
                statement_paths=[abc_eml_file],
                provider_ids={abc_eml_file: "abc_credit"},
                date_range=(date(2030, 1, 1), date(2030, 1, 1)),
            ),
        )

        assert output == EXPECTED_BALANCED_REPORT

    def test_post_output_golden_rebate_entry(self, tmp_path):
        file_path = tmp_path / "农业银行金穗信用卡刷卡金.eml"
        file_path.write_text(create_abc_eml(REBATE_HTML), encoding="utf-8")
        context = ReconcileContext(
            statement_paths=[file_path],
            provider_ids={file_path: "abc_credit"},
            config=Config(
                providers={
                    "abc_credit": ProviderConfig(
                        accounts={"1234": "Liabilities:Credit:ABC:1234"},
                        rebate_income_account="Income:Rebate:ABC",
                    )
                }
            ),
        )

        output = ABCCreditProvider().post_output("; base\n", empty_result(), context)

        assert output == EXPECTED_REBATE_OUTPUT

    def test_post_output_reports_files_assigned_to_it(self, tmp_path, abc_html_content):
        """A statement the run assigned to ABC is reported whatever its name."""
        file_path = tmp_path / "statement.eml"
        file_path.write_text(create_abc_eml(abc_html_content), encoding="utf-8")
        other_path = tmp_path / "农业银行-other.eml"
        other_html = abc_html_content.replace("1234", "5678")
        other_path.write_text(create_abc_eml(other_html), encoding="utf-8")
        context = ReconcileContext(
            statement_paths=[file_path, other_path],
            provider_ids={file_path: "abc_credit", other_path: "other"},
        )

        output = ABCCreditProvider().post_output("", empty_result(), context)

        assert "尾号 1234" in output
        assert "5678" not in output


class TestABCRebateAdjustment:
    """The rebate entry books 本期调整金额, the net that reduced the debt."""

    @staticmethod
    def run(tmp_path, rebate_used: str, adjustment: str) -> str:
        file_path = tmp_path / "农业银行金穗信用卡刷卡金.eml"
        html = formula_rebate_html(rebate_used, adjustment)
        file_path.write_text(create_abc_eml(html), encoding="utf-8")
        context = ReconcileContext(
            statement_paths=[file_path],
            provider_ids={file_path: "abc_credit"},
            config=Config(
                providers={
                    "abc_credit": ProviderConfig(
                        accounts={"1234": "Liabilities:Credit:ABC:1234"},
                        rebate_income_account="Income:Rebate:ABC",
                    )
                }
            ),
        )
        return ABCCreditProvider().post_output("", empty_result(), context)

    def test_summary_reads_formula_fields(self, tmp_path):
        file_path = tmp_path / "农业银行金穗信用卡.eml"
        file_path.write_text(
            create_abc_eml(formula_rebate_html("5.00", "3.00")), encoding="utf-8"
        )

        summary = ABCCreditProvider()._extract_summary(file_path)

        assert summary.new_charges == Decimal("30.00")
        assert summary.adjustment == Decimal("3.00")
        assert summary.rebate_used == Decimal("5.00")

    def test_entry_books_adjustment_when_it_differs_from_rebate_used(self, tmp_path):
        output = self.run(tmp_path, "5.00", "3.00")

        assert "  Liabilities:Credit:ABC:1234  3.00 CNY\n" in output
        assert ";   刷卡金抵扣:             3.00 CNY\n" in output
        assert ";     (本期使用刷卡金 5.00, 本期调整 3.00)\n" in output
        assert "状态: ✅ 平账 (刷卡金 3.00)" in output

    def test_equal_amounts_report_no_breakdown(self, tmp_path):
        output = self.run(tmp_path, "3.00", "3.00")

        assert "  Liabilities:Credit:ABC:1234  3.00 CNY\n" in output
        assert "本期调整" not in output

    def test_adjustment_above_rebate_used_is_flagged(self, tmp_path):
        output = self.run(tmp_path, "3.00", "5.00")

        assert "  Liabilities:Credit:ABC:1234  3.00 CNY\n" in output
        assert "⚠️ 调整金额 5.00 超出刷卡金使用 3.00" in output


class TestABCStatementPeriod:
    """Tests for per-card statement period assignment."""

    def test_cycle_widens_around_a_row_dated_outside_it(self, tmp_path):
        """Test that an out-of-cycle row widens the card's period."""
        html = """<html>
<body>
<table>
    <tr><td><span>620000******1234</span></td></tr>
    <tr><td><span>2030/03/01-2030/03/31</span></td></tr>
</table>
<table>
    <tr>
        <td>300227</td>
        <td>300302</td>
        <td>1234</td>
        <td>merchant-a</td>
        <td>-10.00/CNY</td>
        <td>-10.00/CNY</td>
    </tr>
    <tr>
        <td>300310</td>
        <td>300310</td>
        <td>1234</td>
        <td>merchant-b</td>
        <td>-20.00/CNY</td>
        <td>-20.00/CNY</td>
    </tr>
</table>
</body>
</html>"""
        file_path = tmp_path / "农业银行金穗信用卡电子账单.eml"
        file_path.write_text(create_abc_eml(html), encoding="utf-8")

        transactions = ABCCreditProvider().parse(file_path)

        assert len(transactions) == 2
        for txn in transactions:
            assert txn.statement_period == (date(2030, 2, 27), date(2030, 3, 31))
