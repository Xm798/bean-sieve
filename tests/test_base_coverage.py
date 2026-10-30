"""Coverage scope of ``BaseProvider``, read as a table of ``per_card_statement``.

Pins which accounts a statement period is attributed to when one provider
config maps several keys to different accounts, the preserving rule that keeps
platform statements (alipay/wechat/zabank) covering every configured account,
and what a per-card statement refuses to claim. Also pins how
``assign_statement_periods`` derives those periods, and how ``stamp_cycle`` and
``cycle_coverages`` turn a printed cycle into one coverage per card.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

from bean_sieve.config import Config
from bean_sieve.config.schema import ProviderConfig
from bean_sieve.core.metadata_keys import STATEMENT_CYCLE
from bean_sieve.core.types import Transaction
from bean_sieve.providers.base import BaseProvider

ACCOUNT_A = "Liabilities:CreditCard:Fake:A"
ACCOUNT_B = "Liabilities:CreditCard:Fake:B"

PERIOD_JAN = (date(2030, 1, 1), date(2030, 1, 31))
PERIOD_FEB = (date(2030, 2, 1), date(2030, 2, 28))
PERIOD_MAR = (date(2030, 3, 1), date(2030, 3, 31))


class FakeAccountBank(BaseProvider):
    """按户 provider; not registered, so it never affects auto-detection."""

    provider_id = "fake_account_bank"
    provider_name = "Fake Account Bank"
    supported_formats = [".csv"]

    def parse(self, file_path: Path) -> list[Transaction]:  # noqa: ARG002
        return []


class FakePerCardBank(BaseProvider):
    """Per-card provider; not registered, so it never affects auto-detection."""

    provider_id = "fake_per_card_bank"
    provider_name = "Fake Per Card Bank"
    supported_formats = [".csv"]
    per_card_statement = True

    def parse(self, file_path: Path) -> list[Transaction]:  # noqa: ARG002
        return []


def make_config(
    accounts: dict[str, str],
    provider_id: str = FakeAccountBank.provider_id,
) -> Config:
    return Config(providers={provider_id: ProviderConfig(accounts=accounts)})


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


def test_account_without_rows_takes_the_file_periods() -> None:
    # One statement speaks for the whole household, so the account it never
    # names is still covered - by everything the file spans.
    provider = FakeAccountBank()
    config = make_config({"1111": ACCOUNT_A, "2222": ACCOUNT_B})
    transactions = [make_txn(date(2030, 1, 5), "1111", PERIOD_JAN)]

    assert provider.get_covered_ranges(transactions, config) == {
        ACCOUNT_A: [PERIOD_JAN],
        ACCOUNT_B: [PERIOD_JAN],
    }
    assert provider.get_covered_accounts(transactions, config) == [
        ACCOUNT_A,
        ACCOUNT_B,
    ]


def test_multi_currency_statement_covers_the_silent_currency() -> None:
    # Multi-currency providers key their config on the currency code; a file
    # listing only one currency still reports on the account of the other.
    provider = FakeAccountBank()
    accounts = {"CNY": "Assets:Bank:Fake:CNY", "HKD": "Assets:Bank:Fake:HKD"}
    config = make_config(accounts)
    transactions = [make_txn(date(2030, 1, 5), "CNY", PERIOD_JAN)]

    assert provider.get_covered_ranges(transactions, config) == {
        account: [PERIOD_JAN] for account in accounts.values()
    }
    assert provider.get_covered_accounts(transactions, config) == list(
        accounts.values()
    )


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


def test_untagged_row_period_reaches_an_account_that_named_itself() -> None:
    # An untagged row belongs to the whole statement, so its period lands on
    # every configured account, the ones the file names included.
    provider = FakeAccountBank()
    config = make_config({"1111": ACCOUNT_A, "2222": ACCOUNT_B})
    transactions = [
        make_txn(date(2030, 1, 5), "1111", PERIOD_JAN),
        make_txn(date(2030, 2, 5), None, PERIOD_FEB),
    ]

    assert provider.get_covered_ranges(transactions, config) == {
        ACCOUNT_A: [PERIOD_JAN, PERIOD_FEB],
        ACCOUNT_B: [PERIOD_FEB, PERIOD_JAN],
    }


def test_silent_account_takes_every_period_after_an_untagged_row() -> None:
    # A period an untagged row already handed to the silent account must not
    # stop the rest of the file's periods from reaching it.
    provider = FakeAccountBank()
    config = make_config({"1111": ACCOUNT_A, "2222": ACCOUNT_B})
    transactions = [
        make_txn(date(2030, 1, 5), "1111", PERIOD_JAN),
        make_txn(date(2030, 2, 5), None, PERIOD_FEB),
        make_txn(date(2030, 3, 5), "1111", PERIOD_MAR),
    ]

    assert provider.get_covered_ranges(transactions, config) == {
        ACCOUNT_A: [PERIOD_JAN, PERIOD_FEB, PERIOD_MAR],
        ACCOUNT_B: [PERIOD_FEB, PERIOD_JAN, PERIOD_MAR],
    }


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


def test_cards_sharing_an_account_merge_their_periods() -> None:
    provider = FakePerCardBank()
    config = make_config(
        {"1111": ACCOUNT_A, "2222": ACCOUNT_A}, FakePerCardBank.provider_id
    )
    transactions = [
        make_txn(date(2030, 1, 5), "1111", PERIOD_JAN),
        make_txn(date(2030, 2, 5), "2222", PERIOD_FEB),
    ]

    assert provider.get_covered_ranges(transactions, config) == {
        ACCOUNT_A: [PERIOD_JAN, PERIOD_FEB],
    }
    assert provider.get_covered_accounts(transactions, config) == [ACCOUNT_A]


def test_per_card_statement_claims_nothing_for_a_row_it_cannot_place() -> None:
    provider = FakePerCardBank()
    config = make_config(
        {"1111": ACCOUNT_A, "2222": ACCOUNT_B}, FakePerCardBank.provider_id
    )
    transactions = [
        make_txn(date(2030, 1, 5), None, PERIOD_JAN),
        make_txn(date(2030, 1, 6), "9999", PERIOD_JAN),
    ]

    assert provider.get_covered_ranges(transactions, config) is None
    assert provider.get_covered_accounts(transactions, config) == []


def test_per_card_statement_claims_nothing_without_a_period() -> None:
    provider = FakePerCardBank()
    config = make_config(
        {"1111": ACCOUNT_A, "2222": ACCOUNT_B}, FakePerCardBank.provider_id
    )
    transactions = [
        Transaction(
            date=date(2030, 1, 5),
            amount=Decimal("10.00"),
            currency="CNY",
            description="row",
            card_last4="1111",
            provider=FakePerCardBank.provider_id,
        )
    ]

    assert provider.get_covered_ranges(transactions, config) is None
    assert provider.get_covered_accounts(transactions, config) == []


def make_bare_txn(
    day: date, card_last4: str, post_date: date | None = None
) -> Transaction:
    return Transaction(
        date=day,
        post_date=post_date,
        amount=Decimal("10.00"),
        currency="CNY",
        description="row",
        card_last4=card_last4,
        provider=FakeAccountBank.provider_id,
    )


def test_rows_booked_in_the_cycle_leave_the_period_at_the_cycle() -> None:
    # An installment carries its original purchase date, a delayed settlement
    # its trade date; the cycle books both.
    installment = make_bare_txn(date(2029, 11, 5), "1111", date(2030, 1, 3))
    delayed = make_bare_txn(date(2029, 12, 30), "1111", date(2030, 1, 2))
    inside = make_bare_txn(date(2030, 1, 10), "2222")

    BaseProvider.assign_statement_periods([installment, delayed, inside], PERIOD_JAN)

    assert installment.statement_period == PERIOD_JAN
    assert delayed.statement_period == PERIOD_JAN
    assert inside.statement_period == PERIOD_JAN


def test_a_row_booked_outside_the_cycle_widens_only_its_card() -> None:
    # A provider whose cycle is an approximation (a calendar month) books rows
    # outside it; the statement still speaks for those days.
    outside = make_bare_txn(date(2029, 12, 27), "1111", date(2029, 12, 28))
    inside = make_bare_txn(date(2030, 1, 10), "2222")

    BaseProvider.assign_statement_periods([outside, inside], PERIOD_JAN)

    assert outside.statement_period == (date(2029, 12, 28), PERIOD_JAN[1])
    assert inside.statement_period == PERIOD_JAN


def test_missing_cycle_falls_back_to_each_card_row_span() -> None:
    first = make_bare_txn(date(2030, 1, 5), "1111")
    last = make_bare_txn(date(2030, 1, 20), "1111")
    other = make_bare_txn(date(2030, 2, 14), "2222")

    BaseProvider.assign_statement_periods([first, last, other])

    span = (date(2030, 1, 5), date(2030, 1, 20))
    assert first.statement_period == span
    assert last.statement_period == span
    assert other.statement_period == (date(2030, 2, 14), date(2030, 2, 14))


def test_stamp_cycle_records_the_printed_cycle_beside_the_widened_period() -> None:
    delayed = make_bare_txn(date(2030, 1, 20), "1111", post_date=date(2030, 2, 2))

    BaseProvider.stamp_cycle([delayed], PERIOD_JAN)

    assert delayed.statement_period == (date(2030, 1, 1), date(2030, 2, 2))
    assert delayed.metadata[STATEMENT_CYCLE] == PERIOD_JAN


def test_stamp_cycle_without_a_cycle_records_none() -> None:
    row = make_bare_txn(date(2030, 1, 5), "1111")

    BaseProvider.stamp_cycle([row], None)

    assert STATEMENT_CYCLE not in row.metadata


def test_cycle_coverages_cover_each_card_for_the_printed_cycle() -> None:
    delayed = make_bare_txn(date(2030, 1, 20), "1111", post_date=date(2030, 2, 2))
    other = make_bare_txn(date(2030, 1, 8), "2222")
    unkeyed = make_bare_txn(date(2030, 1, 9), "")
    BaseProvider.stamp_cycle([delayed, other, unkeyed], PERIOD_JAN)
    unstamped = make_bare_txn(date(2030, 1, 10), "1111")

    coverages = BaseProvider.cycle_coverages(
        [delayed, other, unkeyed, unstamped], source="export", clip=True
    )

    assert [(c.account, c.start, c.end, c.rows) for c in coverages] == [
        ("1111", *PERIOD_JAN, [delayed]),
        ("2222", *PERIOD_JAN, [other]),
        ("", *PERIOD_JAN, [unkeyed]),
    ]
    assert all(c.source == "export" and c.clip for c in coverages)


class FakeUnnamedPerCardBank(FakePerCardBank):
    """Per-card provider whose file names no card."""

    provider_id = "fake_unnamed_per_card_bank"
    statement_names_account = False


def test_unkeyed_rows_of_an_unnamed_statement_cover_the_sole_account() -> None:
    provider = FakeUnnamedPerCardBank()
    config = make_config({"default": ACCOUNT_A}, provider.provider_id)
    transactions = [make_txn(date(2030, 1, 5), None, PERIOD_JAN)]

    assert provider.get_covered_ranges(transactions, config) == {
        ACCOUNT_A: [PERIOD_JAN],
    }
