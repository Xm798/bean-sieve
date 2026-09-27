"""Tests for BeancountWriter."""

from datetime import date
from decimal import Decimal

from bean_sieve.core.output import BeancountWriter
from bean_sieve.core.types import Transaction


def test_shared_account_posting_emits_card_last4():
    writer = BeancountWriter(
        output_metadata=["source"],
        check_scope=lambda a: a == "Liabilities:Credit:HXB",
    )
    txn = Transaction(
        date=date(2025, 3, 15),
        amount=Decimal("28.00"),
        currency="CNY",
        description="拿铁",
        payee="瑞幸咖啡",
        card_last4="1234",
        account="Liabilities:Credit:HXB",
        contra_account="Expenses:Food:Coffee",
        provider="alipay",
    )
    output = writer.format_transaction(txn)
    assert 'card_last4: "1234"' in output
    assert "Liabilities:Credit:HXB" in output


def test_non_shared_account_omits_card_last4_posting_meta():
    writer = BeancountWriter(output_metadata=["source"], check_scope=lambda _a: False)
    txn = Transaction(
        date=date(2025, 3, 15),
        amount=Decimal("28.00"),
        currency="CNY",
        description="拿铁",
        payee="瑞幸咖啡",
        card_last4="1234",
        account="Assets:Bank:CCB:1386",
        contra_account="Expenses:Food:Coffee",
        provider="alipay",
    )
    output = writer.format_transaction(txn)
    assert 'card_last4: "1234"' not in output


def test_explicit_posting_metadata_does_not_duplicate():
    """Explicit _posting_metadata + shared account -> single card_last4 line."""
    writer = BeancountWriter(
        output_metadata=["source"],
        check_scope=lambda a: a == "Liabilities:Credit:HXB",
    )
    txn = Transaction(
        date=date(2025, 3, 15),
        amount=Decimal("28.00"),
        currency="CNY",
        description="拿铁",
        payee="瑞幸咖啡",
        card_last4="1234",
        account="Liabilities:Credit:HXB",
        contra_account="Expenses:Food:Coffee",
        provider="alipay",
        metadata={"_posting_metadata": ["card_last4"]},
    )
    output = writer.format_transaction(txn)
    assert output.count('card_last4: "1234"') == 1


def test_posting_metadata_skips_underscore_prefixed_keys():
    """An internal `_` key in _posting_metadata is dropped; other keys still emit."""
    writer = BeancountWriter(output_metadata=["source"], check_scope=lambda _a: False)
    txn = Transaction(
        date=date(2030, 1, 2),
        amount=Decimal("10.00"),
        currency="CNY",
        description="desc-a",
        payee="payee-a",
        account="Assets:Bank:Test",
        contra_account="Expenses:Test",
        provider="prov-a",
        metadata={
            "method": "m",
            "_withdrawal_target": "bank-a",
            "_posting_metadata": ["_withdrawal_target", "method"],
        },
    )
    output = writer.format_transaction(txn)
    assert 'method: "m"' in output
    assert "_withdrawal_target" not in output


def test_format_result_renders_meta_diagnostics_section():
    from bean_sieve.core.types import MatchResult, MetaDiagnostic, ReconcileResult

    diagnostics = [
        MetaDiagnostic(
            severity="hint",
            file="books/2025/q1.bean",
            line=1234,
            account="Liabilities:Credit:HXB",
            key="card_last4",
            expected="1234",
            actual=None,
            message='books/2025/q1.bean:1234  hint  missing posting meta `card_last4: "1234"` on Liabilities:Credit:HXB',
        ),
        MetaDiagnostic(
            severity="warn",
            file="books/2025/q2.bean",
            line=88,
            account="Liabilities:Credit:SPDB",
            key="card_last4",
            expected="1234",
            actual="5678",
            message='books/2025/q2.bean:88  warn  posting meta `card_last4` mismatch on Liabilities:Credit:SPDB: ledger "5678", statement "1234"',
        ),
    ]
    mr = MatchResult(meta_diagnostics=diagnostics)
    result = ReconcileResult(match_result=mr)

    writer = BeancountWriter(check_scope=lambda _a: True)
    output = writer.format_result(result)

    assert "Metadata diagnostics (2)" in output
    assert "books/2025/q1.bean:1234  hint  missing posting meta" in output
    assert "books/2025/q2.bean:88  warn  posting meta `card_last4` mismatch" in output


