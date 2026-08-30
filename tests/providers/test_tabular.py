"""Tests for the shared tabular parsing helpers."""

from __future__ import annotations

from decimal import Decimal

import pytest

from bean_sieve.providers._tabular import to_decimal


class TestToDecimal:
    """Tests for to_decimal()."""

    @pytest.mark.parametrize(
        "value",
        ["", "   ", "-", "--", "abc", ", ", "1, 000"],
    )
    def test_unusable_cells_are_none(self, value: str) -> None:
        assert to_decimal(value) is None

    @pytest.mark.parametrize("value", ["0", "0.00", "-0", 0.0, Decimal("0")])
    def test_zero_is_none(self, value: object) -> None:
        assert to_decimal(value) is None

    def test_thousand_separators_are_stripped(self) -> None:
        assert to_decimal("1,234.56") == Decimal("1234.56")

    def test_separator_removal_precedes_stripping(self) -> None:
        assert to_decimal(" ,1") == Decimal("1")

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("100", Decimal("100")),
            (" 100.50 ", Decimal("100.50")),
            ("-42.10", Decimal("-42.10")),
            (100.0, Decimal("100")),
            (Decimal("1"), Decimal("1")),
        ],
    )
    def test_parsed_values(self, value: object, expected: Decimal) -> None:
        assert to_decimal(value) == expected
