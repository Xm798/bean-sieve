"""Coverage scope of 按户 (non per-card) providers in ``BaseProvider``.

Pins which accounts a statement period is attributed to when one provider
config maps several keys to different accounts, and the preserving rule that
keeps platform statements (alipay/wechat/zabank) covering every configured
account.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

from bean_sieve.config import Config
from bean_sieve.config.schema import ProviderConfig
from bean_sieve.core.types import Transaction
from bean_sieve.providers.base import BaseProvider

ACCOUNT_A = "Liabilities:CreditCard:Fake:A"
ACCOUNT_B = "Liabilities:CreditCard:Fake:B"

PERIOD_JAN = (date(2030, 1, 1), date(2030, 1, 31))
PERIOD_FEB = (date(2030, 2, 1), date(2030, 2, 28))


class FakeAccountBank(BaseProvider):
    """按户 provider; not registered, so it never affects auto-detection."""

    provider_id = "fake_account_bank"
    provider_name = "Fake Account Bank"
    supported_formats = [".csv"]

    def parse(self, file_path: Path) -> list[Transaction]:  # noqa: ARG002
        return []


def make_config(accounts: dict[str, str]) -> Config:
    return Config(
        providers={FakeAccountBank.provider_id: ProviderConfig(accounts=accounts)}
    )


def make_txn(
    day: date,
    card_last4: str | None,
    statement_period: tuple[date, date],
) -> Transaction:
    return Transaction(
        date=day,
        amount=Decimal("10.00"),
        currency="CNY",
        description="row",
        card_last4=card_last4,
        provider=FakeAccountBank.provider_id,
        statement_period=statement_period,
    )


def test_each_card_period_lands_on_its_own_account() -> None:
    provider = FakeAccountBank()
    config = make_config({"1111": ACCOUNT_A, "2222": ACCOUNT_B})
    transactions = [
        make_txn(date(2030, 1, 5), "1111", PERIOD_JAN),
        make_txn(date(2030, 2, 5), "2222", PERIOD_FEB),
    ]

    assert provider.get_covered_ranges(transactions, config) == {
        ACCOUNT_A: [PERIOD_JAN],
        ACCOUNT_B: [PERIOD_FEB],
    }


def test_account_without_rows_is_not_covered() -> None:
    provider = FakeAccountBank()
    config = make_config({"1111": ACCOUNT_A, "2222": ACCOUNT_B})
    transactions = [make_txn(date(2030, 1, 5), "1111", PERIOD_JAN)]

    assert provider.get_covered_ranges(transactions, config) == {
        ACCOUNT_A: [PERIOD_JAN],
    }
    assert provider.get_covered_accounts(transactions, config) == [ACCOUNT_A]


def test_untagged_row_period_reaches_every_configured_account() -> None:
    provider = FakeAccountBank()
    config = make_config({"1111": ACCOUNT_A, "2222": ACCOUNT_B})
    transactions = [make_txn(date(2030, 1, 5), None, PERIOD_JAN)]

    assert provider.get_covered_ranges(transactions, config) == {
        ACCOUNT_A: [PERIOD_JAN],
        ACCOUNT_B: [PERIOD_JAN],
    }
    assert provider.get_covered_accounts(transactions, config) == [
        ACCOUNT_A,
        ACCOUNT_B,
    ]


def test_unknown_card_period_reaches_every_configured_account() -> None:
    provider = FakeAccountBank()
    config = make_config({"1111": ACCOUNT_A, "2222": ACCOUNT_B})
    transactions = [make_txn(date(2030, 1, 5), "9999", PERIOD_JAN)]

    assert provider.get_covered_ranges(transactions, config) == {
        ACCOUNT_A: [PERIOD_JAN],
        ACCOUNT_B: [PERIOD_JAN],
    }
    assert provider.get_covered_accounts(transactions, config) == [
        ACCOUNT_A,
        ACCOUNT_B,
    ]


def test_platform_statement_covers_every_wallet() -> None:
    # Payment platforms key their config on payment methods while card_last4
    # holds a bank-card suffix, so no row is attributable to a single wallet.
    provider = FakeAccountBank()
    wallets = {
        "余额": "Assets:Wallet:Fake:Balance",
        "零钱通": "Assets:Wallet:Fake:Fund",
        "余额宝": "Assets:Wallet:Fake:Yeb",
    }
    config = make_config(wallets)
    transactions = [
        make_txn(date(2030, 1, 5), "1234", PERIOD_JAN),
        make_txn(date(2030, 1, 6), None, PERIOD_JAN),
    ]

    assert provider.get_covered_ranges(transactions, config) == {
        account: [PERIOD_JAN] for account in wallets.values()
    }
    assert provider.get_covered_accounts(transactions, config) == list(wallets.values())


def test_periods_of_one_account_stay_distinct() -> None:
    provider = FakeAccountBank()
    config = make_config({"1111": ACCOUNT_A, "2222": ACCOUNT_A})
    transactions = [
        make_txn(date(2030, 1, 5), "1111", PERIOD_JAN),
        make_txn(date(2030, 2, 5), "2222", PERIOD_FEB),
    ]

    assert provider.get_covered_ranges(transactions, config) == {
        ACCOUNT_A: [PERIOD_JAN, PERIOD_FEB],
    }


def test_no_statement_period_disables_range_filtering() -> None:
    provider = FakeAccountBank()
    config = make_config({"1111": ACCOUNT_A, "2222": ACCOUNT_B})
    transactions = [
        Transaction(
            date=date(2030, 1, 5),
            amount=Decimal("10.00"),
            currency="CNY",
            description="row",
            card_last4="1111",
            provider=FakeAccountBank.provider_id,
        )
    ]

    assert provider.get_covered_ranges(transactions, config) is None
    assert provider.get_covered_accounts(transactions, config) == [
        ACCOUNT_A,
        ACCOUNT_B,
    ]
