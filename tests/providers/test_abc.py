"""Tests for Agricultural Bank of China (ABC) credit card statement provider."""

import base64
import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

import fitz
import pytest

from bean_sieve.config.schema import Config, ProviderConfig
from bean_sieve.core.coverage import CoverageEntry, select
from bean_sieve.core.metadata_keys import STATEMENT_CYCLE
from bean_sieve.core.types import (
    MatchResult,
    ReconcileContext,
    ReconcileResult,
)
from bean_sieve.providers import PROVIDERS, get_provider
from bean_sieve.providers.banks.credit.abc import ABCCreditProvider

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


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


NEW_HEADER = (
    "<tr><td>交易日期 T Date</td><td>入账日期 P Date</td>"
    "<td>卡号 后四位 Card No.</td><td>交易描述 Description</td>"
    "<td>交易金额/币种 Tran Amt/Curr</td>"
    "<td>入账金额/币种 (支出为-) Sett Amt/Curr</td></tr>"
)


@pytest.fixture
def abc_html_content():
    """Sample ABC statement HTML content."""
    return f"""<html>
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
    {NEW_HEADER}
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
        html = f"""<html>
<body>
<table><tr><td><span>2030/01/01-2030/01/31</span></td></tr></table>
<table>
    {NEW_HEADER}
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
        html = f"""<html>
<body>
<table>
    <tr><td><span>620000******1234</span></td></tr>
    <tr><td><span>2030/01/01-2030/01/31</span></td></tr>
</table>
<table>
    {NEW_HEADER}
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
        html = f"""<html>
<body>
<table><tr><td><span>2030/01/01-2030/01/31</span></td></tr></table>
<table>
    {NEW_HEADER}
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
        html = f"""<html>
<body>
<table><tr><td><span>2030/01/01-2030/01/31</span></td></tr></table>
<table>
    {NEW_HEADER}
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
        html = f"""<html>
<body>
<table><tr><td><span>2025/12/01-2025/12/31</span></td></tr></table>
<table>
    {NEW_HEADER}
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
        html = f"""<html>
<body>
<table><tr><td><span>2025/01/01-2025/01/31</span></td></tr></table>
<table>
    {NEW_HEADER}
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
        html = f"""<html>
<body>
<table><tr><td><span>2025/11/01-2025/11/30</span></td></tr></table>
<table>
    {NEW_HEADER}
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
        html = f"""<html>
<body>
<table><tr><td><span>2025/11/01-2025/11/30</span></td></tr></table>
<table>
    {NEW_HEADER}
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
        html = f"""<html>
<body>
<table><tr><td><span>2025/11/01-2025/11/30</span></td></tr></table>
<table>
    {NEW_HEADER}
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


REBATE_HTML = f"""<html>
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
    {NEW_HEADER}
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
    {NEW_HEADER}
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
        html = f"""<html>
<body>
<table>
    <tr><td><span>620000******1234</span></td></tr>
    <tr><td><span>2030/03/01-2030/03/31</span></td></tr>
</table>
<table>
    {NEW_HEADER}
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


# (trans date, post date, card cell, description, amount, booked amount)
Row = tuple[str, str, str, str, str, str]

ROWS: list[Row] = [
    (
        "300302",
        "300302",
        "2468主",
        "网上消费 支付宝，payee-a",
        "-53.00/CNY",
        "-53.00/CNY",
    ),
    ("300305", "300306", "9753附", "消费 shop-b", "-77.00/CNY", "-77.00/CNY"),
    ("300309", "300310", "2468", "退货 shop-b", "81.00/CNY", "81.00/CNY"),
    ("300315", "300315", "", "约定还款", "1,093.00/CNY", "1,093.00/CNY"),
]
CARD = "620000******2468"
CYCLE = "2030/03/01-2030/03/31"


def write_abc_eml(path: Path, rows: list[Row], cycle: str = CYCLE) -> Path:
    body = "\n".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
    )
    html = f"""<html><body>
<table>
<tr><td><span>{CARD}</span></td></tr>
<tr><td><span>{cycle}</span></td></tr>
</table>
<table>
{NEW_HEADER}
{body}
</table>
</body></html>"""
    path.write_text(create_abc_eml(html), encoding="utf-8")
    return path


