"""Tests for the shared tabular parsing helpers."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.providers._tabular import (
    find_header_line,
    first_masked_card_last4,
    masked_card_last4,
    normalize_cell_str,
    read_csv_rows,
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


class TestReadCsvRows:
    """Tests for read_csv_rows()."""

    def test_cells_are_stripped_by_default(self, tmp_path: Path) -> None:
        path = tmp_path / "padded.csv"
        path.write_text("a ,\tb\n", encoding="utf-8")

        assert read_csv_rows(path) == [["a", "b"]]

    def test_raw_cells_are_kept_when_asked(self, tmp_path: Path) -> None:
        path = tmp_path / "padded.csv"
        path.write_text("a ,\tb\n", encoding="utf-8")

        assert read_csv_rows(path, strip_cells=False) == [["a ", "\tb"]]

    def test_blank_rows_are_dropped(self, tmp_path: Path) -> None:
        path = tmp_path / "blanks.csv"
        path.write_text("a,b\n \n,\nc,d\n", encoding="utf-8")

        assert read_csv_rows(path) == [["a", "b"], ["c", "d"]]

    def test_quoted_newlines_stay_in_one_cell(self, tmp_path: Path) -> None:
        path = tmp_path / "quoted.csv"
        path.write_text('a,"one\ntwo"\n', encoding="utf-8", newline="")

        assert read_csv_rows(path, strip_cells=False) == [["a", "one\ntwo"]]

    def test_big5_is_tried_after_gbk(self, tmp_path: Path) -> None:
        path = tmp_path / "big5.csv"
        path.write_bytes("賬項資料,金額\n".encode("big5"))

        assert read_csv_rows(path) == [["賬項資料", "金額"]]

    def test_undecodable_bytes_raise(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.csv"
        path.write_bytes(b"\xff\xfe\x00\x00\xff\xff\xfe")

        with pytest.raises(ValueError):
            read_csv_rows(path)


class TestMaskedCardLast4:
    """Tests for masked_card_last4() and first_masked_card_last4()."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("6222****1234", "1234"),
            ("6222*1234", "1234"),
            ("卡号 6222****1234 户名", "1234"),
            ("62221234", None),
            ("6222****123", None),
            ("", None),
        ],
    )
    def test_single_text(self, text: str, expected: str | None) -> None:
        assert masked_card_last4(text) == expected

    def test_first_matching_line_wins(self) -> None:
        lines = ["header\n", "6222****1234\n", "6333****5678\n"]

        assert first_masked_card_last4(lines) == "1234"

    def test_no_match_is_none(self) -> None:
        assert first_masked_card_last4(["header\n", "no card\n"]) is None

    def test_caller_bounds_the_scan(self) -> None:
        lines = ["filler\n"] * 6 + ["6222****1234\n"]

        assert first_masked_card_last4(lines[:6]) is None
        assert first_masked_card_last4(lines) == "1234"
