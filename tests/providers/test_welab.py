"""Tests for WeLab Bank (汇立银行) statement provider.

All sample data is synthetic: future dates (2030), round amounts, and
placeholder merchants/names. Nothing is copied from real statements.
"""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.providers import get_provider
from bean_sieve.providers.banks.debit.welab import WeLabProvider

# Synthetic column x positions, mirroring the real statement layout
# (Date | Type | Transaction Description | Amount).
_DATE_X, _TYPE_X, _DESC_X, _AMT_X = 55, 165, 270, 505


def _build_statement(path: Path) -> None:
    """Build a synthetic 2-currency WeLab PDF statement at ``path``."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)

    def put(x: float, y: float, text: str, size: float = 6) -> None:
        page.insert_text((x, y), text, fontsize=size, fontname="china-s")

    def row(y: float, d: str, ty: str, de: str, am: str) -> None:
        put(_DATE_X, y, d)
        # Real statements wrap the Type column's Chinese and English onto
        # separate lines; keep them both inside the Type column.
        match = re.match(r"^([一-鿿（）()]+)\s*(.*)$", ty)
        if match and match.group(2):
            put(_TYPE_X, y, match.group(1))
            put(_TYPE_X, y + 8, match.group(2))
        else:
            put(_TYPE_X, y, ty)
        put(_DESC_X, y, de)
        put(_AMT_X, y, am)

    # Page 0 header. Real exports carry NO "WeLab"/"汇立" string on page 0 (the
    # bank name only appears in the notes on a later page), so detection must
    # rely on the WeLab-specific product markers below.
    put(40, 75, "Your Bank Statement (1 Jan 2030 - 31 Jan 2030) 你的银行月结单")
    put(40, 90, "寰球钱包 核心账户 Global Wallet Core Account")
    put(40, 100, "定期存款 Time Deposits - GoSave 2.0 智安存")

    # --- HKD section ---
    put(
        40,
        120,
        "核心账户 Core Account (1010000000) - 港元 HKD (包括智安存 Include Money Safe)",
    )
    put(40, 138, "日期 种类 交易详情 金额 (港元)")
    put(40, 147, "Date Type Transaction Description Amount (HKD)")
    row(168, "01 Jan 2030", "承上结余", "Balance From Previous Statement", "-")
    row(
        190,
        "10 Jan 2030",
        "收款 Receive money",
        "Receive money from 测试用户 Ref: FT30A1",
        "200.00",
    )
    row(212, "12 Jan 2030", "外币兑换", "CNY/HKD @ 1.10 Ref: FX30000001", "-110.00")
    row(
        234,
        "15 Jan 2030",
        "服务收费 Service Charge",
        "测试服务费 Test Fee Ref: FT30B2",
        "-10.00",
    )
    row(256, "31 Jan 2030", "帐户结余 Closing Balance", "", "80.00")

    # --- CNY section ---
    put(
        40,
        300,
        "核心账户 Core Account (1010000000) - 人民币 CNY (包括智安存 Include Money Safe)",
    )
    put(40, 318, "日期 种类 交易详情 金额 (人民币)")
    put(40, 327, "Date Type Transaction Description Amount (CNY)")
    row(348, "12 Jan 2030", "外币兑换", "CNY/HKD @ 1.10 Ref: FX30000001", "100.00")
    # Rich card spending: a foreign spend settled via FX, with the detail wrapped
    # onto a second line (Ref / Transaction Date / FX Ref), as in real statements.
    # The detail line uses a smaller font so it fits the Description column (the
    # synthetic CJK font is far wider than the real statement font).
    put(_DATE_X, 370, "20 Jan 2030")
    put(_TYPE_X, 370, "借记卡消费")
    put(_TYPE_X, 378, "Debit Card spending")
    put(_DESC_X, 370, "WEIXIN*测试商户 CHN Online 5999")
    put(_DESC_X, 378, "Ref: FT30C3, Transaction Date: 22 Jan 2030, FX Ref: FX01", 4)
    put(_AMT_X, 370, "-50.00")
    row(
        392,
        "22 Jan 2030",
        "借记卡退款 Debit Card Refund",
        "WEIXIN*测试退款 CHN Online Ref: FT30D4",
        "30.00",
    )
    row(414, "31 Jan 2030", "帐户结余 Closing Balance", "", "80.00")

    put(40, 750, "Page 1 of 1")
    doc.save(str(path))
    doc.close()


@pytest.fixture
def statement(tmp_path: Path) -> Path:
    path = tmp_path / "welab.pdf"
    _build_statement(path)
    return path


class TestRegistrationAndDetection:
    def test_provider_registration(self) -> None:
        provider = get_provider("welab_debit")
        assert isinstance(provider, WeLabProvider)
        assert provider.provider_id == "welab_debit"
        assert provider.provider_name == "汇立银行"
        assert ".pdf" in provider.supported_formats

    def test_can_handle_by_filename(self) -> None:
        assert WeLabProvider.can_handle(Path("welab_202601.pdf"))
        assert WeLabProvider.can_handle(Path("汇立银行月结单.pdf"))

    def test_cannot_handle_other(self) -> None:
        assert not WeLabProvider.can_handle(Path("statement.csv"))
        # Generic PDF name without WeLab keyword and no WeLab content
        assert not WeLabProvider.can_handle(Path("other_bank.pdf"))

    def test_can_handle_by_content(self, statement: Path) -> None:
        # The real export filename has no bank keyword, so content detection
        # (WeLab product markers on page 0) must carry it.
        generic = statement.parent / "PDF文稿-ABC123-1.pdf"
        statement.rename(generic)
        assert WeLabProvider.can_handle(generic)


class TestDateParsing:
    def test_valid(self) -> None:
        assert WeLabProvider._parse_date("20 Apr 2026") == date(2026, 4, 20)
        assert WeLabProvider._parse_date("1 Jan 2030") == date(2030, 1, 1)

    def test_invalid(self) -> None:
        assert WeLabProvider._parse_date("") is None
        assert WeLabProvider._parse_date("2026-04-20") is None
        assert WeLabProvider._parse_date("32 Apr 2026") is None
        assert WeLabProvider._parse_date("20 Xyz 2026") is None


class TestAmountParsing:
    def test_sign_is_negated(self) -> None:
        # Statement debit (negative) is an expense -> positive in bean-sieve
        assert WeLabProvider._parse_amount("-110.00") == Decimal("110.00")
        # Statement credit (positive) is income -> negative in bean-sieve
        assert WeLabProvider._parse_amount("200.00") == Decimal("-200.00")

    def test_thousand_separator(self) -> None:
        assert WeLabProvider._parse_amount("1,234.56") == Decimal("-1234.56")

    def test_no_amount(self) -> None:
        assert WeLabProvider._parse_amount("-") is None
        assert WeLabProvider._parse_amount("") is None


class TestFieldHelpers:
    def test_english_only(self) -> None:
        assert (
            WeLabProvider._english_only("借记卡消费 Debit Card spending")
            == "Debit Card spending"
        )
        assert (
            WeLabProvider._english_only(
                "外币兑换 (借记卡交易) Foreign currency exchange"
            )
            == "Foreign currency exchange"
        )

    def test_extract_payee_receive(self) -> None:
        assert WeLabProvider._extract_payee("Receive money from 测试用户") == "测试用户"

    def test_extract_payee_merchant(self) -> None:
        assert (
            WeLabProvider._extract_payee("WEIXIN*测试商户 CHN Online 5999")
            == "WEIXIN*测试商户"
        )

    def test_extract_payee_none(self) -> None:
        assert WeLabProvider._extract_payee("测试服务费 Test Fee") is None


class TestParseIntegration:
    def test_statement_period(self, statement: Path) -> None:
        txns = WeLabProvider().parse(statement)
        assert all(
            t.statement_period == (date(2030, 1, 1), date(2030, 1, 31)) for t in txns
        )

    def test_skips_balance_rows(self, statement: Path) -> None:
        txns = WeLabProvider().parse(statement)
        # 承上结余 + 帐户结余 (x2) must not appear
        for t in txns:
            assert "Closing Balance" not in t.description
            assert "Balance From Previous" not in t.description

    def test_parses_all_currencies(self, statement: Path) -> None:
        txns = WeLabProvider().parse(statement)
        currencies = {t.currency for t in txns}
        assert currencies == {"HKD", "CNY"}
        # 3 HKD (receive, exchange, service) + 3 CNY (exchange, spend, refund)
        assert len(txns) == 6

    def test_receive_money(self, statement: Path) -> None:
        txns = WeLabProvider().parse(statement)
        recv = next(t for t in txns if t.order_id == "FT30A1")
        assert recv.currency == "HKD"
        assert recv.amount == Decimal("-200.00")  # income
        assert recv.payee == "测试用户"
        assert recv.card_last4 == "HKD"

    def test_card_spending_sign_and_payee(self, statement: Path) -> None:
        txns = WeLabProvider().parse(statement)
        spend = next(t for t in txns if t.order_id == "FT30C3")
        assert spend.currency == "CNY"
        assert spend.amount == Decimal("50.00")  # expense
        assert spend.payee == "WEIXIN*测试商户"
        assert spend.metadata.get("transaction_type") == "Debit Card spending"
        # The wrapped detail tail goes to metadata; description stays clean.
        assert spend.description == "WEIXIN*测试商户 CHN Online 5999"
        assert spend.metadata.get("fx_ref") == "FX01"
        assert spend.metadata.get("transaction_date") == "22 Jan 2030"

    def test_refund_is_income(self, statement: Path) -> None:
        txns = WeLabProvider().parse(statement)
        refund = next(t for t in txns if t.order_id == "FT30D4")
        assert refund.amount == Decimal("-30.00")  # income

    def test_refund_tagged_and_linked(self, statement: Path) -> None:
        # Refunds get a #refund tag and a ^<order_id> link.
        txns = WeLabProvider().parse(statement)
        refund = next(t for t in txns if t.order_id == "FT30D4")
        assert refund.tags == ["refund"]
        assert refund.links == ["FT30D4"]
        # Non-refund rows carry neither.
        spend = next(t for t in txns if t.order_id == "FT30C3")
        assert spend.tags == []
        assert spend.links == []

    def test_exchange_legs_kept_separate(self, statement: Path) -> None:
        # Both legs of a cross-currency exchange are kept (not merged), so each
        # can match its corresponding posting in a same-account ledger entry.
        txns = WeLabProvider().parse(statement)
        legs = [t for t in txns if t.order_id == "FX30000001"]
        assert len(legs) == 2  # one per currency section, same Ref
        assert all(t.description == "Foreign currency exchange" for t in legs)
        by_ccy = {t.currency: t for t in legs}
        # HKD sell side is an outflow (expense, positive); CNY buy side is an
        # inflow (income, negative).
        assert by_ccy["HKD"].amount == Decimal("110.00")
        assert by_ccy["CNY"].amount == Decimal("-100.00")
        assert by_ccy["HKD"].price_amount is None  # no @@ merging

    def test_only_exchange_typed_rows_are_exchanges(self, statement: Path) -> None:
        # Exchange classification is driven by the Type column ("外币兑换"), not by
        # description text — a refund is never treated as an exchange leg.
        txns = WeLabProvider().parse(statement)
        refund = next(t for t in txns if t.order_id == "FT30D4")
        assert refund.description != "Foreign currency exchange"
        exchanges = [t for t in txns if t.description == "Foreign currency exchange"]
        assert all(t.order_id == "FX30000001" for t in exchanges)


class TestMultiPageContinuation:
    """A currency section spanning pages: continuation pages carry no marker."""

    @staticmethod
    def _build(path: Path) -> None:
        import fitz

        doc = fitz.open()

        def put(page, x: float, y: float, text: str) -> None:
            page.insert_text((x, y), text, fontsize=6, fontname="china-s")

        def header(page) -> None:
            put(page, 40, 138, "日期 种类 交易详情 金额 (人民币)")
            put(page, 40, 147, "Date Type Transaction Description Amount (CNY)")

        # Page 0: CNY section marker + product markers + one row
        p0 = doc.new_page(width=595, height=842)
        put(p0, 40, 90, "寰球钱包 核心账户 Global Wallet Core Account")
        put(p0, 40, 100, "定期存款 Time Deposits - GoSave 2.0 智安存")
        put(p0, 40, 110, "Your Bank Statement (1 Jan 2030 - 31 Jan 2030)")
        put(p0, 40, 120, "核心账户 Core Account (1010000000) - 人民币 CNY")
        header(p0)
        put(p0, 55, 170, "05 Jan 2030")
        put(p0, 165, 170, "借记卡消费")
        put(p0, 270, 170, "WEIXIN*商户A CHN Online Ref: FT3001")
        put(p0, 505, 170, "-20.00")
        put(p0, 40, 750, "Page 1 of 2")

        # Page 1: continuation — NO section marker, repeated column header + rows
        p1 = doc.new_page(width=595, height=842)
        # Repeated statement header on the right side (must not create anchors)
        put(p1, 357, 42, "Your Bank Statement (1 Jan 2030 - 31 Jan 2030)")
        put(p1, 513, 61, "2 Feb 2030")
        header(p1)
        put(p1, 55, 170, "06 Jan 2030")
        put(p1, 165, 170, "借记卡消费")
        put(p1, 270, 170, "WEIXIN*商户B CHN Online Ref: FT3002")
        put(p1, 505, 170, "-30.00")
        put(p1, 55, 192, "07 Jan 2030")
        put(p1, 165, 192, "借记卡消费")
        put(p1, 270, 192, "WEIXIN*商户C CHN Online Ref: FT3003")
        put(p1, 505, 192, "-40.00")
        put(p1, 40, 750, "Page 2 of 2")

        doc.save(str(path))
        doc.close()

    def test_continuation_rows_captured(self, tmp_path: Path) -> None:
        path = tmp_path / "welab_multipage.pdf"
        self._build(path)
        txns = WeLabProvider().parse(path)
        # All three rows captured, including the two on the marker-less page 2
        assert {t.order_id for t in txns} == {"FT3001", "FT3002", "FT3003"}
        assert all(t.currency == "CNY" for t in txns)


class TestEmptyStatement:
    def test_no_transactions(self, tmp_path: Path) -> None:
        import fitz

        path = tmp_path / "empty.pdf"
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text(
            (40, 60), "WeLab Bank 汇立银行", fontsize=6, fontname="china-s"
        )
        page.insert_text(
            (40, 120),
            "核心账户 Core Account (1010000000) - 港元 HKD",
            fontsize=6,
            fontname="china-s",
        )
        doc.save(str(path))
        doc.close()
        assert WeLabProvider().parse(path) == []
