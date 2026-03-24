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
    PresetRule,
    ReconcileContext,
    ReconcileResult,
    RulesEngine,
    Sieve,
    SieveConfig,
    SmartPredictor,
    Transaction,
)
from .core.types import MatchSource
from .providers import auto_detect_provider, get_provider, list_providers
from .providers.base import BaseProvider


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
    preset_rules: list[PresetRule] | None = None,
    covered_accounts: list[str] | None = None,
    covered_ranges: dict[str, list[tuple[date, date]]] | None = None,
) -> ReconcileResult:
    """
    Reconcile transactions against ledger and apply rules.

    Args:
        transactions: List of transactions to reconcile
        sieve: Configured Sieve instance with loaded ledger
        config: Configuration with rules and account mappings
        use_predictor: Whether to use ML prediction for unmapped accounts
        ledger_path: Required if use_predictor is True
        preset_rules: Preset rules from provider for automatic account lookup
        covered_accounts: Accounts covered by this statement for Extra calculation
        covered_ranges: Card to date ranges mapping for Extra calculation

    Returns:
        ReconcileResult with matched, missing, and processed transactions
    """
    config = config or Config()

    # Match against ledger
    match_result = sieve.match(
        transactions,
        covered_accounts=covered_accounts,
        covered_ranges=covered_ranges,
    )

    # Process missing transactions
    missing = list(match_result.missing)

    # Apply rules (preset rules first, then user rules)
    rules_engine = RulesEngine(config, preset_rules=preset_rules)
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
        output_metadata=config.defaults.output_metadata,
        sort_by_time=config.defaults.sort_by_time,
        default_flag=config.defaults.flag,
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
    Supports provider lifecycle hooks (pre_reconcile, post_output).

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

    # Get provider instance for hooks
    provider = _get_provider_for_hooks(statement_paths, provider_id)

    # Create reconcile context for hooks
    context = ReconcileContext(
        statement_paths=statement_paths,
        ledger_path=ledger_path,
        config=config,
        date_range=date_range,
        account_filter=account_filter,
        output_path=output_path,
    )

    # Parse statements
    transactions = parse_statements(statement_paths, provider_id)

    # Infer date range from transactions if not explicitly provided
    # This ensures Extra calculation only considers ledger entries within statement scope
    # Expand by date_tolerance to ensure matching works for edge dates
    if not date_range and transactions:
        from datetime import timedelta

        tolerance = config.defaults.date_tolerance

        # Prefer statement_period from transactions (extracted from statement headers)
        # This ensures Extra calculation covers the full statement period, even if
        # transactions don't span the entire period (e.g., no transactions in January
        # of a full-year statement)
        statement_periods = [
            t.statement_period for t in transactions if t.statement_period
        ]
        if statement_periods:
            # Use the union of all statement periods
            min_date = min(p[0] for p in statement_periods) - timedelta(days=tolerance)
            max_date = max(p[1] for p in statement_periods) + timedelta(days=tolerance)
        else:
            # Fall back to inferring from transaction dates
            min_date = min(t.date for t in transactions) - timedelta(days=tolerance)
            max_date = max(t.date for t in transactions) + timedelta(days=tolerance)

        date_range = (min_date, max_date)

    # Filter by date range if specified
    if date_range:
        transactions = [
            t for t in transactions if date_range[0] <= t.date <= date_range[1]
        ]

    # Pre-reconcile hook: call for each provider's transactions
    transactions = _apply_pre_reconcile_hooks(transactions, context, provider_id)

    # Apply provider output metadata config (posting_metadata, output_metadata)
    transactions = _apply_provider_output_config(transactions, config)

    # Get preset rules from all providers involved
    preset_rules = _collect_preset_rules(transactions, provider_id)

    # Apply negate rules BEFORE matching (sign affects matching logic)
    transactions = _apply_negate_rules(transactions, preset_rules)

    # Deduplicate cross-statement transactions (e.g., same payment in both
    # Alipay and bank card statements)
    transactions = _deduplicate_cross_statements(transactions, config)

    # Set target account for transactions based on provider config and preset rules
    # Preset rules with account_keyword have highest priority
    # This constrains matching to only consider the correct ledger account
    transactions = _set_target_accounts(transactions, config, preset_rules)

    # Collect covered accounts and ranges from providers
    covered_accounts = _collect_covered_accounts(transactions, provider_id, config)
    covered_ranges = _collect_covered_ranges(transactions, provider_id, config)

    # Load ledger
    sieve = load_ledger(
        ledger_path,
        account_filter=account_filter,
        date_range=date_range,
        date_tolerance=config.defaults.date_tolerance,
    )

    # Reconcile (with preset rules and covered accounts/ranges)
    result = reconcile(
        transactions,
        sieve,
        config=config,
        use_predictor=use_predictor,
        ledger_path=ledger_path if use_predictor else None,
        preset_rules=preset_rules,
        covered_accounts=covered_accounts,
        covered_ranges=covered_ranges,
    )

    # Generate output
    if output_path:
        source_info = ", ".join(p.name for p in statement_paths)
        content = generate_output(result, source_info=source_info, config=config)

        # Post-output hook
        if provider:
            content = provider.post_output(content, result, context)

        # Format with beanfmt if configured
        if config.format and config.format.enabled:
            import beanfmt

            content = beanfmt.format(content, **config.format.to_beanfmt_kwargs())  # type: ignore[reportAttributeAccessIssue]

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

    return result