def write_abc_pdf(
    path: Path,
    rows: list[Row],
    cycle: str = CYCLE,
    balance: str = "-1,186.00",
    rows_per_page: int = 30,
) -> Path:
    """Draw a reissued ABC statement; a blank card cell prints nothing."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)

    def put(x: float, y: float, text: str) -> None:
        page.insert_text((x, y), text, fontname="china-s", fontsize=8)

    put(198, 259, "农业银行信用卡对账单2030年03月（补）")
    put(64, 321, "卡号")
    put(239, 321, "账单周期")
    put(127, 327, CARD)
    put(310, 327, cycle)
    put(35, 366, "本期应还款额(欠款为-) New Balance")
    put(325, 366, "最低还款额(欠款为-) Min Payment")
    put(36, 394, "人民币(CNY)")
    put(240, 394, balance)
    put(326, 394, "人民币(CNY)")
    put(520, 394, "-118.60")
    put(28, 524, "交易明细")
    y = 625.0
    for i, row in enumerate(rows):
        if i and i % rows_per_page == 0:
            page = doc.new_page(width=595, height=842)
            y = 77.0
        for x, cell in zip([38, 89, 140, 200, 360, 470], row, strict=True):
            if cell:
                put(x, y, cell)
        y += 21
    doc.save(path)
    doc.close()
    return path


def shape(txns):
    return [
        (
            t.date,
            t.amount,
            t.currency,
            t.description,
            t.card_last4,
            t.statement_period,
            t.metadata,
        )
        for t in txns
    ]


class TestABCReissue:
    """Tests for the reissued PDF statement."""

    def test_rows(self, tmp_path):
        txns = ABCCreditProvider().parse(write_abc_pdf(tmp_path / "r.pdf", ROWS))

        assert [(t.date, t.amount, t.card_last4) for t in txns] == [
            (date(2030, 3, 2), Decimal("53.00"), "2468"),
            (date(2030, 3, 5), Decimal("77.00"), "9753"),
            (date(2030, 3, 9), Decimal("-81.00"), "2468"),
            # A row without a card number belongs to the statement's card.
            (date(2030, 3, 15), Decimal("-1093.00"), "2468"),
        ]
        assert txns[0].description == "网上消费 支付宝，payee-a"
        assert txns[0].metadata[STATEMENT_CYCLE] == (
            date(2030, 3, 1),
            date(2030, 3, 31),
        )

    def test_same_transactions_as_mail(self, tmp_path):
        pdf = write_abc_pdf(tmp_path / "r.pdf", ROWS)
        eml = write_abc_eml(tmp_path / "s.eml", ROWS)

        provider = ABCCreditProvider()
        assert shape(provider.parse(pdf)) == shape(provider.parse(eml))

    def test_rows_across_pages(self, tmp_path):
        pdf = write_abc_pdf(tmp_path / "r.pdf", ROWS, rows_per_page=3)
        assert len(ABCCreditProvider().parse(pdf)) == len(ROWS)

    def test_only_this_provider_claims_it(self, tmp_path):
        pdf = write_abc_pdf(tmp_path / "statement.pdf", ROWS)
        assert [p for p, c in PROVIDERS.items() if c.can_handle(pdf)] == ["abc_credit"]

    def test_claims_no_other_example_statement(self):
        for path in sorted(EXAMPLES.glob("*/*")):
            if path.parent.name != "abc_credit":
                assert not ABCCreditProvider.can_handle(path), path

    def test_summary_has_balance_but_no_formula(self, tmp_path):
        summary = ABCCreditProvider()._extract_summary(
            write_abc_pdf(tmp_path / "r.pdf", ROWS)
        )

        assert summary.card_number == CARD
        assert summary.statement_period == (date(2030, 3, 1), date(2030, 3, 31))
        assert summary.statement_balance == Decimal("-1186.00")
        assert summary.new_charges is None
        assert summary.rebate_used == Decimal(0)

    def test_post_output_reports_the_reissue(self, tmp_path):
        pdf = write_abc_pdf(tmp_path / "r.pdf", ROWS)
        output = ABCCreditProvider().post_output(
            "",
            empty_result(),
            ReconcileContext(statement_paths=[pdf], provider_ids={pdf: "abc_credit"}),
        )

        assert f"卡号: {CARD} (尾号 2468)" in output
        assert "账单应还:            1186.00 CNY" in output
        assert "账单消费" not in output
        assert "刷卡金抵扣 ---" not in output

    def test_post_output_skips_a_reissue_the_mail_replaced(self, tmp_path):
        provider = ABCCreditProvider()
        eml = write_abc_eml(tmp_path / "s.eml", ROWS)
        pdf = write_abc_pdf(tmp_path / "r.pdf", ROWS)
        ranks = {kind: i for i, kind in enumerate(provider.coverage_priority)}
        entries = [
            CoverageEntry("abc_credit", path, cov, ranks[cov.source])
            for path in (eml, pdf)
            for cov in provider.coverage(path, provider.parse(path))
        ]
        result = empty_result()
        result.coverage = select(entries)

        output = provider.post_output(
            "",
            result,
            ReconcileContext(
                statement_paths=[eml, pdf],
                provider_ids={eml: "abc_credit", pdf: "abc_credit"},
            ),
        )

        assert output.count(f"卡号: {CARD}") == 1
        assert "账单应还:            1186.00 CNY" not in output


class TestABCCoverage:
    def test_one_entry_per_card_for_the_printed_cycle(self, tmp_path):
        provider = ABCCreditProvider()
        for path, source in (
            (write_abc_pdf(tmp_path / "r.pdf", ROWS), "reissue"),
            (write_abc_eml(tmp_path / "s.eml", ROWS), "statement"),
        ):
            covs = provider.coverage(path, provider.parse(path))
            assert [
                (c.account, c.start, c.end, len(c.rows), c.source) for c in covs
            ] == [
                ("2468", date(2030, 3, 1), date(2030, 3, 31), 3, source),
                ("9753", date(2030, 3, 1), date(2030, 3, 31), 1, source),
            ]

    @pytest.mark.parametrize("reverse", [False, True])
    def test_mail_outranks_its_reissue(self, tmp_path, reverse):
        provider = ABCCreditProvider()
        paths = [
            write_abc_pdf(tmp_path / "r.pdf", ROWS),
            write_abc_eml(tmp_path / "s.eml", ROWS),
            write_abc_pdf(tmp_path / "next.pdf", ROWS[:1], "2030/04/01-2030/04/30"),
        ]
        if reverse:
            paths.reverse()
        ranks = {kind: i for i, kind in enumerate(provider.coverage_priority)}
        parsed = {path: provider.parse(path) for path in paths}
        entries = [
            CoverageEntry("abc_credit", path, cov, ranks[cov.source])
            for path in paths
            for cov in provider.coverage(path, parsed[path])
        ]
        kept = select(entries).keep([t for rows in parsed.values() for t in rows])
        by_file = {
            path.name: sum(t.source_file == path for t in kept) for path in paths
        }
        assert by_file == {"s.eml": 4, "r.pdf": 0, "next.pdf": 1}


OLD_CARD = "620000*****2468"


def _nest(html: str, depth: int) -> str:
    for _ in range(depth):
        html = f"<table><tr><td>{html}</td></tr></table>"
    return html


def _bilingual(zh: str, en: str) -> str:
    return f"<td><table><tr><td>{zh}</td></tr><tr><td>{en}</td></tr></table></td>"


def old_template_html(
    rows: list[Row],
    cycle: str = "20300301-20300331",
    balance: str = "-1,186.00",
    new_charge: str = "130.00",
) -> str:
    """Render rows in the older mail layout.

    Every block sits in nested layout tables, each detail row with a spacer
    cell in front, YYYYMMDD dates and 交易摘要 / 交易地点 in separate cells; a
    description without a space prints as one merged cell.
    """
    header = "".join(
        _bilingual(zh, en)
        for zh, en in (
            ("交易日", "TDate"),
            ("入账日期", "PDate"),
            ("卡号后四位", "Card No."),
            ("交易摘要", "Type"),
            ("交易地点", "City/Merchant Name/Branches"),
            ("交易金额/币种", "Tran Amt/Curr"),
            ("入账金额/币种(支出为-)", "Sett Amt/Curr"),
        )
    )
    details = []
    for trans, post, card, description, amount, booked in rows:
        cells = ["", f"20{trans}", f"20{post}", card, *description.split(" ", 1)]
        cells += [amount, booked]
        tds = "".join(f"<td>{cell}</td>" for cell in cells)
        details.append(_nest(f"<table><tr>{tds}</tr></table>", 3))
    formula_labels = "".join(
        _bilingual(*label) if isinstance(label, tuple) else f"<td>{label}</td>"
        for label in (
            ("币种", "Curr"),
            ("上期余额（欠款为-)", "Previous Balance"),
            "-",
            ("本期新增应还款额", "New Charge"),
            "+",
            ("本期已还款额、刷卡金转入", "Payments"),
            "=",
            ("本期账户全部余额(欠款为-)", "Account Balance"),
        )
    )
    formula_values = "".join(
        f"<td>{v}</td>"
        for v in ("人民币(CNY)", "-1,072.50", new_charge, "16.50", balance)
    )
    summary = (
        "<table><tr><td></td><td>卡号</td>"
        f"<td>{OLD_CARD}</td><td>账单周期</td><td>{cycle}</td>"
        "<td>到期还款日</td><td>20300420</td></tr></table>"
        "<table><tr><td>币种</td><td>本期应还款额(欠款为-)</td>"
        "<td>最低还款额(欠款为-)</td><td>信用额度</td></tr>"
        "<tr><td>Curr</td><td>New Balance</td><td>Min.Payment</td>"
        "<td>Credit Limit</td></tr>"
        f"<tr><td>人民币(CNY)</td><td>{balance}</td><td>-118.60</td>"
        "<td>23000.00</td></tr></table>"
    )
    return f"""<html>
