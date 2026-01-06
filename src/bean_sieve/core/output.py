"""Beancount output generator."""

import datetime as dt
from datetime import datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path

from .types import MatchSource, ReconcileResult, Transaction


def _sort_key(t: Transaction) -> tuple:
    """Sort key for transactions: by date, then by time."""
    return (t.date, t.time or dt.time.min)


def _sort_transactions(
    transactions: list[Transaction], sort_by_time: str | None
) -> list[Transaction]:
    """Sort transactions based on sort_by_time config."""
    if not sort_by_time:
        return transactions
    reverse = sort_by_time == "desc"
    return sorted(transactions, key=_sort_key, reverse=reverse)


class BeancountWriter:
    """Generate Beancount format output from transactions."""

    def __init__(
        self,
        default_expense: str = "Expenses:FIXME",
        default_income: str = "Income:FIXME",
        default_rebate: str = "Rebate:FIXME",
        output_metadata: list[str] | None = None,
        sort_by_time: str | None = "asc",
        default_flag: str = "!",
    ):
        self.default_expense = default_expense
        self.default_income = default_income
        self.default_rebate = default_rebate
        # Which metadata fields to include (None = all)
        self.output_metadata = output_metadata
        # Sort by datetime: "asc", "desc", or None (no sort)
        self.sort_by_time = sort_by_time
        # Default transaction flag: "*" (cleared) or "!" (pending)
        self.default_flag = default_flag

    def format_transaction(self, txn: Transaction) -> str:
        """Format a single transaction as Beancount entry."""
        lines = []

        # Transaction header: date flag "payee" "narration"
        # Use rule-set flag for rule-matched transactions, otherwise use default_flag
        flag = txn.flag if txn.match_source == MatchSource.RULE else self.default_flag
        payee_str = f'"{txn.payee}"' if txn.payee else '""'
        narration = txn.description.replace('"', '\\"')
        lines.append(f'{txn.date} {flag} {payee_str} "{narration}"')

        # Metadata
        meta_lines = self._format_metadata(txn)
        for meta in meta_lines:
            lines.append(f"  {meta}")

        # Postings
        postings = self._format_postings(txn)
        for posting in postings:
            lines.append(f"  {posting}")

        return "\n".join(lines)

    def _format_metadata(self, txn: Transaction) -> list[str]:
        """Format transaction metadata."""
        meta = []
        allowed = self.output_metadata  # None means all

        def should_include(key: str) -> bool:
            """Check if a metadata key should be included in output."""
            return allowed is None or key in allowed

        def is_empty_value(value: str) -> bool:
            """Check if a string value is empty or meaningless."""
            if not value:
                return True
            stripped = value.strip()
            return stripped == "" or stripped == "/" or stripped == "-"

        # Standard metadata
        if should_include("time") and txn.time:
            meta.append(f'time: "{txn.time.strftime("%H:%M:%S")}"')

        if should_include("order_id") and txn.order_id:
            meta.append(f'order_id: "{txn.order_id}"')

        if should_include("reference"):
            ref = txn.metadata.get("reference")
            if ref and not is_empty_value(ref):
                meta.append(f'reference: "{ref}"')

        # Source: provider name + rule info (not "fixme")
        if should_include("source"):
            source_parts = []
            if txn.provider:
                source_parts.append(txn.provider)
            if txn.metadata.get("matched_rule"):
                source_parts.append(f"rule:{txn.metadata['matched_rule']}")
            elif txn.match_source == MatchSource.RULE:
                source_parts.append("rule")
            elif txn.match_source == MatchSource.PREDICT:
                source_parts.append("predict")
            if source_parts:
                meta.append(f'source: "{":".join(source_parts)}"')

        # Additional metadata from provider (only if allowed)
        skip_keys = {"_ignored", "matched_rule", "reference", "original_payee"}
        for key, value in txn.metadata.items():
            if key in skip_keys:
                continue
            if not should_include(key):
                continue
            if isinstance(value, str):
                if is_empty_value(value):
                    continue
                meta.append(f'{key}: "{value}"')
            elif isinstance(value, bool):
                meta.append(f"{key}: {str(value).upper()}")
            elif isinstance(value, int | float | Decimal):
                meta.append(f"{key}: {value}")

        return meta

    def _format_postings(self, txn: Transaction) -> list[str]:
        """Format transaction postings."""
        postings = []

        # Primary account (asset/liability)
        account = txn.account or "Assets:FIXME"
        amount = -txn.amount  # Statement shows outflow as positive
        postings.append(f"{account}  {amount} {txn.currency}")

        # Handle rebate if present (e.g., 已优惠¥10.00)
        rebate_str = txn.metadata.get("rebate")
        rebate = Decimal(rebate_str) if rebate_str else Decimal("0")
        rebate_currency = txn.metadata.get("rebate_currency") or txn.currency

        if rebate:
            # Rebate posting (income-like, negative)
            postings.append(f"{self.default_rebate}  -{rebate} {rebate_currency}")
            # Contra account includes rebate (total expense = paid + rebate)
            contra_amount = txn.amount + rebate
        else:
            contra_amount = txn.amount

        # Contra account (expense/income)
        contra = txn.contra_account
        if not contra:
            contra = self.default_expense if txn.is_expense else self.default_income
        postings.append(f"{contra}  {contra_amount} {txn.currency}")

        return postings

    def format_transactions(
        self,
        transactions: list[Transaction],
        source_info: str | None = None,
    ) -> str:
        """Format multiple transactions with header."""
        output = StringIO()

        # Header
        output.write("; " + "=" * 60 + "\n")
        output.write("; Generated by Bean-Sieve\n")
        output.write(f"; Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        if source_info:
            output.write(f"; Source: {source_info}\n")
        output.write("; " + "=" * 60 + "\n\n")

        # Output all transactions sorted by time
        for txn in _sort_transactions(transactions, self.sort_by_time):
            output.write(self.format_transaction(txn) + "\n\n")

        return output.getvalue()

    def format_result(
        self, result: ReconcileResult, source_info: str | None = None
    ) -> str:
        """Format complete reconcile result."""
        output = StringIO()

        # Main content
        output.write(
            self.format_transactions(result.processed, source_info=source_info)
        )

        # Summary
        output.write("; --- Summary ---\n")
        output.write(f"; {result.match_result.summary}\n")

        # Extra entries (in ledger but not in statement)
        if result.match_result.extra:
            output.write(
                f"; \n; Extra entries in ledger ({len(result.match_result.extra)}):\n"
            )
            for entry in result.match_result.extra[:10]:  # Limit output
                txn = entry.txn
                posting = entry.posting
                amount = posting.units.number if posting.units else "?"
                output.write(f";   - {txn.date} {amount} @ {posting.account}\n")
            if len(result.match_result.extra) > 10:
                output.write(
                    f";   ... and {len(result.match_result.extra) - 10} more\n"
                )

        return output.getvalue()


def write_output(
    result: ReconcileResult,
    output_path: Path,
    source_info: str | None = None,
    default_expense: str = "Expenses:FIXME",
    default_income: str = "Income:FIXME",
    output_metadata: list[str] | None = None,
) -> None:
    """
    Write reconcile result to Beancount file.

    Convenience function for common usage.
    """
    writer = BeancountWriter(
        default_expense=default_expense,
        default_income=default_income,
        output_metadata=output_metadata,
    )
    content = writer.format_result(result, source_info=source_info)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
