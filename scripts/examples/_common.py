"""Writers shared by the example statement generators."""

import base64
import io
import quopri
import zipfile
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from email.header import Header
from pathlib import Path
from typing import Any

import fitz
import xlwt
from openpyxl import Workbook
from openpyxl.writer.excel import ExcelWriter

Generator = Callable[[Path], None]
GENERATORS: dict[str, Generator] = {}

# Office containers embed creation times; pinning them keeps regenerated files
# byte-identical to the committed ones.
FIXED_TIME = datetime(2030, 1, 1)


def generator(provider_id: str) -> Callable[[Generator], Generator]:
    """Register a function that writes the statement into ``examples/<id>/``."""

    def register(fn: Generator) -> Generator:
        GENERATORS[provider_id] = fn
        return fn

    return register


def write_text(out: Path, content: str, encoding: str = "utf-8") -> None:
    out.write_text(content, encoding=encoding, newline="")


def write_eml(
    out: Path,
    html: str,
    subject: str,
    *,
    sender: str = "creditcard@example.com",
    encoding: str = "quoted-printable",
    charset: str = "utf-8",
) -> None:
    raw = html.encode(charset)
    if encoding == "base64":
        body = base64.encodebytes(raw).decode("ascii")
    else:
        body = quopri.encodestring(raw).decode("ascii")
    content = (
        f"From: {sender}\n"
        f"To: user@example.com\n"
        f"Subject: {Header(subject, 'utf-8').encode()}\n"
        f"Date: Mon, 01 Apr 2030 08:00:00 +0800\n"
        f"MIME-Version: 1.0\n"
        f'Content-Type: text/html; charset="{charset}"\n'
        f"Content-Transfer-Encoding: {encoding}\n"
        f"\n{body}"
    )
    out.write_bytes(content.encode("ascii"))


def new_xls(
    sheet: str, encoding: str = "ascii"
) -> tuple[xlwt.Workbook, xlwt.Worksheet]:
    wb = xlwt.Workbook(encoding=encoding)
    return wb, wb.add_sheet(sheet)


def put_rows(ws: xlwt.Worksheet, top: int, rows: Iterable[Sequence[Any]]) -> int:
    """Write ``rows`` from row ``top`` down and return the next free row."""
    r = top - 1
    for r, row in enumerate(rows, top):
        for c, v in enumerate(row):
            ws.write(r, c, v)
    return r + 1


def write_xlsx(out: Path, rows: Iterable[Sequence[Any]]) -> None:
    wb = Workbook()
    wb.properties.created = FIXED_TIME
    wb.properties.modified = FIXED_TIME
    ws = wb.active
    assert ws is not None
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    # ExcelWriter directly, because Workbook.save() resets `modified` to now.
    ExcelWriter(wb, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)).save()
    # zipfile stamps each member with the wall-clock time.
    stamp = FIXED_TIME.timetuple()[:6]
    with zipfile.ZipFile(buf) as src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            member = zipfile.ZipInfo(info.filename, date_time=stamp)
            member.compress_type = info.compress_type
            member.external_attr = info.external_attr
            dst.writestr(member, src.read(info))


class PdfPage:
    """A single A4 page drawn with the built-in Simplified Chinese font."""

    def __init__(self, font_size: float) -> None:
        self.doc = fitz.open()
        self.page = self.doc.new_page(width=595, height=842)
        self.font_size = font_size

    def put(self, x: float, y: float, text: str, size: float | None = None) -> None:
        self.page.insert_text(
            (x, y), text, fontname="china-s", fontsize=size or self.font_size
        )

    def save(self, out: Path) -> None:
        # Empty metadata and a fixed document ID make the output reproducible.
        self.doc.set_metadata({})
        self.doc.save(out, garbage=4, deflate=True, no_new_id=True)
        self.doc.close()
