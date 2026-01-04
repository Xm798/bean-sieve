"""Base class for statement providers."""

from __future__ import annotations

import base64
import email
import quopri
from abc import ABC, abstractmethod
from email.header import decode_header
from pathlib import Path

from bs4 import BeautifulSoup

from ..core.types import Transaction


class BaseProvider(ABC):
    """
    Base class for statement data source parsers.

    Subclasses must implement:
    - provider_id: Unique identifier (e.g., "hxb_credit")
    - provider_name: Display name (e.g., "Huaxia Bank Credit Card")
    - supported_formats: List of file extensions (e.g., [".eml", ".html"])
    - parse(): Parse file and return transactions
    """

    provider_id: str
    provider_name: str
    supported_formats: list[str]

    @abstractmethod
    def parse(self, file_path: Path) -> list[Transaction]:
        """
        Parse a statement file and return standardized transactions.

        Args:
            file_path: Path to the statement file

        Returns:
            List of Transaction objects
        """
        pass

    @classmethod
    def can_handle(cls, file_path: Path) -> bool:
        """Check if this provider can handle the given file."""
        return file_path.suffix.lower() in cls.supported_formats

    # === Utility methods for subclasses ===

    def extract_html_from_eml(self, file_path: Path) -> str:
        """
        Extract HTML content from an EML file.

        Automatically detects and handles different encodings:
        - Base64
        - Quoted-printable
        - Plain text
        """
        with open(file_path, "rb") as f:
            msg = email.message_from_binary_file(f)

        return self._extract_html_from_message(msg)

    def _extract_html_from_message(self, msg: email.message.Message) -> str:
        """Extract HTML from email message object."""
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/html":
                    return self._decode_payload(part)
            # Fallback to text/plain if no HTML
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain":
                    return self._decode_payload(part)
        else:
            return self._decode_payload(msg)

        return ""

    def _decode_payload(self, part: email.message.Message) -> str:
        """Decode email payload with proper encoding handling."""
        payload = part.get_payload(decode=False)
        encoding = part.get("Content-Transfer-Encoding", "").lower()
        charset = part.get_content_charset() or "utf-8"

        if isinstance(payload, bytes):
            return payload.decode(charset, errors="replace")

        if encoding == "base64":
            try:
                decoded = base64.b64decode(payload)
                return decoded.decode(charset, errors="replace")
            except Exception:
                return payload

        if encoding == "quoted-printable":
            try:
                decoded = quopri.decodestring(payload.encode())
                return decoded.decode(charset, errors="replace")
            except Exception:
                return payload

        return payload

    def decode_subject(self, msg: email.message.Message) -> str:
        """Decode email subject with proper encoding handling."""
        subject = msg.get("Subject", "")
        if not subject:
            return ""

        decoded_parts = decode_header(subject)
        result = []
        for part, encoding in decoded_parts:
            if isinstance(part, bytes):
                result.append(part.decode(encoding or "utf-8", errors="replace"))
            else:
                result.append(part)
        return "".join(result)

    def parse_html(self, html: str) -> BeautifulSoup:
        """Parse HTML content using BeautifulSoup."""
        return BeautifulSoup(html, "html.parser")

    def clean_text(self, text: str) -> str:
        """Clean and normalize text content."""
        if not text:
            return ""
        # Remove excessive whitespace
        return " ".join(text.split())
