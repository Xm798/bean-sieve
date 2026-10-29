"""Tests for ICBC (工商银行) credit card statement provider."""

from datetime import date, time
from decimal import Decimal
from email.header import Header
from pathlib import Path

import fitz
import pytest

from bean_sieve.core.coverage import CoverageEntry, select
from bean_sieve.core.metadata_keys import (
    ORIGINAL_AMOUNT,
    ORIGINAL_CURRENCY,
    STATEMENT_CYCLE,
)
from bean_sieve.providers import auto_detect_provider, get_provider
from bean_sieve.providers.banks.credit.icbc import ICBCCreditProvider

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

# (card, trans date, post date, type, merchant lines, amount, booked amount)
StatementRow = tuple[str, str, str, str, list[str], str, str]

ROWS: list[StatementRow] = [
    (
        "1111",
        "2030-05-02",
        "2030-05-03",
        "消费",
        ["payee-a"],
        "11.70/RMB",
        "11.70/RMB(支出)",
    ),
    (
        "1111",
        "2030-05-04",
        "2030-05-04",
        "消费",
        ["payee-b-long-", "name"],
        "1,234.50/RMB",
        "1,234.50/RMB(支出)",
    ),
    (
        "1111",
        "2030-05-06",
        "2030-05-06",
        "退货",
        ["payee-a"],
        "11.70/RMB",
        "11.70/RMB(存入)",
    ),
    (
        "1111",
        "2030-05-08",
        "2030-05-08",
        "跨行消费",
        ["shop-c"],
        "19.60/HKD",
        "17.30/RMB(支出)",
    ),
    (
        "1111",
        "2030-05-10",
        "2030-05-10",
        "分期付款",
        ["shop-d 第1/3期"],
        "31.20/RMB",
        "31.20/RMB(支出)",
    ),
    (
        "1111",
        "2030-05-12",
        "2030-05-12",
        "年费减免",
        ["年费减免"],
        "0.00/RMB",
        "0.00/RMB(存入)",
    ),
    (
        "1111",
        "2030-05-20",
        "2030-05-20",
        "信用卡还款",
        ["支付宝"],
        "500.00/RMB",
        "500.00/RMB(存入)",
    ),
]
CYCLE_TEXT = "账单周期 2030年5月1日—2030年5月31日"


def write_reissue(
    path: Path, rows: list[StatementRow], cycle: str = CYCLE_TEXT
) -> Path:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)

    def put(x: float, y: float, text: str, size: float = 6) -> None:
        page.insert_text((x, y), text, fontname="china-s", fontsize=size)

    put(60, 100, "2030-06-01 10:00:00 中国工商银行", 13)
    put(40, 200, cycle)
    put(40, 230, "---主卡明细---")
    xs = [20, 55, 120, 185, 240, 360, 460]
    y = 250.0
    for card, trans, post, kind, merchant, amount, booked in rows:
        for x, text in zip(xs[:4], [card, trans, post, kind], strict=True):
            put(x, y, text)
        for i, line in enumerate(merchant):
            put(xs[4], y + i * 12, line)
        put(xs[5], y, amount)
        put(xs[6], y, booked)
        y += 12 * len(merchant) + 10
    put(40, 700, "信用卡对账单（补）", 13)
    doc.save(path)
    doc.close()
    return path


def write_statement_eml(
    path: Path, rows: list[StatementRow], cycle: str = CYCLE_TEXT
) -> Path:
    label, period = cycle.split(" ", 1)
    body = "\n".join(
        f"<tr><td>{card}</td><td>{t}</td><td>{p}</td><td>{k}</td>"
        f"<td>{'<br>'.join(m)}</td><td>{a}</td><td>{b}</td></tr>"
        for card, t, p, k, m, a, b in rows
    )
    html = (
        f"<html><body><table><tr><td>{label}</td><td>{period}</td></tr></table>"
        f"<table><tr><td>---主卡明细---</td></tr>{body}</table></body></html>"
    )
    subject = Header("中国工商银行客户对账单(ICBC Peony Card Bank Statement)", "utf-8")
    path.write_bytes(
        (
            f"Subject: {subject.encode()}\n"
            "MIME-Version: 1.0\n"
            'Content-Type: text/html; charset="utf-8"\n'
            "Content-Transfer-Encoding: 8bit\n\n"
        ).encode("ascii")
        + html.encode("utf-8")
    )
    return path


