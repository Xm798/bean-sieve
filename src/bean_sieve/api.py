"""
API layer for Bean-Sieve.

This module provides the public API for CLI and GUI frontends.
"""

from datetime import date
from pathlib import Path

from .config import Config, load_config
from .core import (
    BeancountWriter,
    MatchResult,
    ReconcileResult,
    RulesEngine,
    Sieve,
    SieveConfig,
    SmartPredictor,
    Transaction,
)
from .core.types import MatchSource
from .providers import auto_detect_provider, get_provider, list_providers


def parse_statement(
    file_path: Path,
    provider_id: str | None = None,
) -> list[Transaction]:
    """
    Parse a statement file and return transactions.

    Args:
        file_path: Path to the statement file
        provider_id: Provider ID to use, or None for auto-detection

    Returns:
        List of parsed transactions

    Raises:
        ValueError: If no suitable provider found
    """
    if provider_id:
        provider = get_provider(provider_id)
    else:
        provider = auto_detect_provider(file_path)
        if not provider:
            raise ValueError(f"Cannot auto-detect provider for: {file_path}")

    return provider.parse(file_path)


def parse_statements(
    file_paths: list[Path],
    provider_id: str | None = None,
) -> list[Transaction]:
    """
    Parse multiple statement files.

    Args:
        file_paths: List of paths to statement files
        provider_id: Provider ID to use for all files, or None for auto-detection

    Returns:
        Combined list of transactions from all files
    """
    all_transactions = []
    for path in file_paths:
        transactions = parse_statement(path, provider_id)
        all_transactions.extend(transactions)
    return all_transactions


def load_ledger(
    ledger_path: Path,
    account_filter: str | None = None,
    date_range: tuple[date, date] | None = None,
    date_tolerance: int = 2,
) -> Sieve:
    """
    Load a Beancount ledger for reconciliation.

    Args:
        ledger_path: Path to main.bean or ledger directory
        account_filter: Only include entries with this account prefix
        date_range: Only include entries within this date range
        date_tolerance: Days tolerance for date matching

    Returns:
        Configured Sieve instance
    """
    config = SieveConfig(date_tolerance=date_tolerance)
    sieve = Sieve(config)
    sieve.load_ledger(ledger_path, account_filter, date_range)
    return sieve


def reconcile(
    transactions: list[Transaction],
    sieve: Sieve,
    config: Config | None = None,
    use_predictor: bool = False,
    ledger_path: Path | None = None,
) -> ReconcileResult:
    """
    Reconcile transactions against ledger and apply rules.

    Args:
        transactions: List of transactions to reconcile
        sieve: Configured Sieve instance with loaded ledger
        config: Configuration with rules and account mappings
        use_predictor: Whether to use ML prediction for unmapped accounts
        ledger_path: Required if use_predictor is True

    Returns:
        ReconcileResult with matched, missing, and processed transactions
    """
    config = config or Config()

    # Match against ledger
    match_result = sieve.match(transactions)

    # Process missing transactions
    missing = list(match_result.missing)

    # Apply rules
    rules_engine = RulesEngine(config)
    processed = [rules_engine.apply(txn) for txn in missing]

    # Filter out ignored transactions
    processed = [t for t in processed if not t.metadata.get("_ignored")]

    # Apply ML predictions if enabled
    if use_predictor and ledger_path:
        predictor = SmartPredictor(
            ledger_path,
            min_confidence=config.predictor.min_confidence,
        )
        if predictor.is_available and predictor.train():
            processed = [predictor.predict(txn) for txn in processed]

    # Apply FIXME fallback for unmatched transactions
    processed = _apply_fixme_fallback(processed, config)

    return ReconcileResult(match_result=match_result, processed=processed)


def _apply_fixme_fallback(
    transactions: list[Transaction], config: Config
) -> list[Transaction]:
    """Apply FIXME fallback for transactions without contra accounts."""
    for txn in transactions:
        if not txn.contra_account:
            if txn.is_expense:
                txn.contra_account = config.defaults.expense_account
            else:
                txn.contra_account = config.defaults.income_account
            txn.match_source = MatchSource.FIXME
            txn.confidence = 0.0
            txn.flag = "!"  # Mark for review
    return transactions


def generate_output(
    result: ReconcileResult,
    output_path: Path | None = None,
    source_info: str | None = None,
    config: Config | None = None,
) -> str:
    """
    Generate Beancount output from reconcile result.

    Args:
        result: ReconcileResult from reconcile()
        output_path: If provided, write to this file
        source_info: Optional source description for header
        config: Configuration for default accounts

    Returns:
        Generated Beancount content as string
    """
    config = config or Config()
    writer = BeancountWriter(
        default_expense=config.defaults.expense_account,
        default_income=config.defaults.income_account,
    )

    content = writer.format_result(result, source_info=source_info)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

    return content


def full_reconcile(
    statement_paths: list[Path],
    ledger_path: Path,
    config_path: Path | None = None,
    output_path: Path | None = None,
    provider_id: str | None = None,
    date_range: tuple[date, date] | None = None,
    account_filter: str | None = None,
    use_predictor: bool = False,
) -> ReconcileResult:
    """
    Complete reconciliation workflow.

    This is the main entry point for the reconcile command.

    Args:
        statement_paths: List of statement files to process
        ledger_path: Path to Beancount ledger
        config_path: Path to bean-sieve.yaml config
        output_path: Path for output file (optional)
        provider_id: Provider to use (or auto-detect)
        date_range: Filter transactions to this range
        account_filter: Filter ledger to accounts with this prefix
        use_predictor: Use ML prediction

    Returns:
        ReconcileResult with all processing results
    """
    # Load config
    config = load_config(config_path) if config_path else Config()

    # Parse statements
    transactions = parse_statements(statement_paths, provider_id)

    # Filter by date range if specified
    if date_range:
        transactions = [
            t for t in transactions if date_range[0] <= t.date <= date_range[1]
        ]

    # Load ledger
    sieve = load_ledger(
        ledger_path,
        account_filter=account_filter,
        date_range=date_range,
        date_tolerance=config.defaults.date_tolerance,
    )

    # Reconcile
    result = reconcile(
        transactions,
        sieve,
        config=config,
        use_predictor=use_predictor,
        ledger_path=ledger_path if use_predictor else None,
    )

    # Generate output
    if output_path:
        source_info = ", ".join(p.name for p in statement_paths)
        generate_output(result, output_path, source_info=source_info, config=config)

    return result


__all__ = [
    # Main API
    "parse_statement",
    "parse_statements",
    "load_ledger",
    "reconcile",
    "generate_output",
    "full_reconcile",
    # Utilities
    "load_config",
    "list_providers",
    "get_provider",
    "auto_detect_provider",
    # Types (re-export for convenience)
    "Transaction",
    "MatchResult",
    "ReconcileResult",
    "Config",
]
