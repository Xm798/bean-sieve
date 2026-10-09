"""End-to-end coverage of Extra detection through ``bean_sieve.api.full_reconcile``.

Goes through ``full_reconcile`` rather than calling ``reconcile()`` directly so the
test exercises the full seam a hand-fed ``covered_ranges`` would bypass: provider
parsing -> ``_collect_covered_ranges`` -> ``Sieve.match``'s range filter -> the
``Extra`` entries in the result.
"""

import quopri
from datetime import date
from pathlib import Path

from bean_sieve.api import full_reconcile

HSBCHK_HEADER = (
    "Transaction date,Post date,Description,Billing amount,Billing currency,"
    "Transaction status,Merchant name,Country / region,Area / district,Credit / Debit"
)


def write_hsbchk_csv(tmp_path: Path, body_lines: list[str], filename: str) -> Path:
    file_path = tmp_path / filename
    content = HSBCHK_HEADER + "\n" + "\n".join(body_lines) + "\n"
    file_path.write_text(content, encoding="utf-8")
    return file_path


def write_bosc_eml(tmp_path: Path, html_content: str, filename: str) -> Path:
    encoded = quopri.encodestring(html_content.encode("utf-8")).decode("ascii")
    eml_content = f"""From: creditcard@bosc.cn
Subject: statement
To: test@example.com
MIME-Version: 1.0
Content-Type: text/html; charset="utf-8"
Content-Transfer-Encoding: quoted-printable

{encoded}
"""
    file_path = tmp_path / filename
    file_path.write_text(eml_content, encoding="utf-8")
    return file_path


def test_hsbchk_credit_extra_end_to_end(tmp_path: Path) -> None:
    # Two statement rows on card 8888, derived period (04-02, 04-10) via the
    # per-card row-span path (assign_statement_periods with no header cycle).
    statement = write_hsbchk_csv(
        tmp_path,
        [
            "02/04/2030,03/04/2030,merchant-a,-10.00,CNY,POSTED,merchant-a,"
            "CHINA,CHN,DEBIT",
            "10/04/2030,11/04/2030,merchant-b,-20.00,CNY,POSTED,merchant-b,"
            "CHINA,CHN,DEBIT",
        ],
        filename="TransactionHistory_8888.csv",
    )

    config_path = tmp_path / "bean-sieve.yaml"
    config_path.write_text(
        """
defaults:
  currency: CNY
  date_tolerance: 2

providers:
  hsbchk_credit:
    accounts:
      "8888": Liabilities:CreditCard:HSBCHK:8888
      "9999": Liabilities:CreditCard:HSBCHK:9999
""",
        encoding="utf-8",
    )

    ledger_path = tmp_path / "main.bean"
    ledger_path.write_text(
        """
2020-01-01 open Liabilities:CreditCard:HSBCHK:8888 CNY
2020-01-01 open Liabilities:CreditCard:HSBCHK:9999 CNY
2020-01-01 open Assets:Bank:Other CNY
2020-01-01 open Expenses:FIXME
2020-01-01 open Income:FIXME

2030-04-02 * "payee-matched" "matches statement row"
  Liabilities:CreditCard:HSBCHK:8888  -10.00 CNY
  Expenses:FIXME

2030-04-05 * "payee-x" "extra: inside covered range, unmatched"
  Liabilities:CreditCard:HSBCHK:8888  -30.00 CNY
  Expenses:FIXME

2030-04-11 * "payee-y" "not extra: inside hull, outside covered range"
  Liabilities:CreditCard:HSBCHK:8888  -40.00 CNY
  Expenses:FIXME

2030-04-05 * "payee-z" "not extra: configured card with no statement rows"
  Liabilities:CreditCard:HSBCHK:9999  -50.00 CNY
  Expenses:FIXME

2030-04-05 * "payee-w" "not extra: foreign account"
  Assets:Bank:Other  -60.00 CNY
  Income:FIXME
""",
        encoding="utf-8",
    )

    result = full_reconcile(
        [statement],
        ledger_path,
        config_path=config_path,
        provider_id="hsbchk_credit",
    )

    extra = {
        (entry.txn.date, entry.posting.account) for entry in result.match_result.extra
    }
    assert extra == {(date(2030, 4, 5), "Liabilities:CreditCard:HSBCHK:8888")}
    assert len(result.match_result.missing) == 1
    assert len(result.match_result.matched) == 1


