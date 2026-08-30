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
