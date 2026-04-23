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
        shared_accounts: set[str] | None = None,
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
        # Accounts shared across providers: auto-emit card_last4 posting meta
        self.shared_accounts = shared_accounts or set()

    def format_transaction(self, txn: Transaction) -> str:
        """Format a single transaction as Beancount entry."""
        lines = []

        # Transaction header: date flag "payee" "narration" tags links
        # Use rule-set flag for rule-matched transactions, otherwise use default_flag
        flag = txn.flag if txn.match_source == MatchSource.RULE else self.default_flag
        payee_str = f'"{txn.payee}"' if txn.payee else '""'
        narration = txn.description.replace('"', '\\"')

        # Build header line with optional tags and links
        header = f'{txn.date} {flag} {payee_str} "{narration}"'
        if txn.tags:
            header += " " + " ".join(f"#{tag}" for tag in txn.tags)
        if txn.links:
            header += " " + " ".join(f"^{link}" for link in txn.links)
        lines.append(header)

        # Metadata
        meta_lines = self._format_metadata(txn)
        for meta in meta_lines:
            lines.append(f"    {meta}")

        # Postings
        postings = self._format_postings(txn)
        for posting in postings:
            lines.append(f"    {posting}")

        return "\n".join(lines)

    def _format_metadata(self, txn: Transaction) -> list[str]:
        """Format transaction metadata."""
        meta = []
        # Use per-transaction override if set (from provider config), else global
        allowed = txn.metadata.get("_output_metadata", self.output_metadata)
        # Force output 'method' when account not matched (for manual processing)
        force_method = not txn.account

        def should_include(key: str) -> bool:
            """Check if a metadata key should be included in output."""
            if key == "method" and force_method:
                return True
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

        if should_include("card_last4") and txn.card_last4:
            meta.append(f'card_last4: "{txn.card_last4}"')

        if should_include("reference"):
            ref = txn.metadata.get("reference")
            if ref and not is_empty_value(ref):
                meta.append(f'reference: "{ref}"')

        # Source: provider name only
        if should_include("source") and txn.provider:
            meta.append(f'source: "{txn.provider}"')

        # Match source for debug (rule pattern)
        if should_include("matched_rule") and txn.metadata.get("matched_rule"):
            meta.append(f'matched_rule: "{txn.metadata["matched_rule"]}"')

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

        # Multi-currency exchange: primary posting with @@ price, contra auto-balanced
        if txn.price_amount is not None and txn.price_currency:
            postings.append(
                f"{account}  {amount} {txn.currency}"
                f" @@ {txn.price_amount} {txn.price_currency}"
            )
            postings.append(f"{account}  {txn.price_amount} {txn.price_currency}")
            return postings

        postings.append(f"{account}  {amount} {txn.currency}")

        # Compute effective posting-metadata keys for this posting:
        # explicit provider config + auto-inject for shared accounts.
        explicit_meta_keys = list(txn.metadata.get("_posting_metadata", []))
        auto_meta_keys: list[str] = []
        if txn.account in self.shared_accounts and txn.card_last4:
            auto_meta_keys.append("card_last4")

        seen: set[str] = set()
        for key in explicit_meta_keys + auto_meta_keys:
            if key in seen:
                continue
            seen.add(key)
            value = getattr(txn, key, None) or txn.metadata.get(key)
            if value:
                postings.append(f'    {key}: "{value}"')

        # Handle rebate if present (e.g., 已优惠¥10.00)
        rebate_str = txn.metadata.get("rebate")
        rebate = Decimal(rebate_str) if rebate_str else Decimal("0")
        rebate_currency = txn.metadata.get("rebate_currency") or txn.currency

        if rebate:
            # Rebate posting (income-like, negative)
            # Use per-transaction rebate account if set, else fall back to default
            rebate_account = txn.metadata.get("_rebate_account") or self.default_rebate
            postings.append(f"{rebate_account}  -{rebate} {rebate_currency}")
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

        # Extra entries (in ledger but not in statement) - output in full
        if result.match_result.extra:
            output.write("\n")
            output.write("; " + "=" * 60 + "\n")
            output.write(
                f"; Extra entries in ledger ({len(result.match_result.extra)})\n"
            )
            output.write("; These exist in ledger but not found in statement\n")
            output.write("; " + "=" * 60 + "\n\n")

            for entry in result.match_result.extra:
                output.write(self._format_extra_entry(entry) + "\n\n")

        return output.getvalue()

    def _format_extra_entry(self, entry) -> str:
        """Format an extra ledger entry with source file link."""
        txn = entry.txn
        lines = []

        # Source file link comment
        filename = txn.meta.get("filename")
        lineno = txn.meta.get("lineno")
        if filename and lineno:
            lines.append(f"; Source: {filename}:{lineno}")

        # Transaction header: date flag "payee" "narration"
        flag = txn.flag
        payee_str = f'"{txn.payee}"' if txn.payee else '""'
        narration = (txn.narration or "").replace('"', '\\"')
        lines.append(f'{txn.date} {flag} {payee_str} "{narration}"')

        # Metadata (skip internal fields)
        skip_meta = {"filename", "lineno", "__tolerances__"}
        for key, value in txn.meta.items():
            if key in skip_meta:
                continue
            if isinstance(value, str):
                lines.append(f'    {key}: "{value}"')
            elif isinstance(value, bool):
                lines.append(f"    {key}: {str(value).upper()}")
            elif value is not None:
                lines.append(f"    {key}: {value}")

        # Tags and links
        if txn.tags:
            for tag in txn.tags:
                lines.append(f"    #{tag}")
        if txn.links:
            for link in txn.links:
                lines.append(f"    ^{link}")

        # All postings
        for p in txn.postings:
            if p.units:
                posting_line = f"    {p.account}  {p.units.number} {p.units.currency}"
            else:
                posting_line = f"    {p.account}"
            # Cost
            if p.cost:
                posting_line += f" {{{p.cost.number} {p.cost.currency}}}"
            # Price
            if p.price:
                posting_line += f" @ {p.price.number} {p.price.currency}"
            lines.append(posting_line)

        return "\n".join(lines)


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