def _apply_pre_reconcile_hooks(
    transactions: list[Transaction],
    context: ReconcileContext,
    provider_id: str | None,
) -> list[Transaction]:
    """
    Apply pre_reconcile hooks for each provider's transactions.

    Groups transactions by provider and calls each provider's pre_reconcile hook.
    """
    if provider_id:
        # Single provider specified, use it for all
        provider = get_provider(provider_id)
        if provider:
            return provider.pre_reconcile(transactions, context)
        return transactions

    # Group by provider
    from collections import defaultdict

    by_provider: dict[str, list[Transaction]] = defaultdict(list)
    for txn in transactions:
        by_provider[txn.provider].append(txn)

    # Apply each provider's hook
    result = []
    for pid, txns in by_provider.items():
        provider = get_provider(pid)
        if provider:
            txns = provider.pre_reconcile(txns, context)
        result.extend(txns)

    return result


def _apply_provider_output_config(
    transactions: list[Transaction],
    config: Config,
) -> list[Transaction]:
    """
    Apply provider-specific output metadata configuration.

    Sets _posting_metadata and _output_metadata in txn.metadata based on
    provider config. These are used by BeancountWriter to control output.
    """
    result = []
    for txn in transactions:
        provider_config = config.get_provider_config(txn.provider)

        # Set posting_metadata from provider config
        if provider_config.posting_metadata:
            txn.metadata["_posting_metadata"] = provider_config.posting_metadata

        # Merge output_metadata: global + provider
        if provider_config.output_metadata:
            global_meta = config.defaults.output_metadata or []
            merged = list(global_meta) + [
                m for m in provider_config.output_metadata if m not in global_meta
            ]
            txn.metadata["_output_metadata"] = merged

        result.append(txn)

    return result


def _collect_preset_rules(
    transactions: list[Transaction],
    provider_id: str | None,
) -> list:
    """Collect preset rules from all providers involved."""
    from .core.preset_rules import PresetRule

    if provider_id:
        provider = get_provider(provider_id)
        return provider.get_preset_rules() if provider else []

    # Collect from all unique providers
    providers_seen: set[str] = set()
    rules: list[PresetRule] = []

    for txn in transactions:
        if txn.provider and txn.provider not in providers_seen:
            providers_seen.add(txn.provider)
            provider = get_provider(txn.provider)
            if provider:
                rules.extend(provider.get_preset_rules())

    return rules


def _apply_negate_rules(
    transactions: list[Transaction],
    preset_rules: list[PresetRule],
) -> list[Transaction]:
    """
    Apply negate rules before matching.

    This is needed because amount sign affects matching logic (income vs expense).
    Negate rules must be applied before sieve.match() to correctly match
    refunds and other sign-inverted transactions.
    """
    # Filter to only negate rules
    negate_rules = [r for r in preset_rules if r.action.negate]
    if not negate_rules:
        return transactions

    # Compile patterns once
    for rule in negate_rules:
        rule.compile_patterns()

    result = []
    for txn in transactions:
        for rule in negate_rules:
            if rule.matches(txn):
                # Negate the amount
                txn = txn.model_copy(update={"amount": -txn.amount})
                break
        result.append(txn)

    return result


