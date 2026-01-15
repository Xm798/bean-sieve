"""Interactive configuration wizard for account mappings."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from beancount import loader
from beancount.core.data import Close, Open

# Bank name keywords for smart sorting
BANK_KEYWORDS = {
    "建设银行": ["CCB", "建设", "龙卡"],
    "工商银行": ["ICBC", "工商", "工行"],
    "招商银行": ["CMB", "招商", "招行"],
    "交通银行": ["BOCOM", "交通", "交行"],
    "中国银行": ["BOC", "中行"],
    "农业银行": ["ABC", "农业", "农行"],
    "浦发银行": ["SPDB", "浦发"],
    "中信银行": ["CITIC", "中信"],
    "民生银行": ["CMBC", "民生"],
    "光大银行": ["CEB", "光大"],
    "华夏银行": ["HXB", "华夏"],
    "兴业银行": ["CIB", "兴业"],
    "平安银行": ["PAB", "平安"],
    "广发银行": ["CGB", "广发"],
    "上海银行": ["BOSC", "上海银行"],
    "微信": ["WeChat", "微信", "经营账户", "零钱通"],
    "MASTERCARD": ["MASTERCARD", "万事达", "MC"],
    "VISA": ["VISA", "维萨"],
    "银联": ["UnionPay", "银联", "CUP"],
}

ACCOUNT_TYPE_KEYWORDS = {
    "信用卡": ["CreditCard", "Credit", "信用"],
    "储蓄卡": ["Debit", "Savings", "储蓄", "Bank"],
    "借记卡": ["Debit", "Checking", "借记"],
}


@dataclass
class PaymentMethod:
    """Represents a unique payment method found in statements."""

    raw: str  # Original text from statement
    count: int  # Number of occurrences
    bank_hint: str | None = None  # Extracted bank name
    card_last4: str | None = None  # Extracted card suffix (last 4 digits)
    is_credit: bool | None = None  # True if credit card, False if debit


def extract_payment_methods(
    transactions: list, existing_patterns: set[str] | None = None
) -> list[PaymentMethod]:
    """
    Extract unique payment methods from transactions.

    Args:
        transactions: List of Transaction objects
        existing_patterns: Set of already-configured patterns (case-insensitive) to skip

    Returns:
        List of PaymentMethod in first-seen order
    """
    existing_lower = {p.lower() for p in (existing_patterns or set())}

    # Use lowercase key for deduplication, but keep first-seen original case
    seen_lower: dict[str, tuple[str, int]] = {}  # lowercase -> (original, count)

    for txn in transactions:
        method = txn.metadata.get("method", "")
        if method and method != "/" and method != "零钱" and method != "余额":
            method_lower = method.lower()
            # Skip if already configured
            if method_lower in existing_lower:
                continue
            if method_lower not in seen_lower:
                seen_lower[method_lower] = (method, 0)
            orig, count = seen_lower[method_lower]
            seen_lower[method_lower] = (orig, count + 1)

    methods = []
    for _method_lower, (raw, count) in seen_lower.items():
        pm = PaymentMethod(raw=raw, count=count)
        _parse_method_info(pm)
        methods.append(pm)

    return methods


def _parse_method_info(pm: PaymentMethod) -> None:
    """Parse bank and card info from payment method string."""
    # Extract card suffix (last 4 digits in parentheses)
    suffix_match = re.search(r"\((\d{4})\)", pm.raw)
    if suffix_match:
        pm.card_last4 = suffix_match.group(1)

    # Detect if credit card
    if "信用卡" in pm.raw or "贷记卡" in pm.raw:
        pm.is_credit = True
    elif "储蓄卡" in pm.raw or "借记卡" in pm.raw:
        pm.is_credit = False

    # Detect bank (check both bank name and keywords)
    for bank_name, keywords in BANK_KEYWORDS.items():
        if bank_name in pm.raw:
            pm.bank_hint = bank_name
            break
        for kw in keywords:
            if kw in pm.raw:
                pm.bank_hint = bank_name
                break
        if pm.bank_hint:
            break


def load_accounts_from_ledger(ledger_path: Path) -> tuple[list[str], set[str]]:
    """
    Load all account names from a Beancount ledger.

    Args:
        ledger_path: Path to main beancount file

    Returns:
        Tuple of (list of account names, set of closed account names)
    """
    if ledger_path.is_dir():
        main_file = ledger_path / "main.bean"
        if not main_file.exists():
            bean_files = list(ledger_path.glob("*.bean"))
            if bean_files:
                main_file = bean_files[0]
            else:
                return [], set()
        ledger_path = main_file

    entries, errors, options = loader.load_file(str(ledger_path))

    opened = set()
    closed = set()
    for entry in entries:
        if isinstance(entry, Open):
            opened.add(entry.account)
        elif isinstance(entry, Close):
            closed.add(entry.account)

    return sorted(opened), closed


def smart_sort_accounts(
    accounts: list[str], method: PaymentMethod, closed: set[str] | None = None
) -> list[str]:
    """
    Sort accounts by relevance to the payment method.

    Higher relevance accounts appear first. Closed accounts are sorted to the end.

    Args:
        accounts: List of account names
        method: PaymentMethod to match against
        closed: Set of closed account names

    Returns:
        Sorted list of accounts
    """
    closed = closed or set()

    def score(account: str) -> tuple[int, int, str]:
        """Returns (is_closed, -relevance, account) for sorting."""
        s = 0
        acc_lower = account.lower()

        # Match card suffix in account name
        if method.card_last4 and method.card_last4 in account:
            s += 100

        # Match bank keywords
        if method.bank_hint:
            keywords = BANK_KEYWORDS.get(method.bank_hint, [])
            for kw in keywords:
                if kw.lower() in acc_lower or kw in account:
                    s += 50
                    break

        # Match account type (credit vs debit)
        if method.is_credit is True:
            if (
                "CreditCard" in account
                or "Credit" in account
                or "Liabilities" in account
            ):
                s += 30
        elif method.is_credit is False and (
            "Debit" in account or "Savings" in account or "Assets:Bank" in account
        ):
            s += 30

        # Prefer specific account hierarchy
        if "Liabilities" in account and method.is_credit:
            s += 10
        if "Assets:Bank" in account and method.is_credit is False:
            s += 10

        # Closed accounts go to the end
        is_closed = 1 if account in closed else 0

        return (is_closed, -s, account)

    return sorted(accounts, key=score)


def generate_yaml_config(mappings: list[tuple[str, str]]) -> str:
    """
    Generate YAML configuration from mappings.

    Args:
        mappings: List of (pattern, account) tuples

    Returns:
        YAML string
    """
    lines = ["account_mappings:"]

    for pattern, account in mappings:
        lines.append(f'  - pattern: "{pattern}"')
        lines.append(f"    account: {account}")

    return "\n".join(lines)