<head><meta http-equiv='Content-Type' content='text/html;charset=gbk'></head>
<body>
{_nest(summary, 3)}
{_nest(f"<table><tr>{formula_labels}</tr></table>", 4)}
{_nest(f"<table><tr>{formula_values}</tr></table>", 4)}
<table><tr><td>【交易明细】</td></tr></table>
{_nest(f"<table><tr>{header}</tr></table>", 4)}
{"".join(details)}
</body></html>"""


def write_old_template_eml(
    path: Path, rows: list[Row], content_type: str = "text/html", **kwargs: str
) -> Path:
    """Write the mail as the older template sends it: GBK, declared only in HTML."""
    encoded = base64.b64encode(old_template_html(rows, **kwargs).encode("gbk"))
    path.write_bytes(
        b"From: creditcard@abchina.com\nMIME-Version: 1.0\n"
        + f"Content-Type: {content_type}\n".encode("ascii")
        + b"Content-Transfer-Encoding: base64\n\n"
        + encoded
    )
    return path


class TestABCOldTemplate:
    """The older mail template carries the same rows as the current one."""

    def test_same_transactions_as_current_template(self, tmp_path):
        provider = ABCCreditProvider()
        old = provider.parse(write_old_template_eml(tmp_path / "old.eml", ROWS))
        new = provider.parse(write_abc_eml(tmp_path / "new.eml", ROWS))

        assert len(old) == len(ROWS)
        assert shape(old) == shape(new)

    def test_empty_mime_charset_falls_back_to_the_html_one(self, tmp_path):
        path = write_old_template_eml(
            tmp_path / "old.eml", ROWS, content_type='text/html; charset=""'
        )
        txns = ABCCreditProvider().parse(path)

        assert txns[0].description == "网上消费 支付宝，payee-a"

    def test_gb2312_label_decodes_characters_outside_gb2312(self, tmp_path):
        # 镕 is in GBK but not in GB2312.
        rows = [("300302", "300302", "", "消费 镕-shop", "-53.00/CNY", "-53.00/CNY")]
        path = write_old_template_eml(
            tmp_path / "old.eml", rows, content_type="text/html; charset=gb2312"
        )
        txns = ABCCreditProvider().parse(path)

        assert txns[0].description == "消费 镕-shop"

    def test_summary(self, tmp_path):
        summary = ABCCreditProvider()._extract_summary(
            write_old_template_eml(tmp_path / "old.eml", ROWS)
        )

        assert summary.card_number == OLD_CARD
        assert summary.statement_period == (date(2030, 3, 1), date(2030, 3, 31))
        assert summary.statement_balance == Decimal("-1186.00")
        assert summary.new_charges == Decimal("130.00")
        assert summary.adjustment is None
        assert summary.rebate_used == Decimal(0)

    def test_post_output_balances(self, tmp_path):
        path = write_old_template_eml(tmp_path / "old.eml", ROWS)
        output = ABCCreditProvider().post_output(
            "",
            empty_result(),
            ReconcileContext(statement_paths=[path], provider_ids={path: "abc_credit"}),
        )

        assert f"卡号: {OLD_CARD} (尾号 2468)" in output
        assert "账单周期: 2030/03/01-2030/03/31" in output
        assert "账单消费:             130.00 CNY" in output
        assert "状态: ✅ 平账" in output
        assert "未读出" not in output

    def test_coverage_uses_the_printed_cycle(self, tmp_path):
        provider = ABCCreditProvider()
        path = write_old_template_eml(tmp_path / "old.eml", ROWS)
        covs = provider.coverage(path, provider.parse(path))

        assert [(c.account, c.start, c.end, len(c.rows)) for c in covs] == [
            ("2468", date(2030, 3, 1), date(2030, 3, 31), 3),
            ("9753", date(2030, 3, 1), date(2030, 3, 31), 1),
        ]


class TestABCUnreadableMail:
    def test_dated_rows_without_a_known_header_are_warned(self, tmp_path, caplog):
        html = old_template_html(ROWS).replace("交易摘要", "交易类型")
        path = tmp_path / "农业银行金穗信用卡.eml"
        path.write_text(create_abc_eml(html), encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            assert ABCCreditProvider().parse(path) == []

        assert "Unrecognised ABC statement layout" in caplog.text
        assert path.name in caplog.text

    def test_missing_cycle_is_warned(self, tmp_path, caplog):
        path = write_abc_eml(tmp_path / "s.eml", ROWS, cycle="")

        with caplog.at_level(logging.WARNING):
            transactions = ABCCreditProvider().parse(path)

        assert len(transactions) == len(ROWS)
        assert "No statement cycle" in caplog.text
        assert path.name in caplog.text

    def test_report_names_the_fields_it_could_not_read(self, tmp_path):
        path = write_abc_eml(tmp_path / "s.eml", ROWS, cycle="")
        output = ABCCreditProvider().post_output(
            "",
            empty_result(),
            ReconcileContext(statement_paths=[path], provider_ids={path: "abc_credit"}),
        )

        assert ";   未读出: 账单周期、本期应还款额、账务说明\n" in output

    def test_rebate_without_a_cycle_books_no_entry(self, tmp_path):
        path = tmp_path / "农业银行金穗信用卡刷卡金.eml"
        html = REBATE_HTML.replace("<span>2030/01/01-2030/01/31</span>", "")
        path.write_text(create_abc_eml(html), encoding="utf-8")
        output = ABCCreditProvider().post_output(
            "",
            empty_result(),
            ReconcileContext(statement_paths=[path], provider_ids={path: "abc_credit"}),
        )

        assert "刷卡金抵扣 ---" not in output
        assert "状态: ✅ 平账 (刷卡金 5.00，账单周期未读出，未生成分录)" in output