def test_bosc_credit_extra_end_to_end(tmp_path: Path) -> None:
    # Header period drives coverage (按户 path); rows sit inside it but don't
    # span it, so a header-only Extra proves the period isn't derived from rows.
    html = """<html>
<body>
<table><tr><td>对账周期：2030年05月01日-2030年05月31日</td></tr></table>
<table>
    <tr loop2="1">
        <td>2030年05月10日</td>
        <td>2030年05月10日</td>
        <td>payee-a</td>
        <td>10.00+</td>
        <td>1234</td>
    </tr>
    <tr loop2="2">
        <td>2030年05月20日</td>
        <td>2030年05月20日</td>
        <td>payee-b</td>
        <td>20.00+</td>
        <td>1234</td>
    </tr>
</table>
</body>
</html>"""
    statement = write_bosc_eml(
        tmp_path, html, filename="上海银行信用卡2030年05月电子对账单.eml"
    )

    config_path = tmp_path / "bean-sieve.yaml"
    config_path.write_text(
        """
defaults:
  currency: CNY
  date_tolerance: 2

providers:
  bosc_credit:
    accounts:
      "1234": Liabilities:CreditCard:BOSC
""",
        encoding="utf-8",
    )

    ledger_path = tmp_path / "main.bean"
    ledger_path.write_text(
        """
2020-01-01 open Liabilities:CreditCard:BOSC CNY
2020-01-01 open Expenses:FIXME

2030-05-03 * "payee-c" "extra: before first row, inside header period"
  Liabilities:CreditCard:BOSC  -30.00 CNY
  Expenses:FIXME

2030-05-10 * "payee-a" "matches statement row"
  Liabilities:CreditCard:BOSC  -10.00 CNY
  Expenses:FIXME

2030-06-01 * "payee-d" "not extra: inside hull, outside header period"
  Liabilities:CreditCard:BOSC  -40.00 CNY
  Expenses:FIXME
""",
        encoding="utf-8",
    )

    result = full_reconcile(
        [statement],
        ledger_path,
        config_path=config_path,
        provider_id="bosc_credit",
    )

    extra = {
        (entry.txn.date, entry.posting.account) for entry in result.match_result.extra
    }
    assert extra == {(date(2030, 5, 3), "Liabilities:CreditCard:BOSC")}
    assert len(result.match_result.matched) == 1


def test_bosc_credit_two_accounts_extra_end_to_end(tmp_path: Path) -> None:
    # Both statement rows belong to card 1234; card 5678 is configured but
    # silent, and one 按户 statement reports on the whole account set, so the
    # silent card's account is covered by the header period all the same.
    html = """<html>
<body>
<table><tr><td>对账周期：2030年07月01日-2030年07月31日</td></tr></table>
<table>
    <tr loop2="1">
        <td>2030年07月10日</td>
        <td>2030年07月10日</td>
        <td>payee-a</td>
        <td>10.00+</td>
        <td>1234</td>
    </tr>
    <tr loop2="2">
        <td>2030年07月20日</td>
        <td>2030年07月20日</td>
        <td>payee-b</td>
        <td>20.00+</td>
        <td>1234</td>
    </tr>
</table>
</body>
</html>"""
    statement = write_bosc_eml(
        tmp_path, html, filename="上海银行信用卡2030年07月电子对账单.eml"
    )

    config_path = tmp_path / "bean-sieve.yaml"
    config_path.write_text(
        """
defaults:
  currency: CNY
  date_tolerance: 2

providers:
  bosc_credit:
    accounts:
      "1234": Liabilities:CreditCard:BOSC:A
      "5678": Liabilities:CreditCard:BOSC:B
""",
        encoding="utf-8",
    )

    ledger_path = tmp_path / "main.bean"
    ledger_path.write_text(
        """
2020-01-01 open Liabilities:CreditCard:BOSC:A CNY
2020-01-01 open Liabilities:CreditCard:BOSC:B CNY
2020-01-01 open Expenses:FIXME

2030-07-10 * "payee-a" "matches statement row"
  Liabilities:CreditCard:BOSC:A  -10.00 CNY
  Expenses:FIXME

2030-07-15 * "payee-x" "extra: statement card, inside header period"
  Liabilities:CreditCard:BOSC:A  -30.00 CNY
  Expenses:FIXME

2030-07-15 * "payee-y" "extra: silent card's account, inside header period"
  Liabilities:CreditCard:BOSC:B  -40.00 CNY
  Expenses:FIXME

2030-08-01 * "payee-z" "not extra: inside hull, outside header period"
  Liabilities:CreditCard:BOSC:A  -50.00 CNY
  Expenses:FIXME
""",
        encoding="utf-8",
    )

    result = full_reconcile(
        [statement],
        ledger_path,
        config_path=config_path,
        provider_id="bosc_credit",
    )

    extra = {
        (entry.txn.date, entry.posting.account) for entry in result.match_result.extra
    }
    assert extra == {
        (date(2030, 7, 15), "Liabilities:CreditCard:BOSC:A"),
        (date(2030, 7, 15), "Liabilities:CreditCard:BOSC:B"),
    }
    assert len(result.match_result.missing) == 1


