"""Tests for WeLab Bank (汇立银行) statement provider.

All sample data is synthetic: future dates (2030), round amounts, and
placeholder merchants/names. Nothing is copied from real statements.
"""

import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

import fitz
import pytest

from bean_sieve.providers import get_provider
from bean_sieve.providers.banks.debit.welab import WeLabProvider

# Cell edges of the real statement table. Every row outlines its own cells; a
# shaded balance row splits the width its own way, sharing the Amount cell
# with the transaction rows on some pages and not on others.
_ROW_EDGES = (61, 69, 145, 249, 475, 535, 544)
_BALANCE_EDGES = (61, 69, 145, 177, 430, 535, 544)
_BALANCE_EDGES_SHARING_AMOUNT = (61, 69, 145, 177, 430, 475, 535, 544)
_LINE_H = 8.0


class _Statement:
    """Draws a WeLab statement top-down, one section after another."""

    def __init__(self, font: str = "china-s") -> None:
        self.font = font
        self.doc = fitz.open()
        self.new_page()

    def new_page(self) -> None:
        self.page = self.doc.new_page(width=595, height=842)
        self.y = 130.0

    def put(self, x: float, y: float, text: str, size: float = 6) -> None:
        self.page.insert_text((x, y), text, fontsize=size, fontname=self.font)

    def _right(self, y: float, text: str) -> None:
        width = fitz.get_text_length(text, fontname=self.font, fontsize=6)
        self.put(534 - width, y, text)

    def _border(self, y: float, edges: tuple[int, ...]) -> None:
        for x0, x1 in zip(edges, edges[1:], strict=False):
            self.page.draw_line((x0, y), (x1, y), width=0.5)

    def section(self, marker: str, header: tuple[str, ...] | None = None) -> None:
        self.put(94, self.y, marker)
        self.page.draw_line((400, self.y + 4), (545, self.y + 4), width=0.5)
        top = self.y + 16
        self.page.draw_rect(fitz.Rect(60, top, 545, top + 30), color=None, fill=0.9)
        cn = header or ("日期", "种类", "交易详情", "金额")
        # The header labels do not start at the cell edges.
        for x, label_cn, label_en in zip(
            (70, 145, 265),
            cn,
            ("Date", "Type", "Transaction Description"),
            strict=False,
        ):
            self.put(x, top + 12, label_cn)
            self.put(x, top + 21, label_en)
        self._right(top + 21, "Amount")
        self.y = top + 30

    def balance(
        self,
        day: str,
        label: str,
        amount: str,
        edges: tuple[int, ...] = _BALANCE_EDGES,
    ) -> None:
        top, bottom = self.y, self.y + 21
        for x0, x1 in zip(edges, edges[1:], strict=False):
            self.page.draw_rect(fitz.Rect(x0, top, x1, bottom), color=None, fill=0.95)
        self._border(top, edges)
        self._border(bottom, edges)
        self.put(70, top + 11, day)
        self.put(145, top + 11, label)
        self._right(top + 11, amount)
        self.y = bottom

    def row(self, day: str, kind: list[str], detail: list[str], amount: str) -> None:
        top = self.y
        bottom = top + 14 + _LINE_H * max(len(kind), len(detail))
        self._border(top, _ROW_EDGES)
        self._border(bottom, _ROW_EDGES)
        self.put(70, top + 11, day)
        # The built-in CJK font runs wider than the bank's; wrapped lines
        # use a smaller size to stay inside their cells.
        for column_x, lines in ((146, kind), (250, detail)):
            for i, text in enumerate(lines):
                self.put(column_x, top + 11 + _LINE_H * i, text, 6 if i == 0 else 4)
        self._right(top + 11, amount)
        self.y = bottom

    def save(self, path: Path) -> Path:
        self.doc.save(str(path))
        self.doc.close()
        return path


_OPENING = "承上结余 Balance From Previous Statement"
_CLOSING = "帐户结余 Closing Balance"


