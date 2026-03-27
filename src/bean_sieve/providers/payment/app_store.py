"""App Store purchase history provider (reportaproblem.apple.com HAR export)."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ...core.types import Transaction
from .. import register_provider
from ..base import BaseProvider

logger = logging.getLogger(__name__)

# Currency symbol to ISO code mapping
# NOTE: "¥" is ambiguous (CNY and JPY both use it). Defaults to CNY since this
# tool is primarily used with the Chinese App Store. JPY users should verify.
CURRENCY_MAP = {
    "¥": "CNY",
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "₩": "KRW",
    "₹": "INR",
}

# Regex to split currency symbol from amount: "¥19.90" -> ("¥", "19.90")
AMOUNT_RE = re.compile(r"^([^\d\-]+)?\s*(-?[\d,]+\.?\d*)$")


@register_provider
class AppStoreProvider(BaseProvider):
    """
    Provider for App Store purchase history exported as HAR files.

    Data source: https://reportaproblem.apple.com
    Export method: Open browser DevTools, scroll to load all purchases,
    then export the /api/purchase/search requests as a HAR file.

    File format:
    - HAR 1.2 JSON containing POST requests to /api/purchase/search
    - Each response contains a paginated batch of purchase records
    - Free purchases (isFreePurchase=true) are skipped
    """

    provider_id = "app_store"
    provider_name = "App Store"
    supported_formats = [".har"]
    filename_keywords = ["apple", "reportaproblem"]
    content_keywords = ["reportaproblem.apple.com/api/purchase/search"]

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse Apple purchase history from HAR file."""
        with open(file_path, encoding="utf-8") as f:
            har = json.load(f)

        entries = har.get("log", {}).get("entries", [])
        if not entries:
            logger.warning("No entries found in HAR file: %s", file_path)
            return []

        transactions: list[Transaction] = []
        seen_item_ids: set[str] = set()

        for entry in entries:
            url = entry.get("request", {}).get("url", "")
            if "/api/purchase/search" not in url:
                continue

            resp_text = entry.get("response", {}).get("content", {}).get("text", "")
            if not resp_text:
                continue

            try:
                resp = json.loads(resp_text)
            except json.JSONDecodeError:
                logger.warning("Failed to parse response JSON in %s", file_path)
                continue

            for purchase in resp.get("purchases", []):
                for txn in self._parse_purchase(purchase, file_path, seen_item_ids):
                    transactions.append(txn)

        return transactions

    def _parse_purchase(
        self,
        purchase: dict,
        file_path: Path,
        seen_item_ids: set[str],
    ) -> list[Transaction]:
        """Parse a single purchase record into transactions."""
        results: list[Transaction] = []
        weborder = purchase.get("weborder", "")

        for pli in purchase.get("plis", []):
            if pli.get("isFreePurchase", False):
                continue

            item_id = pli.get("itemId", "")
            if item_id and item_id in seen_item_ids:
                continue
            if item_id:
                seen_item_ids.add(item_id)

            txn = self._parse_pli(pli, weborder, file_path)
            if txn:
                results.append(txn)

        return results

    def _parse_pli(
        self,
        pli: dict,
        weborder: str,
        file_path: Path,
    ) -> Transaction | None:
        """Parse a purchase line item into a Transaction."""
        amount_str = pli.get("amountPaid", "")
        amount, currency = self._parse_amount(amount_str)
        if amount is None or amount == Decimal(0):
            return None

        # isCredit means refund/credit — negate to income
        if pli.get("isCredit", False):
            amount = -amount

        pli_date_str = pli.get("pliDate", "")
        tx_date = self._parse_date(pli_date_str)
        if tx_date is None:
            return None

        content = pli.get("localizedContent") or {}
        name = content.get("nameForDisplay", "")
        detail = content.get("detailForDisplay", "")
        media_type = content.get("mediaType", "") or ""

        # description = item name, payee = app/service name
        description = name if name else detail
        payee = detail if detail and detail != name else None

        item_id = pli.get("itemId", "")
        # Use composite order_id to avoid match_key collision for multi-PLI purchases
        order_id = f"{weborder}-{item_id}" if weborder and item_id else weborder

        return Transaction(
            date=tx_date.date(),
            time=tx_date.time(),
            amount=amount,
            currency=currency,
            description=description,
            payee=payee,
            order_id=order_id,
            provider=self.provider_id,
            source_file=file_path,
            source_line=None,
            metadata={
                "media_type": media_type,
                "item_id": pli.get("itemId", ""),
                "adam_id": pli.get("adamId", ""),
            },
        )

    @staticmethod
    def _parse_amount(amount_str: str | None) -> tuple[Decimal | None, str]:
        """Parse amount string like '¥19.90' into (Decimal, currency_code)."""
        if not amount_str:
            return None, "CNY"

        match = AMOUNT_RE.match(amount_str.strip())
        if not match:
            return None, "CNY"

        symbol = (match.group(1) or "").strip()
        value_str = match.group(2).replace(",", "")
        if not value_str:
            return None, "CNY"

        currency = CURRENCY_MAP.get(symbol, "CNY")

        try:
            return Decimal(value_str), currency
        except (InvalidOperation, ValueError):
            return None, currency

    @staticmethod
    def _parse_date(date_str: str | None) -> datetime | None:
        """Parse ISO datetime string like '2026-03-22T04:00:44Z'."""
        if not date_str:
            return None
        try:
            # Handle both "2026-03-22T04:00:44Z" and "2026-03-22T04:00:44.264Z"
            cleaned = date_str.replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)
            return dt.replace(tzinfo=None)
        except (ValueError, TypeError):
            return None
