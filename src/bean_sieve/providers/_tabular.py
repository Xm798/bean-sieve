"""Shared parsing helpers for tabular (CSV/XLS) bank statement providers."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


def to_decimal(value: object) -> Decimal | None:
    """Convert a split-amount cell to Decimal.

    Returns None for an empty, placeholder, zero or unparsable cell, so callers
    can read "not this column" off the same result as "no usable value".
    """
    cleaned = str(value).replace(",", "").strip()
    if not cleaned or cleaned == "-":
        return None
    try:
        d = Decimal(cleaned)
        return d if d != 0 else None
    except InvalidOperation:
        return None