def _set_target_accounts(
    transactions: list[Transaction],
    config: Config,
    preset_rules: list[PresetRule] | None = None,
) -> list[Transaction]:
    """
    Set target account for transactions based on provider config and preset rules.

    Priority order:
    1. Preset rules with account_keyword (highest priority)
    2. card_last4 lookup in provider.accounts
    3. method lookup in provider.accounts

    For bank card providers: use card_last4 to look up in providers.xxx.accounts
    For payment platforms: use method to look up in providers.xxx.accounts
    This constrains matching to only consider the correct ledger account.
    """
    preset_rules = preset_rules or []
    result = []

    for txn in transactions:
        if txn.account:
            # Already has account set
            result.append(txn)
            continue

        # 1. Try preset rules first (account_keyword with highest priority)
        for preset in preset_rules:
            if preset.matches(txn) and preset.action.account_keyword:
                account = _lookup_account_by_keyword(
                    preset.action.account_keyword, config
                )
                if account:
                    txn = txn.model_copy(
                        update={
                            "account": account,
                            "metadata": {
                                **txn.metadata,
                                "matched_preset_rule": preset.rule_id,
                            },
                        }
                    )
                    break

        # If preset rules already set account, skip provider config lookup
        if txn.account:
            result.append(txn)
            continue

        # 2. Try to resolve account from provider config
        provider_config = config.get_provider_config(txn.provider)

        # Try card_last4 first (for bank card providers)
        if txn.card_last4 and txn.card_last4 in provider_config.accounts:
            txn = txn.model_copy(
                update={"account": provider_config.accounts[txn.card_last4]}
            )
        # Try method (for payment platform providers like Alipay/WeChat)
        elif (
            method := txn.metadata.get("method")
        ) and method in provider_config.accounts:
            txn = txn.model_copy(update={"account": provider_config.accounts[method]})

        # 3. Try account_mappings for method (payment platforms using external cards)
        if not txn.account:
            method = txn.metadata.get("method", "")
            if method:
                for mapping in config.account_mappings:
                    if mapping.pattern in method or method in mapping.pattern:
                        update = {"account": mapping.account}
                        txn = txn.model_copy(update=update)
                        if mapping.rebate_account and txn.metadata.get("rebate"):
                            txn.metadata["_rebate_account"] = mapping.rebate_account
                        break

        result.append(txn)

    return result


def _lookup_account_by_keyword(keyword: str, config: Config) -> str | None:
    """
    Lookup account by keyword in account_mappings.

    Args:
        keyword: Keyword to search for in account_mappings patterns
        config: Configuration with account_mappings

    Returns:
        Matched account name, or None if not found
    """
    import re

    for mapping in config.account_mappings:
        if re.search(keyword, mapping.pattern, re.IGNORECASE):
            return mapping.account
    return None


def _deduplicate_cross_statements(
    transactions: list[Transaction],
    config: Config,
) -> list[Transaction]:
    """
    Deduplicate transactions that appear in multiple statements.

    For example, a payment made via Alipay using a bank card will appear in both:
    - The bank card statement (direct record)
    - The Alipay statement (indirect record, method = "XX银行储蓄卡(1234)")

    This function removes indirect records that have a matching direct record,
    using 1:1 pairing to preserve legitimate same-day same-amount transactions.

    Pairing criteria:
    - Same date, amount, target account
    - Time within 5 minutes (if both have time), or same day (if no time)
    - One is direct (card_last4 in provider's accounts), one is indirect (method)
    """
    if len(transactions) <= 1:
        return transactions

    # Separate direct and indirect records
    direct_records: list[Transaction] = []
    indirect_records: list[Transaction] = []

    for txn in transactions:
        priority = _get_dedup_priority(txn, config)
        if priority >= 100:
            # Direct record: card_last4 matches provider's accounts
            direct_records.append(txn)
        elif priority <= 10:
            # Indirect record: payment platform using external method
            indirect_records.append(txn)
        else:
            # Neither - keep as direct (will be preserved)
            direct_records.append(txn)

    # Try to pair each indirect record with a direct record
    used_direct: set[int] = set()
    result = []

    for indirect in indirect_records:
        indirect_account = _resolve_target_account(indirect, config)
        matched = False

        for direct in direct_records:
            if id(direct) in used_direct:
                continue

            direct_account = _resolve_target_account(direct, config)

            # Must target same account
            if indirect_account != direct_account:
                continue

            # Must be same date and amount
            if indirect.date != direct.date:
                continue
            if abs(indirect.amount) != abs(direct.amount):
                continue

            # Time check: within 5 minutes if both have time
            if indirect.time and direct.time:
                from datetime import datetime, timedelta

                t1 = datetime.combine(indirect.date, indirect.time)
                t2 = datetime.combine(direct.date, direct.time)
                if abs(t1 - t2) > timedelta(minutes=5):
                    continue

            # Found a match - mark direct as used, discard indirect
            used_direct.add(id(direct))
            matched = True
            break

        if not matched:
            # No matching direct record - keep indirect
            result.append(indirect)

    # Add all direct records (they are always kept)
    result.extend(direct_records)

    return result