CMB_DEBIT_CONFIG = """
defaults:
  currency: CNY
  date_tolerance: 2

providers:
  cmb_debit:
    accounts:
      "1111": Assets:Bank:CMB:A
      "2222": Assets:Bank:CMB:B
"""

CMB_DEBIT_OPENS = """
2020-01-01 open Assets:Bank:CMB:A CNY
2020-01-01 open Assets:Bank:CMB:B CNY
2020-01-01 open Expenses:FIXME
"""


def write_cmb_debit_csv(
    tmp_path: Path, card_suffix: str, rows: list[tuple[str, str]]
) -> Path:
    """Write a CMB debit export with one (YYYYMMDD, expense) row per entry."""
    lines = [
        '"# 招商银行交易记录"',
        f'"# 账    号: [一卡通:6214********{card_suffix}   招商银行]"',
        "",
        '"交易日期","交易时间","收入","支出","余额","交易类型","交易备注"',
    ]
    lines += [
        f'"\t{day}","\t12:00:00","","{expense}","100.00","type-a","\tremark-a"'
        for day, expense in rows
    ]
    file_path = tmp_path / f"CMB_{card_suffix}.csv"
    file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return file_path


def run_cmb_debit(tmp_path: Path, statements: list[Path], ledger: str):
    config_path = tmp_path / "bean-sieve.yaml"
    config_path.write_text(CMB_DEBIT_CONFIG, encoding="utf-8")
    ledger_path = tmp_path / "main.bean"
    ledger_path.write_text(CMB_DEBIT_OPENS + ledger, encoding="utf-8")
    result = full_reconcile(
        statements, ledger_path, config_path=config_path, provider_id="cmb_debit"
    )
    return {
        (entry.txn.date, entry.posting.account) for entry in result.match_result.extra
    }


def test_debit_statement_covers_only_its_own_card(tmp_path: Path) -> None:
    # A debit export carries one card, so card B's ledger entries inside card
    # A's row span are not something A's file can contradict.
    statement_a = write_cmb_debit_csv(
        tmp_path, "1111", [("20300302", "10.00"), ("20300310", "20.00")]
    )
    extra = run_cmb_debit(
        tmp_path,
        [statement_a],
        """
2030-03-02 * "payee-a" "matches card A row"
  Assets:Bank:CMB:A  -10.00 CNY
  Expenses:FIXME

2030-03-10 * "payee-b" "matches card A row"
  Assets:Bank:CMB:A  -20.00 CNY
  Expenses:FIXME

2030-03-05 * "payee-x" "extra: card A, unmatched"
  Assets:Bank:CMB:A  -30.00 CNY
  Expenses:FIXME

2030-03-05 * "payee-y" "not extra: card B has no statement loaded"
  Assets:Bank:CMB:B  -40.00 CNY
  Expenses:FIXME
""",
    )
    assert extra == {(date(2030, 3, 5), "Assets:Bank:CMB:A")}


def test_debit_transfer_needs_a_row_on_each_card(tmp_path: Path) -> None:
    # Card A's row settles only A's leg of an A->B transfer: B's own export
    # has to show the incoming leg, so its absence there is Extra.
    statement_a = write_cmb_debit_csv(tmp_path, "1111", [("20300310", "50.00")])
    statement_b = write_cmb_debit_csv(
        tmp_path, "2222", [("20300301", "10.00"), ("20300320", "20.00")]
    )
    extra = run_cmb_debit(
        tmp_path,
        [statement_a, statement_b],
        """
2030-03-01 * "payee-b" "matches card B row"
  Assets:Bank:CMB:B  -10.00 CNY
  Expenses:FIXME

2030-03-20 * "payee-c" "matches card B row"
  Assets:Bank:CMB:B  -20.00 CNY
  Expenses:FIXME

2030-03-10 * "transfer-a-to-b" "A leg matches card A row"
  Assets:Bank:CMB:A  -50.00 CNY
  Assets:Bank:CMB:B   50.00 CNY
""",
    )
    assert extra == {(date(2030, 3, 10), "Assets:Bank:CMB:B")}
