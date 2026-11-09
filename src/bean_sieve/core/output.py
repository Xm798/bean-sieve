"""Beancount output generator."""

import datetime as dt
import linecache
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

from beancount.parser import printer

from .metadata_keys import (
    FEE_ACCOUNT,
    MATCHED_RULE,
    ORIGINAL_PAYEE,
    OUTPUT_METADATA,
    POSTING_METADATA,
    REBATE_ACCOUNT,
    REFERENCE,
)
from .types import (
    MatchDiagnostic,
    MatchSource,
    MetaDiagnostic,
    ReconcileResult,
    Transaction,
)

if TYPE_CHECKING:
    from ..config import Config


def _sort_key(t: Transaction) -> tuple:
    return (t.date, t.time or dt.time.min)


def _sort_transactions(
    transactions: list[Transaction], sort_by_time: str | None
) -> list[Transaction]:
    if not sort_by_time:
        return transactions
    reverse = sort_by_time == "desc"
    return sorted(transactions, key=_sort_key, reverse=reverse)


class BeancountWriter:
    def __init__(
        self,
        default_expense: str = "Expenses:FIXME",
        default_income: str = "Income:FIXME",
        default_rebate: str = "Income:Rebate:FIXME",
        default_fee: str = "Expenses:FIXME",
        output_metadata: list[str] | None = None,
        sort_by_time: str | None = "asc",
        default_flag: str = "!",
        check_scope: Callable[[str], bool] | None = None,
    ):
        self.default_expense = default_expense
        self.default_income = default_income
        self.default_rebate = default_rebate
        self.default_fee = default_fee
        # None writes every metadata field.
        self.output_metadata = output_metadata
        # "asc", "desc", or None to keep the input order.
        self.sort_by_time = sort_by_time
        # Flag of rows no rule matched; rule-matched rows keep the rule's flag.
        self.default_flag = default_flag
        # True for accounts in the meta_check scope: their card_last4 goes on
        # the posting, and only their metadata diagnostics are written.
        self.check_scope: Callable[[str], bool] = check_scope or (lambda _: False)

    @classmethod
    def from_config(cls, config: "Config") -> "BeancountWriter":
        defaults = config.defaults
        return cls(
            default_expense=defaults.expense_account,
            default_income=defaults.income_account,
            default_rebate=defaults.rebate_account,
            default_fee=defaults.fee_account,
            output_metadata=defaults.output_metadata,
            sort_by_time=defaults.sort_by_time,
            default_flag=defaults.flag,
            check_scope=build_check_scope(config),
        )

    def format_transaction(self, txn: Transaction) -> str:
        lines = []

        flag = txn.flag if txn.match_source == MatchSource.RULE else self.default_flag
        narration = txn.description.replace('"', '\\"')

        # A single quoted string is the narration; the payee slot is omitted
        # rather than written as "" (Beancount convention).
        header = f"{txn.date} {flag}"
        if txn.payee:
            header += f' "{txn.payee}"'
        header += f' "{narration}"'
        if txn.tags:
            header += " " + " ".join(f"#{tag}" for tag in txn.tags)
        if txn.links:
            header += " " + " ".join(f"^{link}" for link in txn.links)
        lines.append(header)

        meta_lines = self._format_metadata(txn)
        for meta in meta_lines:
            lines.append(f"    {meta}")

        postings = self._format_postings(txn)
        for posting in postings:
            lines.append(f"    {posting}")

        return "\n".join(lines)

    def _format_metadata(self, txn: Transaction) -> list[str]:
        meta = []
        # Rows of a provider with its own output_metadata carry the global list
        # merged with it.
        allowed = txn.metadata.get(OUTPUT_METADATA, self.output_metadata)
        # An unmapped row always shows its method, so the account can be filled
        # in by hand.
        force_method = not txn.account

        def should_include(key: str) -> bool:
            if key == "method" and force_method:
                return True
            return allowed is None or key in allowed

        def is_empty_value(value: str) -> bool:
            if not value:
                return True
            stripped = value.strip()
            return stripped == "" or stripped == "/" or stripped == "-"

        if should_include("time") and txn.time:
            meta.append(f'time: "{txn.time.strftime("%H:%M:%S")}"')

        if should_include("order_id") and txn.order_id:
            meta.append(f'order_id: "{txn.order_id}"')

        if (
            should_include("card_last4")
            and txn.card_last4
            and not (txn.account and self.check_scope(txn.account))
        ):
            meta.append(f'card_last4: "{txn.card_last4}"')

        if should_include(REFERENCE):
            ref = txn.metadata.get(REFERENCE)
            if ref and not is_empty_value(ref):
                meta.append(f'reference: "{ref}"')

        if should_include("source") and txn.provider:
            meta.append(f'source: "{txn.provider}"')

        if should_include(MATCHED_RULE) and txn.metadata.get(MATCHED_RULE):
            meta.append(f'{MATCHED_RULE}: "{txn.metadata[MATCHED_RULE]}"')

        # MATCHED_RULE and REFERENCE have their slots above; ORIGINAL_PAYEE is
        # kept in memory only, to audit a rule's payee override.
        skip_keys = {MATCHED_RULE, REFERENCE, ORIGINAL_PAYEE}
        for key, value in txn.metadata.items():
            # Beancount metadata keys must start with a lowercase letter, so an
            # internal `_` key would make the generated ledger fail bean-check.
            if key.startswith("_") or key in skip_keys:
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
        postings = []

        account = txn.account or "Assets:FIXME"
        amount = -txn.amount  # Statement shows outflow as positive

        # Currency exchange within one account: the sold amount buys
        # price_amount of price_currency. With a fee only the net amount buys
        # it, so the general path below prices the bought leg instead.
        exchange = txn.price_amount is not None and bool(txn.price_currency)
        if exchange and not txn.fee:
            postings.append(
                f"{account}  {amount} {txn.currency}"
                f" @@ {txn.price_amount} {txn.price_currency}"
            )
            postings.append(f"{account}  {txn.price_amount} {txn.price_currency}")
            return postings

        postings.append(f"{account}  {amount} {txn.currency}")

        posting_meta_keys = list(txn.metadata.get(POSTING_METADATA, []))
        if (
            txn.account
            and txn.card_last4
            and self.check_scope(txn.account)
            and "card_last4" not in posting_meta_keys
        ):
            posting_meta_keys.append("card_last4")

        for key in posting_meta_keys:
            # Beancount metadata keys must start with a lowercase letter, so an
            # internal `_` key would make the generated ledger fail bean-check.
            if key.startswith("_"):
                continue
            value = getattr(txn, key, None) or txn.metadata.get(key)
            if value:
                postings.append(f'    {key}: "{value}"')

        rebate_str = txn.metadata.get("rebate")
        rebate = Decimal(rebate_str) if rebate_str else Decimal("0")
        rebate_currency = txn.metadata.get("rebate_currency") or txn.currency

        if rebate:
            rebate_account = txn.metadata.get(REBATE_ACCOUNT) or self.default_rebate
            postings.append(f"{rebate_account}  -{rebate} {rebate_currency}")
            # The contra side books the gross: the amount paid plus the rebate.
            contra_amount = txn.amount + rebate
        else:
            contra_amount = txn.amount

        if txn.fee:
            fee_account = txn.metadata.get(FEE_ACCOUNT) or self.default_fee
            postings.append(f"{fee_account}  {txn.fee} {txn.currency}")
            contra_amount -= txn.fee

        if exchange:
            contra = account
            foreign_amount, foreign_currency = txn.price_amount, txn.price_currency
        else:
            contra = txn.contra_account or (
                self.default_expense if txn.is_expense else self.default_income
            )
            foreign_amount = txn.original_amount
            foreign_currency = txn.original_currency
        if (
            foreign_amount is not None
            and foreign_currency
            and foreign_currency != txn.currency
            and contra_amount
        ):
            # The primary account moves txn.currency, so the conversion sits on
            # the contra side and the primary posting keeps its own amount.
            units = foreign_amount.copy_sign(contra_amount)
            postings.append(
                f"{contra}  {units} {foreign_currency}"
                f" @@ {abs(contra_amount)} {txn.currency}"
            )
        else:
            postings.append(f"{contra}  {contra_amount} {txn.currency}")

        return postings

    def format_transactions(
        self,
        transactions: list[Transaction],
        source_info: str | None = None,
    ) -> str:
        output = StringIO()

        output.write("; " + "=" * 60 + "\n")
        output.write("; Generated by Bean-Sieve\n")
        output.write(f"; Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        if source_info:
            output.write(f"; Source: {source_info}\n")
        output.write("; " + "=" * 60 + "\n\n")

        for txn in _sort_transactions(transactions, self.sort_by_time):
            output.write(self.format_transaction(txn) + "\n\n")

        return output.getvalue()

    @staticmethod
    def format_balance(
        balance_date: dt.date,
        account: str,
        amount: Decimal,
        currency: str,
    ) -> str:
        return f"{balance_date} balance {account}  {amount} {currency}"

    def format_result(
        self, result: ReconcileResult, source_info: str | None = None
    ) -> str:
        output = StringIO()

        output.write(
            self.format_transactions(result.processed, source_info=source_info)
        )

        output.write("; --- Summary ---\n")
        output.write(f"; {result.match_result.summary}\n")

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

        diagnostics = sorted(
            (
                d
                for d in result.match_result.meta_diagnostics
                if self.check_scope(d.account)
            ),
            key=lambda d: (d.file, d.line, d.severity),
        )
        self._write_diagnostic_section(output, "Metadata diagnostics", diagnostics)

        # Ambiguous matches are not gated by check_scope: the chosen account
        # (e.g. a clearing account) often falls outside the meta_check scope.
        ambiguous = sorted(
            result.match_result.match_diagnostics, key=lambda d: (d.file, d.line)
        )
        self._write_diagnostic_section(output, "Ambiguous matches", ambiguous)

        return output.getvalue()

    @staticmethod
    def _write_diagnostic_section(
        output: StringIO,
        title: str,
        items: list[MetaDiagnostic] | list[MatchDiagnostic],
    ) -> None:
        if not items:
            return
        output.write("\n")
        output.write("; " + "=" * 60 + "\n")
        output.write(f"; {title} ({len(items)})\n")
        output.write("; " + "=" * 60 + "\n")
        for d in items:
            output.write(f"; {d.message}\n")

    def _extract_entry_source(self, filename: str, lineno: int) -> str | None:
        """The source text from 1-based ``lineno`` up to the next non-blank line
        starting in column 0 (the next directive or comment) or EOF; None when
        the file is unreadable or ``lineno`` lies outside it."""
        src = linecache.getlines(filename)
        if not src or lineno < 1 or lineno > len(src):
            return None
        start = lineno - 1
        end = start + 1
        while end < len(src):
            line = src[end]
            if line and not line[0].isspace() and line.strip():
                break
            end += 1
        return "".join(src[start:end]).rstrip()

    def _format_extra_entry(self, entry) -> str:
        """The entry's source text, else Beancount's printing of it."""
        txn = entry.txn
        filename = txn.meta.get("filename")
        lineno = txn.meta.get("lineno")

        if filename and lineno:
            body = self._extract_entry_source(filename, lineno)
            if body is not None:
                return f"; Source: {filename}:{lineno}\n{body}"

        # Without readable source text, print the parsed entry.
        body = printer.format_entry(txn).rstrip()
        if filename and lineno:
            return f"; Source: {filename}:{lineno}\n{body}"
        return body


def build_check_scope(config: "Config") -> Callable[[str], bool]:
    """An account is in the card_last4 check scope when its name contains a
    ``diagnostics.meta_check_accounts`` entry."""
    keywords = tuple(config.diagnostics.meta_check_accounts)
    if not keywords:
        return lambda _account: False
    return lambda account: any(kw in account for kw in keywords)


def write_output(
    result: ReconcileResult,
    output_path: Path,
    source_info: str | None = None,
    config: "Config | None" = None,
) -> None:
    from ..config import Config

    writer = BeancountWriter.from_config(config or Config())
    content = writer.format_result(result, source_info=source_info)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