def test_format_result_filters_diagnostics_by_check_scope():
    """Only diagnostics whose account is in scope should be rendered."""
    from bean_sieve.core.types import MatchResult, MetaDiagnostic, ReconcileResult

    diagnostics = [
        MetaDiagnostic(
            severity="hint",
            file="books/a.bean",
            line=1,
            account="Liabilities:Credit:HXB",
            key="card_last4",
            expected="1234",
            actual=None,
            message="books/a.bean:1  hint  in-scope",
        ),
        MetaDiagnostic(
            severity="hint",
            file="books/b.bean",
            line=2,
            account="Assets:Bank:ICBC:9999",
            key="card_last4",
            expected="9999",
            actual=None,
            message="books/b.bean:2  hint  out-of-scope",
        ),
    ]
    mr = MatchResult(meta_diagnostics=diagnostics)
    result = ReconcileResult(match_result=mr)

    writer = BeancountWriter(check_scope=lambda a: "HXB" in a)
    output = writer.format_result(result)

    assert "Metadata diagnostics (1)" in output
    assert "in-scope" in output
    assert "out-of-scope" not in output


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
    writer = BeancountWriter(check_scope=lambda _a: True)
    output = writer.format_result(result)

    idx_a10 = output.index("books/a.bean:10")
    idx_a50 = output.index("books/a.bean:50")
    idx_b10 = output.index("books/b.bean:10")
    assert idx_a10 < idx_a50 < idx_b10


def test_shared_account_card_last4_not_duplicated_at_txn_level():
    """When account is shared and output_metadata emits all (None), card_last4
    should appear ONCE at posting level, not at transaction level too."""
    writer = BeancountWriter(check_scope=lambda a: a == "Liabilities:Credit:HXB")
    # output_metadata default is None = emit all fields
    txn = Transaction(
        date=date(2025, 3, 15),
        amount=Decimal("28.00"),
        currency="CNY",
        description="拿铁",
        payee="瑞幸咖啡",
        card_last4="1234",
        account="Liabilities:Credit:HXB",
        contra_account="Expenses:Food:Coffee",
        provider="alipay",
    )
    output = writer.format_transaction(txn)
    assert output.count('card_last4: "1234"') == 1
    # And the single occurrence should be at posting level (8-space indent)
    lines = output.split("\n")
    card_lines = [ln for ln in lines if "card_last4" in ln]
    assert len(card_lines) == 1
    assert card_lines[0].startswith("        ")  # 8-space = posting level


def test_non_shared_account_keeps_txn_level_card_last4():
    """For non-shared accounts, card_last4 is still emitted at txn level if output_metadata allows."""
    writer = BeancountWriter(check_scope=lambda _a: False)
    txn = Transaction(
        date=date(2025, 3, 15),
        amount=Decimal("28.00"),
        currency="CNY",
        description="拿铁",
        payee="瑞幸咖啡",
        card_last4="1234",
        account="Assets:Bank:CCB:1386",
        contra_account="Expenses:Food:Coffee",
        provider="alipay",
    )
    output = writer.format_transaction(txn)
    lines = output.split("\n")
    card_lines = [ln for ln in lines if "card_last4" in ln]
    assert len(card_lines) == 1
    assert card_lines[0].startswith("    ") and not card_lines[0].startswith("        ")


def _characterisation_txn() -> Transaction:
    """Transaction carrying one of every metadata key the writer can see."""
    return Transaction(
        date=date(2030, 1, 2),
        amount=Decimal("10.00"),
        currency="CNY",
        description="desc-a",
        payee="payee-a",
        account="Assets:Bank:Test",
        contra_account="Expenses:Test",
        provider="prov-a",
        metadata={
            "method": "m",
            "rebate": "1.00",
            "rebate_currency": "CNY",
            "_rebate_account": "Income:Rebate:Test",
            "_withdrawal_target": "bank-a",
            "matched_preset_rule": "pr-a",
            "matched_rule": "mr-a",
            "original_payee": "payee-b",
            "original_description": "desc-b",
            "reference": "ref-a",
            "_ignored": False,
            "_posting_metadata": ["method"],
            "balance": "99.00",
        },
    )


