"""Shared parsing helpers for tabular (CSV/XLS) bank statement providers."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path


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


# Ordered widest-first: the BOM variant must win before plain utf-8 swallows it.
CJK_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "utf-8", "gbk")


def read_text_lines(path: Path, encodings: Sequence[str] = CJK_ENCODINGS) -> list[str]:
    """Read a text statement, trying each encoding in turn."""
    for encoding in encodings:
        try:
            with open(path, encoding=encoding) as f:
                return f.readlines()
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError(f"Cannot decode {path}")


def find_header_line(
    lines: Sequence[str], keywords: Sequence[str], limit: int | None = None
) -> int | None:
    """Return the index of the first line containing every keyword."""
    for i, line in enumerate(lines[:limit]):
        if all(kw in line for kw in keywords):
            return i
    return None
