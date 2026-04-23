"""Tests for BeancountWriter."""

from datetime import date
from decimal import Decimal

from bean_sieve.core.output import BeancountWriter
from bean_sieve.core.types import Transaction


def test_shared_account_posting_emits_card_last4():
    writer = BeancountWriter(
        output_metadata=["source"],
        shared_accounts={"Liabilities:Credit:HXB"},
    )
    txn = Transaction(
        date=date(2025, 3, 15),
        amount=Decimal("28.00"),
        currency="CNY",
        description="拿铁",
        payee="瑞幸咖啡",
        card_last4="3855",
        account="Liabilities:Credit:HXB",
        contra_account="Expenses:Food:Coffee",
        provider="alipay",
    )
    output = writer.format_transaction(txn)
    assert 'card_last4: "3855"' in output
    assert "Liabilities:Credit:HXB" in output


def test_non_shared_account_omits_card_last4_posting_meta():
    writer = BeancountWriter(output_metadata=["source"], shared_accounts=set())
    txn = Transaction(
        date=date(2025, 3, 15),
        amount=Decimal("28.00"),
        currency="CNY",
        description="拿铁",
        payee="瑞幸咖啡",
        card_last4="3855",
        account="Assets:Bank:CCB:1386",
        contra_account="Expenses:Food:Coffee",
        provider="alipay",
    )
    output = writer.format_transaction(txn)
    assert 'card_last4: "3855"' not in output


def test_explicit_posting_metadata_does_not_duplicate():
    """Explicit _posting_metadata + shared account -> single card_last4 line."""
    writer = BeancountWriter(
        output_metadata=["source"],
        shared_accounts={"Liabilities:Credit:HXB"},
    )
    txn = Transaction(
        date=date(2025, 3, 15),
        amount=Decimal("28.00"),
        currency="CNY",
        description="拿铁",
        payee="瑞幸咖啡",
        card_last4="3855",
        account="Liabilities:Credit:HXB",
        contra_account="Expenses:Food:Coffee",
        provider="alipay",
        metadata={"_posting_metadata": ["card_last4"]},
    )
    output = writer.format_transaction(txn)
    assert output.count('card_last4: "3855"') == 1