# (date, time, 借/贷, currency, amount, booked currency, booked amount,
#  balance, counterparty name, counterparty account, summary, place lines)
ExportRow = tuple[str, str, str, str, str, str, str, str, str, str, str, list[str]]

_EXPORT_COLUMNS = [
    ("入账日期", 790),
    ("交易卡号", 720),
    ("收支", 650),
    ("交易币种", 610),
    ("交易金额", 560),
    ("入账币种", 510),
    ("入账金额", 460),
    ("账户余额", 400),
    ("对方户名", 340),
    ("对方账号", 280),
    ("摘要", 220),
    ("交易场所", 160),
]


def write_export(
    path: Path,
    pages: list[list[ExportRow]],
    footer: str = "",
    period: str = "起止日期：2030-05-15 — 2030-06-30",
) -> Path:
    doc = fitz.open()
    for page_no, rows in enumerate(pages):
        page = doc.new_page(width=595, height=842)
        page.set_rotation(90)

        def put(x: float, y: float, text: str, page=page) -> None:
            # china-s advances every character, ASCII included, by one em.
            page.insert_text(
                (x, y + len(text) * 2.5),
                text,
                fontname="china-s",
                fontsize=5,
                rotate=90,
            )

        put(30, 400, "中国工商银行信用卡历史明细（电子版）")
        if period:
            put(55, 330, period)
        for label, y in _EXPORT_COLUMNS:
            if label == "收支":
                put(76, y, "收")
                put(83, y, "支")
            else:
                put(80, y, label)
        xs = [100.0]
        for row in rows[:-1]:
            xs.append(xs[-1] + 7 * max(2, len(row[-1])) + 4)
        # Column-major, so each cell stays its own extracted line.
        for x, row in zip(xs, rows, strict=True):
            put(x, 790, row[0])
            put(x + 7, 790, row[1])
        for x in xs:
            put(x + 3.5, 720, "6222000000001111")
        for col, (_, y) in enumerate(_EXPORT_COLUMNS[2:11], start=2):
            for x, row in zip(xs, rows, strict=True):
                if row[col]:
                    put(x + 3.5, y, row[col])
        for x, row in zip(xs, rows, strict=True):
            for i, line in enumerate(row[-1]):
                put(x + i * 7, 160, line)
        if footer and page_no == len(pages) - 1:
            put(xs[-1] + 22, 400, footer)
    doc.save(path)
    doc.close()
    return path


EXPORT_PAGE_1: list[ExportRow] = [
    (
        "2030-05-20",
        "08:00:00",
        "贷",
        "人民币",
        "500.00",
        "人民币",
        "500.00",
        "-11.70",
        "name-x",
        "6222****0000",
        "转帐",
        ["支付宝"],
    ),
    (
        "2030-06-02",
        "12:30:00",
        "借",
        "人民币",
        "42.60",
        "人民币",
        "42.60",
        "-50.00",
        "",
        "",
        "消费",
        ["payee-e-long-", "name"],
    ),
]
EXPORT_PAGE_2: list[ExportRow] = [
    (
        "2030-06-05",
        "09:00:00",
        "借",
        "港币",
        "19.60",
        "人民币",
        "17.30",
        "-68.00",
        "",
        "",
        "消费",
        ["shop-c"],
    ),
    (
        "2030-06-07",
        "10:00:00",
        "贷",
        "人民币",
        "42.60",
        "人民币",
        "42.60",
        "-28.00",
        "",
        "",
        "退货",
        ["payee-e"],
    ),
    # A reversal of an earlier charge: 借 with a negative amount.
    (
        "2030-06-09",
        "11:00:00",
        "借",
        "人民币",
        "-17.30",
        "人民币",
        "-17.30",
        "-10.70",
        "",
        "",
        "消费",
        ["shop-c"],
    ),
]


@pytest.fixture
def provider() -> ICBCCreditProvider:
    return ICBCCreditProvider()


