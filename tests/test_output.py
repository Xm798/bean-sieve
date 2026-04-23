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


def test_format_result_renders_meta_diagnostics_section():
    from bean_sieve.core.types import MatchResult, MetaDiagnostic, ReconcileResult

    diagnostics = [
        MetaDiagnostic(
            severity="hint",
            file="books/2025/q1.bean",
            line=1234,
            account="Liabilities:Credit:HXB",
            key="card_last4",
            expected="3855",
            actual=None,
            message='books/2025/q1.bean:1234  hint  missing posting meta `card_last4: "3855"` on Liabilities:Credit:HXB',
        ),
        MetaDiagnostic(
            severity="warn",
            file="books/2025/q2.bean",
            line=88,
            account="Liabilities:Credit:SPDB",
            key="card_last4",
            expected="3855",
            actual="4192",
            message='books/2025/q2.bean:88  warn  posting meta `card_last4` mismatch on Liabilities:Credit:SPDB: ledger "4192", statement "3855"',
        ),
    ]
    mr = MatchResult(meta_diagnostics=diagnostics)
    result = ReconcileResult(match_result=mr)

    writer = BeancountWriter()
    output = writer.format_result(result)

    assert "Metadata diagnostics (2)" in output
    assert "books/2025/q1.bean:1234  hint  missing posting meta" in output
    assert "books/2025/q2.bean:88  warn  posting meta `card_last4` mismatch" in output


def test_format_result_omits_section_when_no_diagnostics():
    from bean_sieve.core.types import MatchResult, ReconcileResult

    result = ReconcileResult(match_result=MatchResult())
    writer = BeancountWriter()
    output = writer.format_result(result)
    assert "Metadata diagnostics" not in output


def test_format_result_sorts_diagnostics():
    """Diagnostics sorted by (file, line, severity)."""
    from bean_sieve.core.types import MatchResult, MetaDiagnostic, ReconcileResult

    diagnostics = [
        MetaDiagnostic(
            severity="warn",
            file="books/b.bean",
            line=10,
            account="A",
            key="card_last4",
            expected="1",
            actual="2",
            message="books/b.bean:10  warn  msg",
        ),
        MetaDiagnostic(
            severity="hint",
            file="books/a.bean",
            line=50,
            account="A",
            key="card_last4",
            expected="1",
            actual=None,
            message="books/a.bean:50  hint  msg",
        ),
        MetaDiagnostic(
            severity="hint",
            file="books/a.bean",
            line=10,
            account="A",
            key="card_last4",
            expected="1",
            actual=None,
            message="books/a.bean:10  hint  msg",
        ),
    ]
    mr = MatchResult(meta_diagnostics=diagnostics)
    result = ReconcileResult(match_result=mr)
    writer = BeancountWriter()
    output = writer.format_result(result)

    idx_a10 = output.index("books/a.bean:10")
    idx_a50 = output.index("books/a.bean:50")
    idx_b10 = output.index("books/b.bean:10")
    assert idx_a10 < idx_a50 < idx_b10