def _resolve_target_account(txn: Transaction, config: Config) -> str | None:
    """
    Resolve the target account for a transaction.

    For bank card providers: use card_last4 to look up in providers.xxx.accounts
    For payment platforms: use metadata['method'] to look up in account_mappings
    """
    # First try provider's accounts (for bank card providers)
    provider_config = config.get_provider_config(txn.provider)
    if txn.card_last4 and txn.card_last4 in provider_config.accounts:
        return provider_config.accounts[txn.card_last4]

    # Try metadata['method'] in account_mappings (for payment platforms)
    method = txn.metadata.get("method", "")
    if method:
        for mapping in config.account_mappings:
            if mapping.pattern in method or method in mapping.pattern:
                return mapping.account

    # Try card_last4 in all provider accounts
    if txn.card_last4:
        for pconfig in config.providers.values():
            if txn.card_last4 in pconfig.accounts:
                return pconfig.accounts[txn.card_last4]

    return None


def _get_dedup_priority(txn: Transaction, config: Config) -> int:
    """
    Get deduplication priority for a transaction.

    Higher priority = more likely to be kept.
    Direct records (card_last4 matches provider's accounts) have higher priority.
    """
    provider_config = config.get_provider_config(txn.provider)

    # Direct record: card_last4 is in this provider's accounts
    if txn.card_last4 and txn.card_last4 in provider_config.accounts:
        return 100

    # Indirect record: method points to another account (via account_mappings)
    method = txn.metadata.get("method", "")
    if method:
        # Payment platform transaction using external payment method
        return 10

    # Fallback
    return 50


def _collect_covered_accounts(
    transactions: list[Transaction],
    provider_id: str | None,
    config: Config,
) -> list[str]:
    """
    Collect covered accounts from all providers involved.

    Returns the union of accounts from each provider's get_covered_accounts().
    """
    covered: set[str] = set()

    if provider_id:
        provider = get_provider(provider_id)
        if provider:
            covered.update(provider.get_covered_accounts(transactions, config))
    else:
        # Collect from all unique providers
        from collections import defaultdict

        by_provider: dict[str, list[Transaction]] = defaultdict(list)
        for txn in transactions:
            by_provider[txn.provider].append(txn)

        for pid, txns in by_provider.items():
            provider = get_provider(pid)
            if provider:
                covered.update(provider.get_covered_accounts(txns, config))

    return list(covered)


def _collect_covered_ranges(
    transactions: list[Transaction],
    provider_id: str | None,
    config: Config,
) -> dict[str, list[tuple[date, date]]] | None:
    """
    Collect covered date ranges per account from all providers involved.

    Returns a dict mapping account name to list of (start, end) date ranges.
    Returns None if no provider provides range information.
    """
    from collections import defaultdict

    all_ranges: dict[str, list[tuple[date, date]]] = defaultdict(list)
    has_ranges = False

    if provider_id:
        provider = get_provider(provider_id)
        if provider:
            ranges = provider.get_covered_ranges(transactions, config)
            if ranges is not None:
                has_ranges = True
                for account, periods in ranges.items():
                    all_ranges[account].extend(periods)
    else:
        by_provider: dict[str, list[Transaction]] = defaultdict(list)
        for txn in transactions:
            by_provider[txn.provider].append(txn)

        for pid, txns in by_provider.items():
            provider = get_provider(pid)
            if provider:
                ranges = provider.get_covered_ranges(txns, config)
                if ranges is not None:
                    has_ranges = True
                    for account, periods in ranges.items():
                        all_ranges[account].extend(periods)

    return dict(all_ranges) if has_ranges else None


def _get_provider_for_hooks(
    statement_paths: list[Path],
    provider_id: str | None,
) -> BaseProvider | None:
    """
    Get a provider instance for lifecycle hooks.

    If provider_id is specified, returns that provider.
    Otherwise, auto-detects from the first file (assumes all files use same provider).
    """
    if not statement_paths:
        return None

    if provider_id:
        return get_provider(provider_id)

    # Auto-detect from first file
    return auto_detect_provider(statement_paths[0])


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
    "ReconcileContext",
    "Config",
]