class TestDetection:
    def test_registered(self):
        assert isinstance(get_provider("icbc_credit"), ICBCCreditProvider)
        assert ICBCCreditProvider.per_card_statement is True

    def test_detects_every_source(self, tmp_path):
        files = [
            write_reissue(tmp_path / "a.pdf", ROWS),
            write_statement_eml(tmp_path / "b.eml", ROWS),
            write_export(tmp_path / "c.pdf", [EXPORT_PAGE_1]),
        ]
        for path in files:
            detected = auto_detect_provider(path)
            assert detected is not None
            assert detected.provider_id == "icbc_credit"

    def test_example_statements_detect_as_their_own_provider(self):
        """No other example PDF or eml is claimed by icbc_credit, nor the reverse."""
        for path in sorted(EXAMPLES.glob("*/*")):
            if path.suffix.lower() not in (".pdf", ".eml"):
                continue
            detected = auto_detect_provider(path)
            assert detected is not None, path
            assert detected.provider_id == path.parent.name, path


class TestReissue:
    def test_rows(self, provider, tmp_path):
        txns = provider.parse(write_reissue(tmp_path / "r.pdf", ROWS))
        assert [(t.date, t.amount, t.description) for t in txns] == [
            (date(2030, 5, 2), Decimal("11.70"), "消费 payee-a"),
            (date(2030, 5, 4), Decimal("1234.50"), "消费 payee-b-long-name"),
            (date(2030, 5, 6), Decimal("-11.70"), "退货 payee-a"),
            (date(2030, 5, 8), Decimal("17.30"), "跨行消费 shop-c"),
            (date(2030, 5, 10), Decimal("31.20"), "分期付款 shop-d 第1/3期"),
            (date(2030, 5, 20), Decimal("-500.00"), "信用卡还款 支付宝"),
        ]
        first = txns[0]
        assert first.post_date == date(2030, 5, 3)
        assert first.card_last4 == "1111"
        assert first.currency == "CNY"
        assert first.metadata["transaction_type"] == "消费"

    def test_foreign_currency_keeps_original(self, provider, tmp_path):
        txns = provider.parse(write_reissue(tmp_path / "r.pdf", ROWS))
        fx = next(t for t in txns if t.description.startswith("跨行消费"))
        assert fx.metadata[ORIGINAL_AMOUNT] == Decimal("19.60")
        assert fx.metadata[ORIGINAL_CURRENCY] == "HKD"
        assert ORIGINAL_AMOUNT not in txns[0].metadata

    def test_cycle_and_period(self, provider, tmp_path):
        delayed: StatementRow = (
            "1111",
            "2030-04-29",
            "2030-05-01",
            "消费",
            ["payee-z"],
            "4.60/RMB",
            "4.60/RMB(支出)",
        )
        txns = provider.parse(write_reissue(tmp_path / "r.pdf", [delayed, *ROWS]))
        cycle = (date(2030, 5, 1), date(2030, 5, 31))
        assert all(t.metadata[STATEMENT_CYCLE] == cycle for t in txns)
        assert all(t.statement_period == (date(2030, 4, 29), cycle[1]) for t in txns)

    def test_no_rows(self, provider, tmp_path):
        assert provider.parse(write_reissue(tmp_path / "r.pdf", [])) == []


class TestStatementEml:
    def test_same_transactions_as_reissue(self, provider, tmp_path):
        def fields(path: Path):
            return [
                (
                    t.date,
                    t.post_date,
                    t.amount,
                    t.currency,
                    t.description,
                    t.card_last4,
                    t.statement_period,
                    t.metadata,
                )
                for t in provider.parse(path)
            ]

        eml = fields(write_statement_eml(tmp_path / "s.eml", ROWS))
        assert len(eml) == 6
        assert eml == fields(write_reissue(tmp_path / "r.pdf", ROWS))