def _build_statement(path: Path) -> None:
    """Build a synthetic 2-currency WeLab PDF statement at ``path``."""
    st = _Statement()
    # Real exports carry NO "WeLab"/"汇立" string on page 0 (the bank name only
    # appears in the notes on a later page), so detection must rely on the
    # WeLab-specific product markers below.
    st.put(40, 75, "Your Bank Statement (1 Jan 2030 - 31 Jan 2030) 你的银行月结单")
    st.put(40, 90, "寰球钱包 核心账户 Global Wallet Core Account")
    st.put(40, 100, "定期存款 Time Deposits - GoSave 2.0 智安存")

    st.section(
        "核心账户 Core Account (1010000000) - 港元 HKD (包括智安存 Include Money Safe)"
    )
    st.balance("1 Jan 2030", _OPENING, "-", _BALANCE_EDGES_SHARING_AMOUNT)
    st.row("10 Jan 2030", ["收款", "Receive money"], ["Receive money from 测试用户", "Ref: FT30A1"], "200.00")  # fmt: skip
    st.row("12 Jan 2030", ["外币兑换", "Foreign currency exchange"], ["CNY/HKD @ 1.10", "Ref: FX30000001"], "-110.00")  # fmt: skip
    st.row("15 Jan 2030", ["服务收费", "Service Charge"], ["测试服务费 Test Fee", "Ref: FT30B2"], "-10.00")  # fmt: skip
    st.balance("31 Jan 2030", _CLOSING, "80.00", _BALANCE_EDGES_SHARING_AMOUNT)

    st.y += 30
    st.section(
        "核心账户 Core Account (1010000000) - 人民币 CNY (包括智安存 Include Money Safe)"
    )
    st.row("12 Jan 2030", ["外币兑换", "Foreign currency exchange"], ["CNY/HKD @ 1.10", "Ref: FX30000001"], "100.00")  # fmt: skip
    # A foreign spend settled via FX, its detail wrapped onto a second line.
    st.row("20 Jan 2030", ["借记卡消费", "Debit Card spending"], ["WEIXIN*测试商户 CHN Online 5999", "Ref: FT30C3, Transaction Date: 22 Jan 2030, FX Ref: FX01"], "-50.00")  # fmt: skip
    st.row("22 Jan 2030", ["借记卡退款", "Debit Card Refund"], ["WEIXIN*测试退款 CHN Online", "Ref: FT30D4"], "30.00")  # fmt: skip
    st.balance("31 Jan 2030", _CLOSING, "80.00")

    st.put(280, 818, "Page 1 of 1")
    st.save(path)


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

    def test_can_handle_by_content_without_gosave(self, tmp_path: Path) -> None:
        # From 2026-09 the time-deposit line no longer names GoSave.
        import fitz

        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        for y, text in (
            (
                90,
                "寰球钱包 核心账户 Global Wallet Core Account (包括智安存 Include Money Safe)",
            ),
            (
                100,
                "定期存款 Time Deposit（包括目标定期存款及智安存 Includes Target Time Deposit and Money Safe）",
            ),
        ):
            page.insert_text((40, y), text, fontsize=6, fontname="china-s")
        generic = tmp_path / "PDF文稿-ABC123-2.pdf"
        doc.save(generic)
        doc.close()
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
                "外币兑换 (借记卡交易) Foreign currency exchange (Debit Card transaction)"
            )
            == "Foreign currency exchange (Debit Card transaction)"
        )
        assert (
            WeLabProvider._english_only("HKD 存款利息 HKD Deposit interest")
            == "HKD Deposit interest"
        )
        assert WeLabProvider._english_only("借記卡退款 Debit Card Refund") == (
            "Debit Card Refund"
        )
        assert WeLabProvider._english_only("承上结余") == ""

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

    def test_card_spending_posting_date(self, statement: Path) -> None:
        # The Date column is the trade; "Transaction Date" is the later posting.
        txns = WeLabProvider().parse(statement)
        spend = next(t for t in txns if t.order_id == "FT30C3")
        assert spend.date == date(2030, 1, 20)
        assert spend.post_date == date(2030, 1, 22)
        assert "transaction_date" not in spend.metadata

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
        # Exchange classification is driven by the Type column, not by
        # description text — a refund is never treated as an exchange leg.
        txns = WeLabProvider().parse(statement)
        refund = next(t for t in txns if t.order_id == "FT30D4")
        assert refund.description != "Foreign currency exchange"
        exchanges = [t for t in txns if t.description == "Foreign currency exchange"]
        assert all(t.order_id == "FX30000001" for t in exchanges)

    def test_running_balances(self, statement: Path) -> None:
        txns = WeLabProvider().parse(statement)
        balances = [(t.currency, t.metadata.get("balance")) for t in txns]
        # A nil opening ("-") and a section without one both start from zero.
        assert balances == [
            ("HKD", "200.00"),
            ("HKD", "90.00"),
            ("HKD", "80.00"),
            ("CNY", "100.00"),
            ("CNY", "50.00"),
            ("CNY", "80.00"),
        ]


