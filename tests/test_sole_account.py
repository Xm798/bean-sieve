"""Providers whose statements carry no account number take the sole configured account."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.api import full_reconcile
from bean_sieve.core.types import Transaction
from bean_sieve.providers import PROVIDERS
from bean_sieve.providers.base import BaseProvider
from tests.helpers import run_full_reconcile, write_alipay_csv

LEDGER = """
2020-01-01 open Assets:Fake:Main HKD
2020-01-01 open Assets:Fake:Other HKD
"""


class UnnamedAccountProvider(BaseProvider):
    provider_id = "fake_unnamed_account"
    provider_name = "Fake Unnamed Account"
    supported_formats = [".csv"]
    filename_keywords = ["fake-unnamed"]
    statement_names_account = False

    def parse(self, file_path: Path) -> list[Transaction]:
        return [
            Transaction(
                date=date(2030, 1, 2),
                amount=Decimal("10.00"),
                currency="HKD",
                description="payee-a",
                provider=self.provider_id,
                source_file=file_path,
                metadata={"balance": "90.00"},
            )
        ]


class NamedAccountProvider(UnnamedAccountProvider):
    provider_id = "fake_named_account"
    provider_name = "Fake Named Account"
    filename_keywords = ["fake-named"]
    statement_names_account = True


@pytest.fixture(autouse=True)
def fake_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    for provider in (UnnamedAccountProvider, NamedAccountProvider):
        monkeypatch.setitem(PROVIDERS, provider.provider_id, provider)


def run(tmp_path: Path, statement_name: str, provider_config: str) -> str:
    statement = tmp_path / statement_name
    statement.write_text("date,amount\n", encoding="utf-8")
    config_path = tmp_path / "bean-sieve.yaml"
    config_path.write_text(
        f"defaults:\n  currency: HKD\nproviders:\n{provider_config}",
        encoding="utf-8",
    )
    ledger_path = tmp_path / "main.bean"
    ledger_path.write_text(LEDGER, encoding="utf-8")
    output_path = tmp_path / "pending.bean"
    full_reconcile(
        [statement], ledger_path, config_path=config_path, output_path=output_path
    )
    return output_path.read_text(encoding="utf-8")


def test_sole_configured_account_takes_every_row(tmp_path: Path) -> None:
    output = run(
        tmp_path,
        "fake-unnamed.csv",
        "  fake_unnamed_account:\n"
        "    accounts:\n"
        '      "default": Assets:Fake:Main\n'
        "    balance: true\n",
    )

    assert "Assets:Fake:Main  -10.00 HKD" in output
    assert "Assets:FIXME" not in output
    assert "card_last4" not in output
    assert "2030-01-03 balance Assets:Fake:Main" in output
    assert "90.00 HKD" in output


def test_several_configured_accounts_leave_rows_unassigned(tmp_path: Path) -> None:
    output = run(
        tmp_path,
        "fake-unnamed.csv",
        "  fake_unnamed_account:\n"
        "    accounts:\n"
        '      "a": Assets:Fake:Main\n'
        '      "b": Assets:Fake:Other\n',
    )

    assert "Assets:FIXME" in output
    assert "Assets:Fake:Main" not in output


def test_provider_naming_its_account_keeps_unkeyed_rows_unassigned(
    tmp_path: Path,
) -> None:
    output = run(
        tmp_path,
        "fake-named.csv",
        '  fake_named_account:\n    accounts:\n      "1234": Assets:Fake:Main\n',
    )

    assert "Assets:FIXME" in output


def test_platform_row_paid_from_the_sole_account_is_deduplicated(
    tmp_path: Path,
) -> None:
    bank_statement = tmp_path / "fake-unnamed.csv"
    bank_statement.write_text("date,amount\n", encoding="utf-8")
    alipay_statement = write_alipay_csv(
        tmp_path,
        [
            "2030-01-02 11:00:00,日用百货,merchant-a,,item-a,支出,10.00,"
            "bank-a储蓄卡(1111),交易成功,ORDER0001,,"
        ],
    )
    config = (
        "defaults:\n  currency: HKD\n"
        "account_mappings:\n"
        '  - pattern: "bank-a储蓄卡(1111)"\n'
        "    account: Assets:Fake:Main\n"
        "providers:\n"
        "  fake_unnamed_account:\n"
        "    accounts:\n"
        '      "default": Assets:Fake:Main\n'
    )

    result = run_full_reconcile(
        tmp_path, [bank_statement, alipay_statement], config, LEDGER
    )

    assert [txn.provider for txn in result.missing] == [
        UnnamedAccountProvider.provider_id
    ]


def test_card_suffix_key_becomes_the_rows_card(tmp_path: Path) -> None:
    output = run(
        tmp_path,
        "fake-unnamed.csv",
        "  fake_unnamed_account:\n"
        "    accounts:\n"
        '      "1234": Assets:Fake:Main\n'
        "rules:\n"
        '  - card_last4: "1234"\n'
        "    contra_account: Expenses:Fake\n",
    )

    assert 'card_last4: "1234"' in output
    assert "Expenses:Fake  10.00 HKD" in output
