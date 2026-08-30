"""Tests for the shared tabular parsing helpers."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.providers._tabular import (
    find_header_line,
    normalize_cell_str,
    read_text_lines,
    to_decimal,
)


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


class TestNormalizeCellStr:
    """Tests for normalize_cell_str()."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (8888.0, "8888"),
            (8888.5, "8888.5"),
            (8888, "8888"),
            (" 8888 ", "8888"),
            ("", ""),
            (Decimal("8888"), "8888"),
            (None, "None"),
        ],
    )
    def test_rendered_text(self, value: object, expected: str) -> None:
        assert normalize_cell_str(value) == expected

    @pytest.mark.parametrize(
        ("value", "exc"),
        [(float("nan"), ValueError), (float("inf"), OverflowError)],
    )
    def test_non_finite_floats_raise(self, value: float, exc: type[Exception]) -> None:
        with pytest.raises(exc):
            normalize_cell_str(value)


class TestReadTextLines:
    """Tests for read_text_lines()."""

    def test_bom_is_stripped(self, tmp_path: Path) -> None:
        path = tmp_path / "bom.csv"
        path.write_bytes("日期,金额\n2030-01-02,10.00\n".encode("utf-8-sig"))

        assert read_text_lines(path) == ["日期,金额\n", "2030-01-02,10.00\n"]

    def test_gbk_falls_back_after_utf8(self, tmp_path: Path) -> None:
        path = tmp_path / "gbk.csv"
        path.write_bytes("摘要,金额\n".encode("gbk"))

        assert read_text_lines(path) == ["摘要,金额\n"]

    def test_undecodable_bytes_raise(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.csv"
        path.write_bytes(b"\xff\xfe\x00\x00\x81\x40\xff")

        with pytest.raises(ValueError):
            read_text_lines(path)

    def test_encodings_are_tried_in_the_given_order(self, tmp_path: Path) -> None:
        path = tmp_path / "order.csv"
        path.write_bytes("摘要\n".encode("gbk"))

        assert read_text_lines(path, ("latin-1",)) == ["ÕªÒª\n"]


class TestFindHeaderLine:
    """Tests for find_header_line()."""

    LINES = ["preamble\n", "交易日期,摘要\n", "交易日期,摘要\n"]

    def test_every_keyword_must_be_present(self) -> None:
        assert find_header_line(["交易日期,金额\n"], ("交易日期", "摘要")) is None

    def test_first_match_wins(self) -> None:
        assert find_header_line(self.LINES, ("交易日期", "摘要")) == 1

    def test_missing_header_is_none(self) -> None:
        assert find_header_line(["preamble\n"], ("交易日期", "摘要")) is None

    def test_unlimited_scan_reaches_late_headers(self) -> None:
        lines = ["preamble\n"] * 10 + ["交易日期,摘要\n"]

        assert find_header_line(lines, ("交易日期", "摘要")) == 10

    def test_limit_stops_the_scan(self) -> None:
        lines = ["preamble\n"] * 10 + ["交易日期,摘要\n"]

        assert find_header_line(lines, ("交易日期", "摘要"), 10) is None
