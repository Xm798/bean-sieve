"""Resolution of `account_mappings` entries.

Four resolution rules coexist and are deliberately kept apart: they differ in
direction (pattern inside text, or keyword inside pattern), in case sensitivity
and in whether the needle is a literal or a regex. Callers pick one by name.
Every rule scans the mappings in config order and takes the first hit.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..config.schema import AccountMapping
from .types import Transaction


def resolve_by_method(
    mappings: Sequence[AccountMapping], method: str
) -> AccountMapping | None:
    """First mapping whose pattern is a case-sensitive substring of `method`."""
    for mapping in mappings:
        if mapping.pattern in method:
            return mapping
    return None


def resolve_by_method_ci(
    mappings: Sequence[AccountMapping], text: str
) -> AccountMapping | None:
    """First mapping whose pattern is a case-insensitive substring of `text`."""
    text_lower = text.lower()
    for mapping in mappings:
        if mapping.pattern.lower() in text_lower:
            return mapping
    return None


def resolve_by_keyword_substring(
    mappings: Sequence[AccountMapping], keyword: str
) -> str | None:
    """Account of the first mapping whose pattern contains `keyword`, ignoring case."""
    keyword_lower = keyword.lower()
    for mapping in mappings:
        if keyword_lower in mapping.pattern.lower():
            return mapping.account
    return None


def resolve_by_keyword_regex(
    mappings: Sequence[AccountMapping], keyword: str
) -> str | None:
    """Account of the first mapping whose pattern `keyword` matches as a regex."""
    for mapping in mappings:
        if re.search(keyword, mapping.pattern, re.IGNORECASE):
            return mapping.account
    return None


def apply_rebate_account(txn: Transaction, mapping: AccountMapping) -> None:
    """Record the mapping's rebate account when the transaction carries a rebate."""
    if mapping.rebate_account and txn.metadata.get("rebate"):
        txn.metadata["_rebate_account"] = mapping.rebate_account