class TestExport:
    def test_rows_across_pages(self, provider, tmp_path):
        path = write_export(
            tmp_path / "e.pdf",
            [EXPORT_PAGE_1, EXPORT_PAGE_2],
            footer="支出合计：58.00",
        )
        txns = provider.parse(path)
        assert [(t.date, t.time, t.amount, t.description) for t in txns] == [
            (date(2030, 5, 20), time(8, 0), Decimal("-500.00"), "转帐 支付宝"),
            (
                date(2030, 6, 2),
                time(12, 30),
                Decimal("42.60"),
                "消费 payee-e-long-name",
            ),
            (date(2030, 6, 5), time(9, 0), Decimal("17.30"), "消费 shop-c"),
            (date(2030, 6, 7), time(10, 0), Decimal("-42.60"), "退货 payee-e"),
            (date(2030, 6, 9), time(11, 0), Decimal("-17.30"), "消费 shop-c"),
        ]
        assert {t.card_last4 for t in txns} == {"1111"}
        assert all(t.post_date is None for t in txns)
        repayment = txns[0]
        assert repayment.metadata["summary"] == "转帐"
        assert repayment.metadata["counterparty_name"] == "name-x"
        assert repayment.metadata["counterparty_account"] == "6222****0000"
        assert repayment.metadata["balance"] == Decimal("-11.70")
        assert txns[2].metadata[ORIGINAL_AMOUNT] == Decimal("19.60")
        assert txns[2].metadata[ORIGINAL_CURRENCY] == "HKD"
        assert all(
            t.metadata[STATEMENT_CYCLE] == (date(2030, 5, 15), date(2030, 6, 30))
            for t in txns
        )


class TestExportEdges:
    def test_without_range_warns_and_covers_nothing(self, provider, tmp_path, caplog):
        path = write_export(tmp_path / "e.pdf", [EXPORT_PAGE_1], period="")
        with caplog.at_level("WARNING"):
            txns = provider.parse(path)
        assert len(txns) == 2
        assert provider.coverage(path, txns) == []
        assert "起止日期" in caplog.text

    def test_unreadable_original_amount_is_not_stored(self, provider, tmp_path):
        row: ExportRow = (
            "2030-06-05",
            "09:00:00",
            "借",
            "港币",
            "-",
            "人民币",
            "17.30",
            "-68.00",
            "",
            "",
            "消费",
            ["shop-c"],
        )
        [txn] = provider.parse(write_export(tmp_path / "e.pdf", [[row]]))
        assert ORIGINAL_AMOUNT not in txn.metadata
        assert ORIGINAL_CURRENCY not in txn.metadata


class TestCoverage:
    def test_sources(self, provider, tmp_path):
        files = {
            "statement": write_statement_eml(tmp_path / "s.eml", ROWS),
            "reissue": write_reissue(tmp_path / "r.pdf", ROWS),
            "export": write_export(tmp_path / "e.pdf", [EXPORT_PAGE_1]),
        }
        for source, path in files.items():
            [cov] = provider.coverage(path, provider.parse(path))
            assert (cov.account, cov.source, cov.clip) == (
                "1111",
                source,
                source == "export",
            )
        [cov] = provider.coverage(files["reissue"], provider.parse(files["reissue"]))
        assert (cov.start, cov.end, len(cov.rows)) == (
            date(2030, 5, 1),
            date(2030, 5, 31),
            6,
        )

    @pytest.mark.parametrize("reverse", [False, True])
    def test_statement_outranks_reissue_and_export_fills_the_rest(
        self, provider, tmp_path, reverse
    ):
        paths = [
            write_reissue(tmp_path / "r.pdf", ROWS),
            write_export(tmp_path / "e.pdf", [EXPORT_PAGE_1, EXPORT_PAGE_2]),
            write_statement_eml(tmp_path / "s.eml", ROWS),
        ]
        if reverse:
            paths.reverse()
        ranks = {kind: i for i, kind in enumerate(provider.coverage_priority)}
        parsed = {path: provider.parse(path) for path in paths}
        entries = [
            CoverageEntry("icbc_credit", path, cov, ranks[cov.source])
            for path in paths
            for cov in provider.coverage(path, parsed[path])
        ]
        kept = select(entries).keep([t for rows in parsed.values() for t in rows])
        by_file = {
            path.name: sum(t.source_file == path for t in kept) for path in paths
        }
        # The export's May row is inside the statement cycle; its June rows stay.
        assert by_file == {"s.eml": 6, "r.pdf": 0, "e.pdf": 4}
