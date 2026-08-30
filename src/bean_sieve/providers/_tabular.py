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


def normalize_cell_str(value: object) -> str:
    """Convert an xlrd cell value to text.

    xlrd hands back a float for every numeric cell, so an integral float is
    rendered without its ".0" tail to keep card suffixes and other digit strings
    comparable.
    """
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value).strip()