def test_header_omits_payee_slot_when_payee_missing():
    """A payee-less transaction writes `date flag "narration"`, never `""`."""
    txn = Transaction(
        date=date(2030, 1, 2),
        amount=Decimal("10.00"),
        currency="CNY",
        description="desc-a",
        account="Assets:Bank:Test",
        contra_account="Expenses:Test",
        provider="prov-a",
    )
    header = BeancountWriter().format_transaction(txn).split("\n")[0]
    assert header == '2030-01-02 ! "desc-a"'


def test_metadata_emission_characterisation():
    """Pins every metadata line the writer emits; internal `_` keys stay out."""
    output = BeancountWriter().format_transaction(_characterisation_txn())

    assert output.split("\n") == [
        '2030-01-02 ! "payee-a" "desc-a"',
        '    reference: "ref-a"',
        '    source: "prov-a"',
        '    matched_rule: "mr-a"',
        '    method: "m"',
        '    rebate: "1.00"',
        '    rebate_currency: "CNY"',
        '    matched_preset_rule: "pr-a"',
        '    original_description: "desc-b"',
        '    balance: "99.00"',
        "    Assets:Bank:Test  -10.00 CNY",
        '        method: "m"',
        "    Income:Rebate:Test  -1.00 CNY",
        "    Expenses:Test  11.00 CNY",
    ]


def test_metadata_emission_honours_output_metadata_allowlist():
    writer = BeancountWriter(output_metadata=["method", "rebate"])
    output = writer.format_transaction(_characterisation_txn())

    meta_lines = [
        ln
        for ln in output.split("\n")[1:]
        if ln.startswith("    ") and not ln.startswith("        ") and ": " in ln
    ]
    assert meta_lines == ['    method: "m"', '    rebate: "1.00"']


def test_ignored_transactions_never_reach_the_writer():
    from bean_sieve.config.schema import Config, Rule, RuleAction, RuleCondition
    from bean_sieve.core.rules import apply_rules

    config = Config(
        rules=[
            Rule(
                condition=RuleCondition(description="drop-me"),
                action=RuleAction(ignore=True),
            )
        ]
    )
    txn = Transaction(
        date=date(2030, 1, 2),
        amount=Decimal("10.00"),
        currency="CNY",
        description="drop-me",
        provider="prov-a",
    )
    assert apply_rules([txn], config) == []


def _foreign_txn(amount: str, metadata: dict) -> Transaction:
    return Transaction(
        date=date(2030, 1, 2),
        amount=Decimal(amount),
        currency="CNY",
        description="desc-a",
        account="Liabilities:Credit:Test",
        contra_account="Expenses:Test",
        provider="prov-a",
        metadata=metadata,
    )


def test_foreign_expense_prices_contra_posting_in_original_currency():
    writer = BeancountWriter(output_metadata=[])
    txn = _foreign_txn(
        "37.00",
        {"original_amount": Decimal("41.00"), "original_currency": "HKD"},
    )

    assert writer.format_transaction(txn).split("\n")[1:] == [
        "    Liabilities:Credit:Test  -37.00 CNY",
        "    Expenses:Test  41.00 HKD @@ 37.00 CNY",
    ]


def test_foreign_refund_keeps_the_sign_on_the_original_units():
    writer = BeancountWriter(output_metadata=[])
    txn = _foreign_txn(
        "-37.00",
        {"original_amount": Decimal("41.00"), "original_currency": "HKD"},
    )

    assert writer.format_transaction(txn).split("\n")[1:] == [
        "    Liabilities:Credit:Test  37.00 CNY",
        "    Expenses:Test  -41.00 HKD @@ 37.00 CNY",
    ]


def test_original_amount_without_currency_writes_billed_amount():
    writer = BeancountWriter(output_metadata=[])
    txn = _foreign_txn("37.00", {"original_amount": Decimal("41.00")})

    assert writer.format_transaction(txn).split("\n")[1:] == [
        "    Liabilities:Credit:Test  -37.00 CNY",
        "    Expenses:Test  37.00 CNY",
    ]
