"""Auto-generate rules from ledger history."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from beancount import loader
from beancount.core.data import Transaction as BeanTransaction

from ..config.schema import Config


@dataclass
class SuggestedRule:
    """A rule suggestion extracted from ledger history."""

    payee: str
    contra_account: str
    count: int
    ratio: float


def suggest_rules(
    ledger_path: Path,
    min_count: int = 3,
    min_ratio: float = 0.9,
    existing_config: Config | None = None,
) -> list[SuggestedRule]:
    """
    Scan ledger and suggest rules based on high-frequency payee → account mappings.

    Args:
        ledger_path: Path to beancount ledger file or directory
        min_count: Minimum number of occurrences to suggest a rule
        min_ratio: Minimum ratio of dominant account to total
        existing_config: Existing config for deduplication against current rules

    Returns:
        List of suggested rules sorted by count descending
    """
    # Resolve ledger path
    if ledger_path.is_dir():
        main_file = ledger_path / "main.bean"
        if not main_file.exists():
            bean_files = list(ledger_path.glob("*.bean"))
            if not bean_files:
                return []
            main_file = bean_files[0]
        ledger_path = main_file

    entries, _errors, _options = loader.load_file(str(ledger_path))

    # Extract payee → contra_account pairs
    payee_accounts: dict[str, list[str]] = defaultdict(list)

    for entry in entries:
        if not isinstance(entry, BeanTransaction):
            continue
        if not entry.payee:
            continue

        # Find the contra posting (Expenses/Income side)
        contra_postings = [
            p for p in entry.postings if p.account.startswith(("Expenses:", "Income:"))
        ]

        # Only use unambiguous transactions (exactly one contra posting)
        if len(contra_postings) != 1:
            continue

        payee_accounts[entry.payee].append(contra_postings[0].account)

    # Aggregate and filter
    suggestions: list[SuggestedRule] = []

    for payee, accounts in payee_accounts.items():
        total = len(accounts)
        if total < min_count:
            continue

        # Find dominant account
        account_counts: dict[str, int] = defaultdict(int)
        for acc in accounts:
            account_counts[acc] += 1

        dominant_account = max(account_counts, key=account_counts.get)  # type: ignore[arg-type]
        ratio = account_counts[dominant_account] / total

        if ratio < min_ratio:
            continue

        suggestions.append(
            SuggestedRule(
                payee=payee,
                contra_account=dominant_account,
                count=total,
                ratio=ratio,
            )
        )

    # Sort by count descending
    suggestions.sort(key=lambda s: s.count, reverse=True)

    # Deduplicate against existing rules
    if existing_config and existing_config.rules:
        suggestions = _deduplicate(suggestions, existing_config)

    return suggestions


def _deduplicate(
    suggestions: list[SuggestedRule],
    config: Config,
) -> list[SuggestedRule]:
    """Remove suggestions already covered by existing rules."""
    # Compile existing rule patterns
    patterns: list[re.Pattern] = []
    for rule in config.rules:
        if rule.condition.description:
            try:
                patterns.append(re.compile(rule.condition.description, re.IGNORECASE))
            except re.error:
                continue

    if not patterns:
        return suggestions

    result = []
    for suggestion in suggestions:
        if not any(p.search(suggestion.payee) for p in patterns):
            result.append(suggestion)

    return result


def format_rules_yaml(suggestions: list[SuggestedRule]) -> str:
    """Format suggestions as YAML rules snippet."""
    if not suggestions:
        return ""

    lines: list[str] = []
    for suggestion in suggestions:
        escaped = re.escape(suggestion.payee)
        lines.append(
            f"  # {suggestion.payee} ({suggestion.count} transactions, "
            f"{suggestion.ratio:.0%})"
        )
        lines.append(f'  - description: ".*{escaped}.*"')
        lines.append(f'    target_payee: "{suggestion.payee}"')
        lines.append(f"    contra_account: {suggestion.contra_account}")
        lines.append("")

    return "\n".join(lines)