class TestTableLayout:
    @staticmethod
    def _cny_statement(rows: list[tuple[str, list[str], list[str], str]]) -> _Statement:
        st = _Statement()
        st.put(40, 75, "Your Bank Statement (1 Jan 2030 - 31 Jan 2030)")
        st.section("核心账户 Core Account (1010000000) - 人民币 CNY")
        st.balance("1 Jan 2030", _OPENING, "150.00")
        for row in rows:
            st.row(*row)
        return st

    def test_posting_order_drives_balances(self, tmp_path: Path) -> None:
        # Listed by trade date, the spending posts after the later-listed income.
        st = self._cny_statement(
            [
                (
                    "5 Jan 2030",
                    ["借记卡消费", "Debit Card spending"],
                    [
                        "WEIXIN*shop-a CHN Online",
                        "Ref: FT01, Transaction Date: 8 Jan 2030",
                    ],
                    "-40.00",
                ),  # fmt: skip
                (
                    "6 Jan 2030",
                    ["收款", "Receive money"],
                    ["Receive money from payer-b", "Ref: FT02"],
                    "10.00",
                ),  # fmt: skip
            ]
        )
        st.balance("31 Jan 2030", _CLOSING, "120.00")
        txns = WeLabProvider().parse(st.save(tmp_path / "s.pdf"))
        assert [(t.order_id, t.metadata["balance"]) for t in txns] == [
            ("FT02", "160.00"),
            ("FT01", "120.00"),
        ]

    def test_long_detail_stays_in_its_row(self, tmp_path: Path) -> None:
        detail = ["WEIXIN*shop-a CHN Online", "line-b", "line-c", "Ref: FT01, FX Ref: FX09"]  # fmt: skip
        st = self._cny_statement(
            [
                ("5 Jan 2030", ["借记卡消费", "Debit Card spending"], detail, "-40.00"),
                (
                    "6 Jan 2030",
                    ["收款", "Receive money"],
                    ["Receive money from payer-b", "Ref: FT02"],
                    "10.00",
                ),  # fmt: skip
            ]
        )
        st.balance("31 Jan 2030", _CLOSING, "120.00")
        txns = WeLabProvider().parse(st.save(tmp_path / "s.pdf"))
        spend, receive = sorted(txns, key=lambda t: t.date)
        assert spend.order_id == "FT01"
        assert spend.metadata["fx_ref"] == "FX09"
        assert receive.description == "Receive money from payer-b"

    def test_traditional_chinese_labels(self, tmp_path: Path) -> None:
        st = _Statement(font="china-t")
        st.put(40, 75, "Your Bank Statement (1 Jan 2030 - 31 Jan 2030)")
        st.section(
            "核心帳戶 Core Account (1010000000) - 港元 HKD",
            ("日期", "種類", "交易詳情", "金額"),
        )
        st.balance("1 Jan 2030", "承上結餘 Balance From Previous Statement", "150.00")
        st.row("5 Jan 2030", ["外幣兌換", "Foreign currency exchange"], ["CNY/HKD @ 1.10", "Ref: FX01"], "-20.00")  # fmt: skip
        st.row("6 Jan 2030", ["借記卡退款", "Debit Card Refund"], ["WEIXIN*shop-a CHN Online", "Ref: FT02"], "10.00")  # fmt: skip
        st.balance("31 Jan 2030", "帳戶結餘 Closing Balance", "140.00")
        txns = WeLabProvider().parse(st.save(tmp_path / "s.pdf"))
        exchange, refund = txns
        assert exchange.description == "Foreign currency exchange"
        assert refund.tags == ["refund"]
        assert refund.metadata["balance"] == "140.00"

    def test_unbalanced_section_warns_and_stamps_nothing(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        st = self._cny_statement(
            [
                (
                    "6 Jan 2030",
                    ["收款", "Receive money"],
                    ["Receive money from payer-b", "Ref: FT02"],
                    "10.00",
                )
            ]  # fmt: skip
        )
        st.balance("31 Jan 2030", _CLOSING, "170.00")
        with caplog.at_level(logging.WARNING):
            txns = WeLabProvider().parse(st.save(tmp_path / "s.pdf"))
        assert len(txns) == 1
        assert "balance" not in txns[0].metadata
        assert "off by 10.00" in caplog.text


class TestMultiPageContinuation:
    """A currency section spanning pages: continuation pages carry no marker."""

    def test_continuation_rows_captured(self, tmp_path: Path) -> None:
        st = _Statement()
        st.put(40, 90, "寰球钱包 核心账户 Global Wallet Core Account")
        st.put(40, 110, "Your Bank Statement (1 Jan 2030 - 31 Jan 2030)")
        st.section("核心账户 Core Account (1010000000) - 人民币 CNY")
        st.row("5 Jan 2030", ["借记卡消费", "Debit Card spending"], ["WEIXIN*商户A CHN Online", "Ref: FT3001"], "-20.00")  # fmt: skip
        st.put(280, 818, "Page 1 of 2")
        st.new_page()
        # Repeated statement header on the right side (must not create rows)
        st.put(357, 42, "Your Bank Statement (1 Jan 2030 - 31 Jan 2030)")
        st.put(513, 61, "2 Feb 2030")
        st.row("6 Jan 2030", ["借记卡消费", "Debit Card spending"], ["WEIXIN*商户B CHN Online", "Ref: FT3002"], "-30.00")  # fmt: skip
        st.row("7 Jan 2030", ["借记卡消费", "Debit Card spending"], ["WEIXIN*商户C CHN Online", "Ref: FT3003"], "-40.00")  # fmt: skip
        st.balance("31 Jan 2030", _CLOSING, "-90.00")
        st.put(280, 818, "Page 2 of 2")
        txns = WeLabProvider().parse(st.save(tmp_path / "welab_multipage.pdf"))
        assert {t.order_id for t in txns} == {"FT3001", "FT3002", "FT3003"}
        assert all(t.currency == "CNY" for t in txns)
        assert txns[-1].metadata["balance"] == "-90.00"


class TestEmptyStatement:
    def test_no_transactions(self, tmp_path: Path) -> None:
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
