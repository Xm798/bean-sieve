"""Resolution of `account_mappings` entries.

Five resolution rules coexist and are deliberately kept apart: they differ in
direction (pattern inside text, or keyword inside pattern), in case sensitivity,
in whether the needle is a literal or a regex, and in whether the text is split
into bank name and card suffix. Callers pick one by name.
Every rule scans the mappings in config order and takes the first hit; the two
keyword rules first look for an exact pattern match, because preset keywords
name generic wallets ("零钱", "余额") that are substrings of longer wallet names
("零钱通", "余额宝") and must resolve to their own mapping whatever the YAML order.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..config.schema import AccountMapping
from .metadata_keys import REBATE_ACCOUNT
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


_CARD_LAST4_REGEX = re.compile(r"\((\d{4})\)$")


def extract_card_last4(method: str | None) -> str | None:
    """Extract the trailing 4-digit card suffix from a payment method string.

    Returns the 4 digits if ``method`` ends with ``(XXXX)``, else ``None``.
    """
    if not method:
        return None
    m = _CARD_LAST4_REGEX.search(method)
    return m.group(1) if m else None


def resolve_by_bank_and_suffix(
    mappings: Sequence[AccountMapping], text: str
) -> AccountMapping | None:
    """First mapping whose pattern contains both the bank name and the 4-digit
    suffix of a `bank(XXXX)` text, ignoring case."""
    text = text.strip()
    suffix = extract_card_last4(text)
    if suffix is None:
        return None
    bank = text[: text.rindex("(")].strip().lower()
    if not bank:
        return None
    for mapping in mappings:
        pattern = mapping.pattern.lower()
        if bank in pattern and suffix in pattern:
            return mapping
    return None


def resolve_by_keyword_substring(
    mappings: Sequence[AccountMapping], keyword: str
) -> str | None:
    """Account of the first mapping whose pattern equals `keyword`, else of the
    first whose pattern contains it, ignoring case."""
    keyword_lower = keyword.lower()
    for mapping in mappings:
        if mapping.pattern.lower() == keyword_lower:
            return mapping.account
    for mapping in mappings:
        if keyword_lower in mapping.pattern.lower():
            return mapping.account
    return None


def resolve_by_keyword_regex(
    mappings: Sequence[AccountMapping], keyword: str
) -> str | None:
    """Account of the first mapping whose whole pattern the regex `keyword`
    matches, else of the first it finds a match in, ignoring case."""
    for mapping in mappings:
        if re.fullmatch(keyword, mapping.pattern, re.IGNORECASE):
            return mapping.account
    for mapping in mappings:
        if re.search(keyword, mapping.pattern, re.IGNORECASE):
            return mapping.account
    return None


def apply_rebate_account(txn: Transaction, mapping: AccountMapping) -> None:
    """Record the mapping's rebate account when the transaction carries a rebate."""
    if mapping.rebate_account and txn.metadata.get("rebate"):
        txn.metadata[REBATE_ACCOUNT] = mapping.rebate_account
