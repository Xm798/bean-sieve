"""Charset resolution of ``BaseProvider`` when decoding mail bodies."""

import base64
import email
from email.message import Message
from pathlib import Path

from bean_sieve.core.types import Transaction
from bean_sieve.providers.base import BaseProvider

# "镕" lies outside GB2312 but inside GBK.
HTML = "<html><head>{meta}</head><body>账单 镕 payee-a</body></html>"


class FakeMailBank(BaseProvider):
    """Not registered, so it never affects auto-detection."""

    provider_id = "fake_mail_bank"
    provider_name = "Fake Mail Bank"
    supported_formats = [".eml"]

    def parse(self, file_path: Path) -> list[Transaction]:  # noqa: ARG002
        return []


def base64_part(body: bytes, charset: str | None) -> Message:
    part = Message()
    content_type = "text/html" + (f"; charset={charset}" if charset else "")
    part["Content-Type"] = content_type
    part["Content-Transfer-Encoding"] = "base64"
    part.set_payload(base64.b64encode(body).decode("ascii"))
    return part


def test_meta_charset_decodes_a_part_without_mime_charset() -> None:
    html = HTML.format(
        meta='<meta http-equiv="Content-Type" content="text/html; charset=GBK">'
    )
    part = base64_part(html.encode("gbk"), None)

    assert FakeMailBank()._decode_payload(part) == html


def test_gb2312_label_decodes_characters_outside_gb2312() -> None:
    html = HTML.format(meta="")
    part = base64_part(html.encode("gb18030"), "gb2312")

    assert FakeMailBank()._decode_payload(part) == html


def test_unknown_charset_falls_back_to_utf8() -> None:
    html = HTML.format(meta="")
    part = base64_part(html.encode("utf-8"), "x-unknown")

    assert FakeMailBank()._decode_payload(part) == html


def eight_bit_part(body: bytes, transfer_encoding: str) -> Message:
    raw = (
        b"Content-Type: text/html\r\n"
        + f"Content-Transfer-Encoding: {transfer_encoding}\r\n\r\n".encode()
        + body
    )
    return email.message_from_bytes(raw)


def test_meta_charset_decodes_an_8bit_part() -> None:
    html = HTML.format(meta='<meta charset="gbk">')
    part = eight_bit_part(html.encode("gbk"), "8bit")

    assert FakeMailBank()._decode_payload(part) == html


def test_7bit_ascii_part_is_unchanged() -> None:
    html = "<html><body>payee-a 10.00</body></html>"
    part = eight_bit_part(html.encode("ascii"), "7bit")

    assert FakeMailBank()._decode_payload(part) == html
