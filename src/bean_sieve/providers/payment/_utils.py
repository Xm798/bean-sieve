"""Shared utilities for payment-platform providers."""

from __future__ import annotations

import re

_CARD_LAST4_REGEX = re.compile(r"\((\d{4})\)$")


def extract_card_last4(method: str | None) -> str | None:
    """Extract the trailing 4-digit card suffix from a payment method string.

    Returns the 4 digits if ``method`` ends with ``(XXXX)``, else ``None``.
    """
    if not method:
        return None
    m = _CARD_LAST4_REGEX.search(method)
    return m.group(1) if m else None
