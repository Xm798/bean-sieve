"""Base class for statement providers."""

from __future__ import annotations

import base64
import email
import quopri
import re
from abc import ABC, abstractmethod
from email.header import decode_header
from email.message import Message
from pathlib import Path
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup

from ..config import Config
from ..core.types import ReconcileContext, ReconcileResult, Transaction

if TYPE_CHECKING:
    from ..core.preset_rules import PresetRule


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

    # Keywords for file detection (override in subclasses)
    filename_pattern: re.Pattern | None = None  # e.g., re.compile(r"平安.*借记")
    filename_keywords: list[str] = []  # e.g., ["微信", "wechat"]
    content_keywords: list[str] = []  # e.g., ["微信支付账单明细"]

    @classmethod
    def can_handle(cls, file_path: Path) -> bool:
        """
        Check if this provider can handle the given file.

        Detection priority:
        1. Filename keywords (fast, no file read)
        2. Content keywords (reads file header)
        3. Fall back to extension check only if no keywords defined
        """
        # Check extension first
        if file_path.suffix.lower() not in cls.supported_formats:
            return False

        # If pattern/keywords are defined, require match
        if cls.filename_pattern or cls.filename_keywords or cls.content_keywords:
            return cls._match_filename(file_path) or cls._match_content(file_path)

        # No keywords defined, extension match is enough
        return True

    @classmethod
    def _match_filename(cls, file_path: Path) -> bool:
        """Check if filename matches pattern or contains any keyword."""
        # Pattern takes priority over keywords
        if cls.filename_pattern:
            return bool(cls.filename_pattern.search(file_path.name))
        if not cls.filename_keywords:
            return False
        filename_lower = file_path.name.lower()
        return any(kw.lower() in filename_lower for kw in cls.filename_keywords)

    @classmethod
    def _match_content(cls, file_path: Path) -> bool:
        """Check if file content contains any keyword."""
        if not cls.content_keywords:
            return False
        try:
            # Read first 500 bytes for header detection
            content = cls._read_file_header(file_path, 500)
            return any(kw in content for kw in cls.content_keywords)
        except Exception:
            return False

    @classmethod
    def _read_file_header(cls, file_path: Path, size: int = 500) -> str:
        """Read file header with encoding detection."""
        # Try common encodings
        for encoding in ["utf-8", "gbk", "gb2312", "utf-16"]:
            try:
                with open(file_path, encoding=encoding) as f:
                    return f.read(size)
            except (UnicodeDecodeError, UnicodeError):
                continue
        return ""

    # === Lifecycle Hooks (override in subclasses as needed) ===

    def pre_reconcile(
        self,
        transactions: list[Transaction],
        context: ReconcileContext,  # noqa: ARG002
    ) -> list[Transaction]:
        """
        Hook: Called before reconciliation.

        Use this to transform transactions before they are matched
        against the ledger. Default implementation returns unchanged.

        Args:
            transactions: Parsed transactions from this provider
            context: Reconciliation context with config, paths, etc.

        Returns:
            Transformed list of transactions
        """
        return transactions

    def post_output(
        self,
        content: str,
        result: ReconcileResult,  # noqa: ARG002
        context: ReconcileContext,  # noqa: ARG002
    ) -> str:
        """
        Hook: Called after output generation.

        Use this to append additional content to the generated output,
        such as settlement entries or reconciliation summaries.
        Default implementation returns unchanged.

        Args:
            content: Generated Beancount output content
            result: Reconciliation result
            context: Reconciliation context with config, paths, etc.

        Returns:
            Modified output content
        """
        return content

    # === Coverage Scope ===

    def get_covered_accounts(
        self,
        transactions: list[Transaction],  # noqa: ARG002
        config: Config,
    ) -> list[str]:
        """
        Return list of accounts covered by this provider's statement.

        Used to calculate Extra entries during reconciliation - only ledger
        entries in these accounts are considered as potential "extra" entries.

        Default implementation returns accounts from config.providers[provider_id].accounts.
        Override in subclasses for custom logic.

        Args:
            transactions: Parsed transactions from this provider
            config: Bean-Sieve configuration

        Returns:
            List of account names (e.g., ["Assets:Bank:PAB:6666"])
        """
        provider_config = config.get_provider_config(self.provider_id)
        return list(provider_config.accounts.values())

    # === Preset Rules ===

    @classmethod
    def get_preset_rules(cls) -> list[PresetRule]:
        """
        Return preset rules specific to this provider.

        Override in subclasses to define rules that automatically
        identify transaction types and lookup accounts from account_mappings.

        Returns:
            List of PresetRule objects
        """
        return []

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

    def _extract_html_from_message(self, msg: Message) -> str:
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

    def _decode_payload(self, part: Message) -> str:
        """Decode email payload with proper encoding handling."""
        payload = part.get_payload(decode=False)
        encoding = part.get("Content-Transfer-Encoding", "").lower()
        charset = part.get_content_charset() or "utf-8"

        if isinstance(payload, bytes):
            return payload.decode(charset, errors="replace")

        if not isinstance(payload, str):
            return ""

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

    def decode_subject(self, msg: Message) -> str:
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
