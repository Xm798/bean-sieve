"""Shared PyMuPDF readers for PDF statement providers."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

# Spans of one printed line have tops within this many points of each other.
_LINE_TOLERANCE = 2.0


def pdf_layouts(file_path: Path) -> Iterator[dict[str, Any]]:
    """Yield each page's ``rawdict`` layout, which keeps per-character boxes."""
    import fitz

    with fitz.open(file_path) as doc:
        for page in doc:
            yield cast(dict[str, Any], page.get_text("rawdict"))


def first_page_text(file_path: Path) -> str:
    import fitz

    with fitz.open(file_path) as doc:
        return str(doc[0].get_text()) if doc.page_count else ""


def span_runs(
    span: dict[str, Any], direction: tuple[float, float] = (1.0, 0.0)
) -> list[list[dict[str, Any]]]:
    """Cut a rawdict span's characters into runs at gaps wider than its font size.

    PyMuPDF joins neighbouring table cells written one after another into a
    single span, so each run is one cell. Gaps are measured along
    ``direction``, the span's writing direction.
    """
    dx, dy = direction
    runs: list[list[dict[str, Any]]] = [[]]
    last_end: float | None = None
    for char in span["chars"]:
        x0, y0, x1, y1 = char["bbox"]
        a, b = x0 * dx + y0 * dy, x1 * dx + y1 * dy
        if last_end is not None and min(a, b) - last_end > span["size"]:
            runs.append([])
        runs[-1].append(char)
        last_end = max(a, b)
    return runs


def pdf_lines(file_path: Path) -> list[list[str]]:
    """Return the PDF's printed lines, each as its cells read left to right.

    A line is the cells whose tops lie within _LINE_TOLERANCE of its first
    cell's.
    """
    rows: list[list[tuple[float, float, str]]] = []
    for layout in pdf_layouts(file_path):
        cells = sorted(
            cell
            for block in layout["blocks"]
            for line in block.get("lines", [])
            for span in line["spans"]
            for cell in _span_cells(span)
        )
        page_start = len(rows)
        for cell in cells:
            if len(rows) == page_start or cell[0] - rows[-1][0][0] > _LINE_TOLERANCE:
                rows.append([])
            rows[-1].append(cell)
    return [[text for _, _, text in sorted(row, key=_cell_x)] for row in rows]


def _span_cells(span: dict[str, Any]) -> list[tuple[float, float, str]]:
    top = span["bbox"][1]
    return [
        (top, run[0]["bbox"][0], text)
        for run in span_runs(span)
        if run and (text := "".join(char["c"] for char in run).strip())
    ]


def _cell_x(cell: tuple[float, float, str]) -> float:
    return cell[1]
