# Posting Metadata Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Emit `card_last4` on postings for shared-account banks (HXB/SPDB/CMB etc.), and surface lint-style diagnostics when ledger entries miss or conflict on `card_last4` metadata.

**Architecture:** Alipay/WeChat parsers extract `card_last4` from the `method` string. `api.py` infers "shared accounts" from `account_mappings` (accounts targeted by ≥2 patterns). `BeancountWriter` auto-emits `card_last4` posting meta on those accounts. `Sieve._is_match` downgrades `card_last4` from hard filter to soft check, producing `MetaDiagnostic` entries captured in `MatchResult.meta_diagnostics`. Output renders a lint-style `file:line  severity  message` section in pending.bean. New `diagnostics.meta_check` config toggles the behavior (default on).

**Tech Stack:** Python 3.12, Pydantic v2, beancount, pytest, uv.

---

## File Structure

**New files:** none. All additions fit into existing files.

**Modified files:**
- `src/bean_sieve/providers/payment/alipay.py` — extract `card_last4` from `method`
- `src/bean_sieve/providers/payment/wechat.py` — extract `card_last4` from `method`
- `src/bean_sieve/config/schema.py` — `DiagnosticsConfig`, `Config.diagnostics`
- `src/bean_sieve/core/types.py` — `MetaDiagnostic`, `MatchResult.meta_diagnostics`
- `src/bean_sieve/core/__init__.py` — export `MetaDiagnostic` (if package exports are used)
- `src/bean_sieve/core/sieve.py` — soft `card_last4` check; produce diagnostics
- `src/bean_sieve/core/output.py` — `shared_accounts` posting injection; diagnostics rendering
- `src/bean_sieve/api.py` — `_infer_shared_account_metadata`; thread into `generate_output`
- `bean-sieve.example.yaml` — document `diagnostics.meta_check`
- `bean-sieve.schema.json` — add `diagnostics` schema
- `tests/providers/test_alipay.py`, `tests/providers/test_wechat.py` — card_last4 extraction
- `tests/test_types.py` — MetaDiagnostic + MatchResult field
- `tests/test_api.py` (or new `tests/test_shared_accounts.py`) — `_infer_shared_account_metadata`
- `tests/core/test_sieve.py` — soft check, diagnostics
- `tests/core/test_output.py` — posting injection, diagnostics rendering

---

## Task 1: Extract card_last4 in Alipay provider

**Files:**
- Modify: `src/bean_sieve/providers/payment/alipay.py`
- Test: `tests/providers/test_alipay.py`

- [ ] **Step 1: Write the failing test**

Add this test to `tests/providers/test_alipay.py` (append to the existing test class; if no class, append to module):

```python
def test_card_last4_extracted_from_method_with_suffix():
    """Alipay method like '华夏银行信用卡(3855)' should populate card_last4."""
    from bean_sieve.providers.payment.alipay import AlipayProvider

    provider = AlipayProvider()
    # _extract_card_last4 is a pure helper on the provider; method string in, digits out
    assert provider._extract_card_last4("华夏银行信用卡(3855)") == "3855"
    assert provider._extract_card_last4("浦发银行信用卡(4192)") == "4192"


def test_card_last4_none_when_no_suffix():
    from bean_sieve.providers.payment.alipay import AlipayProvider

    provider = AlipayProvider()
    assert provider._extract_card_last4("余额") is None
    assert provider._extract_card_last4("余额宝") is None
    assert provider._extract_card_last4("花呗") is None
    assert provider._extract_card_last4("") is None
    assert provider._extract_card_last4("随便一段话") is None


def test_card_last4_ignores_non_trailing_digits():
    from bean_sieve.providers.payment.alipay import AlipayProvider

    provider = AlipayProvider()
    # Must be (4 digits) at the END of the string
    assert provider._extract_card_last4("某卡(1234)额外文字") is None
    assert provider._extract_card_last4("某卡(12345)") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/providers/test_alipay.py::test_card_last4_extracted_from_method_with_suffix -v`
Expected: FAIL with `AttributeError: 'AlipayProvider' object has no attribute '_extract_card_last4'`

- [ ] **Step 3: Implement the helper and wire it into `_parse_row`**

In `src/bean_sieve/providers/payment/alipay.py`:

Near the top of the file (alongside `STATEMENT_PERIOD_REGEX`), add:

```python
CARD_LAST4_REGEX = re.compile(r"\((\d{4})\)$")
```

(If `re` isn't already imported, add `import re` at the top.)

Add a method on `AlipayProvider`:

```python
@staticmethod
def _extract_card_last4(method: str | None) -> str | None:
    """Extract 4-digit card suffix from method string, e.g. '某银行信用卡(3855)' -> '3855'."""
    if not method:
        return None
    m = CARD_LAST4_REGEX.search(method)
    return m.group(1) if m else None
```

In `_parse_row` (around line 153-174), set `card_last4` on the returned `Transaction`:

```python
return Transaction(
    date=tx_datetime.date(),
    time=tx_datetime.time(),
    amount=amount,
    currency="CNY",
    description=description,
    payee=peer,
    order_id=order_id,
    card_last4=self._extract_card_last4(method),  # <-- add this line
    provider=self.provider_id,
    source_file=file_path,
    source_line=line_num,
    statement_period=statement_period,
    metadata={
        "category": category,
        "peer_account": peer_account,
        "method": method,
        "status": status,
        "merchant_id": merchant_id,
        "tx_type": tx_type_str,
        "remarks": remarks,
    },
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/providers/test_alipay.py -v`
Expected: all tests PASS, including the three new ones.

- [ ] **Step 5: Commit**

```bash
git add src/bean_sieve/providers/payment/alipay.py tests/providers/test_alipay.py
git commit -m "feat(alipay): extract card_last4 from method string"
```

---

## Task 2: Extract card_last4 in WeChat provider

**Files:**
- Modify: `src/bean_sieve/providers/payment/wechat.py`
- Test: `tests/providers/test_wechat.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/providers/test_wechat.py`:

```python
def test_card_last4_extracted_from_method_with_suffix():
    from bean_sieve.providers.payment.wechat import WechatProvider

    provider = WechatProvider()
    assert provider._extract_card_last4("招商银行信用卡(8355)") == "8355"
    assert provider._extract_card_last4("建设银行信用卡(0800)") == "0800"


def test_card_last4_none_for_wallet_methods():
    from bean_sieve.providers.payment.wechat import WechatProvider

    provider = WechatProvider()
    assert provider._extract_card_last4("零钱") is None
    assert provider._extract_card_last4("零钱通") is None
    assert provider._extract_card_last4("经营账户") is None
    assert provider._extract_card_last4("") is None
    assert provider._extract_card_last4(None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/providers/test_wechat.py::test_card_last4_extracted_from_method_with_suffix -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement helper and wire into `_parse_row`**

In `src/bean_sieve/providers/payment/wechat.py`:

Near the top (with other regex constants, e.g. `COMMISSION_REGEX`):

```python
CARD_LAST4_REGEX = re.compile(r"\((\d{4})\)$")
```

(Ensure `import re` exists.)

Add method on `WechatProvider`:

```python
@staticmethod
def _extract_card_last4(method: str | None) -> str | None:
    """Extract 4-digit card suffix from method string."""
    if not method:
        return None
    m = CARD_LAST4_REGEX.search(method)
    return m.group(1) if m else None
```

In `_parse_row` (around line 286-310), add `card_last4` to the returned `Transaction`:

```python
return Transaction(
    date=tx_datetime.date(),
    time=tx_datetime.time(),
    amount=amount,
    currency="CNY",
    description=description,
    payee=peer,
    order_id=order_id,
    card_last4=self._extract_card_last4(method),  # <-- add this line
    provider=self.provider_id,
    source_file=file_path,
    source_line=line_num,
    statement_period=statement_period,
    metadata={
        "tx_type": tx_type_str,
        "method": method,
        "status": status,
        "merchant_id": merchant_id,
        "remarks": remarks,
        "order_type": order_type_str,
        "commission": str(commission) if commission else None,
        "rebate": str(rebate) if rebate else None,
        "rebate_currency": rebate_currency,
        "_withdrawal_target": withdrawal_target,
    },
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/providers/test_wechat.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/bean_sieve/providers/payment/wechat.py tests/providers/test_wechat.py
git commit -m "feat(wechat): extract card_last4 from method string"
```

---

## Task 3: Add `DiagnosticsConfig` to schema

**Files:**
- Modify: `src/bean_sieve/config/schema.py`
- Test: `tests/test_config.py` (create a new test file if it does not already contain config tests; otherwise append)

- [ ] **Step 1: Check whether `tests/test_config.py` exists**

Run: `ls tests/test_config.py 2>/dev/null && echo EXISTS || echo MISSING`

If MISSING, create it with this header:

```python
"""Tests for Config schema."""

from bean_sieve.config.schema import Config
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_diagnostics_meta_check_defaults_to_true():
    cfg = Config()
    assert cfg.diagnostics.meta_check is True


def test_diagnostics_meta_check_can_be_disabled_via_dict():
    cfg = Config.from_dict({"diagnostics": {"meta_check": False}})
    assert cfg.diagnostics.meta_check is False


def test_diagnostics_default_section_when_absent():
    cfg = Config.from_dict({})
    assert cfg.diagnostics.meta_check is True
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'diagnostics'`

- [ ] **Step 4: Implement**

In `src/bean_sieve/config/schema.py`:

Add class above `Config`:

```python
class DiagnosticsConfig(BaseModel):
    """Diagnostic behavior toggles."""

    meta_check: bool = True
```

Add field to `Config`:

```python
class Config(BaseModel):
    """Complete Bean-Sieve configuration."""

    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    account_mappings: list[AccountMapping] = Field(default_factory=list)
    rules: list[Rule] = Field(default_factory=list)

    format: FormatConfig | None = None
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    diagnostics: DiagnosticsConfig = Field(default_factory=DiagnosticsConfig)

    model_config = ConfigDict(validate_assignment=True)
```

In `Config.from_dict`, after the `providers` dict construction (around the end of the method), extend the parsing:

```python
diagnostics_data = data.get("diagnostics") or {}
diagnostics = DiagnosticsConfig(**diagnostics_data)

return cls(
    defaults=defaults,
    account_mappings=account_mappings,
    rules=rules,
    format=format_config,
    providers=providers,
    diagnostics=diagnostics,
)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: 3 new tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/bean_sieve/config/schema.py tests/test_config.py
git commit -m "feat(config): add diagnostics.meta_check toggle (default true)"
```

---

## Task 4: Add `MetaDiagnostic` type and extend `MatchResult`

**Files:**
- Modify: `src/bean_sieve/core/types.py`
- Test: `tests/test_types.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_types.py`:

```python
def test_meta_diagnostic_construction():
    from bean_sieve.core.types import MetaDiagnostic

    d = MetaDiagnostic(
        severity="hint",
        file="books/2025/q1.bean",
        line=1234,
        account="Liabilities:Credit:HXB",
        key="card_last4",
        expected="3855",
        actual=None,
        message='books/2025/q1.bean:1234  hint  missing posting meta `card_last4: "3855"` on Liabilities:Credit:HXB',
    )
    assert d.severity == "hint"
    assert d.actual is None


def test_match_result_meta_diagnostics_default_empty():
    from bean_sieve.core.types import MatchResult

    mr = MatchResult()
    assert mr.meta_diagnostics == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_types.py::test_meta_diagnostic_construction tests/test_types.py::test_match_result_meta_diagnostics_default_empty -v`
Expected: FAIL with `ImportError` for `MetaDiagnostic`.

- [ ] **Step 3: Implement**

In `src/bean_sieve/core/types.py`:

Add imports at top (if `Literal` not already imported):

```python
from typing import TYPE_CHECKING, Any, Literal
```

Add class above `MatchResult`:

```python
class MetaDiagnostic(BaseModel):
    """Posting metadata diagnostic emitted during reconciliation."""

    severity: Literal["hint", "warn"]  # hint=missing, warn=conflict
    file: str
    line: int
    account: str
    key: str
    expected: str
    actual: str | None = None
    message: str
```

Extend `MatchResult`:

```python
class MatchResult(BaseModel):
    """Reconciliation result from Sieve engine."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    matched: list[tuple[Transaction, TxnPosting]] = Field(default_factory=list)
    missing: list[Transaction] = Field(default_factory=list)
    extra: list[TxnPosting] = Field(default_factory=list)
    meta_diagnostics: list["MetaDiagnostic"] = Field(default_factory=list)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_types.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/bean_sieve/core/types.py tests/test_types.py
git commit -m "feat(types): add MetaDiagnostic and MatchResult.meta_diagnostics"
```

---

## Task 5: Soft card_last4 check + diagnostic production in Sieve

**Files:**
- Modify: `src/bean_sieve/core/sieve.py`
- Test: `tests/core/test_sieve.py` (append; create if missing)

- [ ] **Step 1: Check test file exists**

Run: `ls tests/core/test_sieve.py 2>/dev/null || ls tests/test_sieve.py 2>/dev/null`

Use whichever path exists. If neither, create `tests/test_sieve.py` with header:

```python
"""Tests for Sieve matching engine."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.core.sieve import Sieve, SieveConfig
from bean_sieve.core.types import Transaction
```

Below refer to this file as `<sieve_test_path>`.

- [ ] **Step 2: Write the failing tests**

Append to `<sieve_test_path>`:

```python
def _write_ledger(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "ledger.bean"
    p.write_text(content, encoding="utf-8")
    return p


def test_soft_check_emits_hint_when_ledger_missing_card_last4(tmp_path):
    """Ledger entry matches by date+amount but has no card_last4 meta -> hint diagnostic."""
    ledger = _write_ledger(
        tmp_path,
        """
2025-03-15 * "瑞幸咖啡" "拿铁"
    Liabilities:Credit:HXB  -28.00 CNY
    Expenses:Food:Coffee  28.00 CNY

1900-01-01 open Liabilities:Credit:HXB
1900-01-01 open Expenses:Food:Coffee
""".strip(),
    )

    sieve = Sieve(SieveConfig(date_tolerance=0))
    sieve.load_ledger(ledger)

    txn = Transaction(
        date=date(2025, 3, 15),
        amount=Decimal("28.00"),
        currency="CNY",
        description="拿铁",
        payee="瑞幸咖啡",
        card_last4="3855",
        account="Liabilities:Credit:HXB",
        provider="alipay",
    )
    result = sieve.match([txn], meta_check=True)

    assert len(result.matched) == 1
    assert len(result.missing) == 0
    assert len(result.meta_diagnostics) == 1
    d = result.meta_diagnostics[0]
    assert d.severity == "hint"
    assert d.key == "card_last4"
    assert d.expected == "3855"
    assert d.actual is None
    assert d.account == "Liabilities:Credit:HXB"


def test_soft_check_emits_warn_when_ledger_card_last4_differs(tmp_path):
    """Ledger meta conflicts with statement -> warn diagnostic, still matches (no missing)."""
    ledger = _write_ledger(
        tmp_path,
        """
2025-03-15 * "瑞幸咖啡" "拿铁"
    card_last4: "4192"
    Liabilities:Credit:HXB  -28.00 CNY
    Expenses:Food:Coffee  28.00 CNY

1900-01-01 open Liabilities:Credit:HXB
1900-01-01 open Expenses:Food:Coffee
""".strip(),
    )
    sieve = Sieve(SieveConfig(date_tolerance=0))
    sieve.load_ledger(ledger)

    txn = Transaction(
        date=date(2025, 3, 15),
        amount=Decimal("28.00"),
        currency="CNY",
        description="拿铁",
        payee="瑞幸咖啡",
        card_last4="3855",
        account="Liabilities:Credit:HXB",
        provider="alipay",
    )
    result = sieve.match([txn], meta_check=True)

    assert len(result.matched) == 1
    assert len(result.missing) == 0
    assert len(result.meta_diagnostics) == 1
    d = result.meta_diagnostics[0]
    assert d.severity == "warn"
    assert d.actual == "4192"
    assert d.expected == "3855"


def test_hard_filter_retained_when_meta_check_disabled(tmp_path):
    """meta_check=False -> conflicting card_last4 still rejects the match (legacy behavior)."""
    ledger = _write_ledger(
        tmp_path,
        """
2025-03-15 * "瑞幸咖啡" "拿铁"
    card_last4: "4192"
    Liabilities:Credit:HXB  -28.00 CNY
    Expenses:Food:Coffee  28.00 CNY

1900-01-01 open Liabilities:Credit:HXB
1900-01-01 open Expenses:Food:Coffee
""".strip(),
    )
    sieve = Sieve(SieveConfig(date_tolerance=0))
    sieve.load_ledger(ledger)

    txn = Transaction(
        date=date(2025, 3, 15),
        amount=Decimal("28.00"),
        currency="CNY",
        description="拿铁",
        payee="瑞幸咖啡",
        card_last4="3855",
        account="Liabilities:Credit:HXB",
        provider="alipay",
    )
    result = sieve.match([txn], meta_check=False)

    assert len(result.matched) == 0
    assert len(result.missing) == 1
    assert result.meta_diagnostics == []


def test_matched_ledger_with_identical_card_last4_no_diagnostic(tmp_path):
    ledger = _write_ledger(
        tmp_path,
        """
2025-03-15 * "瑞幸咖啡" "拿铁"
    card_last4: "3855"
    Liabilities:Credit:HXB  -28.00 CNY
    Expenses:Food:Coffee  28.00 CNY

1900-01-01 open Liabilities:Credit:HXB
1900-01-01 open Expenses:Food:Coffee
""".strip(),
    )
    sieve = Sieve(SieveConfig(date_tolerance=0))
    sieve.load_ledger(ledger)

    txn = Transaction(
        date=date(2025, 3, 15),
        amount=Decimal("28.00"),
        currency="CNY",
        description="拿铁",
        payee="瑞幸咖啡",
        card_last4="3855",
        account="Liabilities:Credit:HXB",
        provider="alipay",
    )
    result = sieve.match([txn], meta_check=True)

    assert len(result.matched) == 1
    assert result.meta_diagnostics == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest <sieve_test_path>::test_soft_check_emits_hint_when_ledger_missing_card_last4 -v`
Expected: FAIL — `match()` does not accept `meta_check` kwarg, or diagnostics missing.

- [ ] **Step 4: Implement in `sieve.py`**

Change `Sieve.match` signature and body in `src/bean_sieve/core/sieve.py`:

```python
def match(
    self,
    transactions: Iterable[Transaction],
    covered_accounts: list[str] | None = None,
    covered_ranges: dict[str, list[tuple[date, date]]] | None = None,
    meta_check: bool = True,
) -> MatchResult:
    """
    Match statement transactions against loaded ledger entries.

    When meta_check=True (default), card_last4 is a soft check: matches still
    succeed on date/amount/payee, but mismatches/missing metadata surface as
    MetaDiagnostic entries. When meta_check=False, card_last4 acts as a hard
    filter (legacy behavior).
    """
    transactions = list(transactions)
    matched: list[tuple[Transaction, TxnPosting]] = []
    missing: list[Transaction] = []
    diagnostics: list[MetaDiagnostic] = []
    used_ledger_entries: set[int] = set()

    for txn in transactions:
        match = self._find_match(txn, used_ledger_entries, meta_check=meta_check)
        if match:
            matched.append((txn, match))
            used_ledger_entries.add(id(match))
            if meta_check:
                diag = self._diagnose_meta(txn, match)
                if diag is not None:
                    diagnostics.append(diag)
        else:
            missing.append(txn)

    # Find extra ledger entries (unchanged logic below) ...
    extra = []
    for entry in self._ledger_entries:
        if id(entry) in used_ledger_entries:
            continue
        if (
            covered_accounts is not None
            and entry.posting.account not in covered_accounts
        ):
            continue
        if covered_ranges is not None:
            account = entry.posting.account
            entry_date = entry.txn.date
            if account in covered_ranges and not self._in_covered_range(
                account, entry_date, covered_ranges
            ):
                continue
        extra.append(entry)

    return MatchResult(
        matched=matched,
        missing=missing,
        extra=extra,
        meta_diagnostics=diagnostics,
    )
```

Add import at top of `sieve.py`:

```python
from .types import MatchResult, MetaDiagnostic, Transaction
```

Thread `meta_check` through `_find_match` and `_is_match`:

```python
def _find_match(
    self, txn: Transaction, used: set[int], meta_check: bool = True
) -> TxnPosting | None:
    """Find a matching ledger entry for the given transaction."""
    if txn.order_id:
        for entry in self._ledger_entries:
            if id(entry) in used:
                continue
            if self._match_by_order_id(txn, entry):
                return entry

    candidates = self._get_candidates(txn)
    for candidate in candidates:
        if id(candidate) in used:
            continue
        if self._is_match(txn, candidate, meta_check=meta_check):
            return candidate
    return None
```

Modify `_is_match` — replace the existing card suffix block (lines ~302-305) with:

```python
    # Card suffix: hard filter only when meta_check is off (legacy behavior)
    if not meta_check and txn.card_last4:
        meta_card = bean_txn.meta.get("card_last4")
        if meta_card and meta_card != txn.card_last4:
            return False

    return True
```

And update the `_is_match` signature:

```python
def _is_match(
    self, txn: Transaction, entry: TxnPosting, meta_check: bool = True
) -> bool:
```

Add the diagnostic producer:

```python
def _diagnose_meta(
    self, txn: Transaction, entry: TxnPosting
) -> MetaDiagnostic | None:
    """Produce a MetaDiagnostic for card_last4 mismatch or absence."""
    if not txn.card_last4:
        return None
    bean_txn = entry.txn
    meta_card = bean_txn.meta.get("card_last4")
    file = bean_txn.meta.get("filename", "<unknown>")
    line = int(bean_txn.meta.get("lineno", 0) or 0)
    account = entry.posting.account

    if meta_card is None:
        message = (
            f'{file}:{line}  hint  missing posting meta '
            f'`card_last4: "{txn.card_last4}"` on {account}'
        )
        return MetaDiagnostic(
            severity="hint",
            file=file,
            line=line,
            account=account,
            key="card_last4",
            expected=txn.card_last4,
            actual=None,
            message=message,
        )
    if str(meta_card) != txn.card_last4:
        message = (
            f"{file}:{line}  warn  posting meta `card_last4` mismatch on "
            f'{account}: ledger "{meta_card}", statement "{txn.card_last4}"'
        )
        return MetaDiagnostic(
            severity="warn",
            file=file,
            line=line,
            account=account,
            key="card_last4",
            expected=txn.card_last4,
            actual=str(meta_card),
            message=message,
        )
    return None
```

- [ ] **Step 5: Run the sieve tests**

Run: `uv run pytest <sieve_test_path> -v`
Expected: all 4 new tests PASS.

- [ ] **Step 6: Run the full test suite to check for regressions**

Run: `uv run pytest -x`
Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/bean_sieve/core/sieve.py <sieve_test_path>
git commit -m "feat(sieve): soft card_last4 check with MetaDiagnostic output"
```

---

## Task 6: `_infer_shared_account_metadata` helper

**Files:**
- Modify: `src/bean_sieve/api.py`
- Test: `tests/test_api.py` (append; create if missing)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api.py` (or create the file with a `from bean_sieve.api import ...` header):

```python
from bean_sieve.api import _infer_shared_account_metadata
from bean_sieve.config.schema import AccountMapping, Config


def test_shared_accounts_includes_account_with_multiple_patterns():
    cfg = Config(
        account_mappings=[
            AccountMapping(pattern="华夏银行信用卡(3855)", account="Liabilities:Credit:HXB"),
            AccountMapping(pattern="华夏银行信用卡(9999)", account="Liabilities:Credit:HXB"),
            AccountMapping(pattern="浦发银行信用卡(4192)", account="Liabilities:Credit:SPDB"),
        ]
    )
    shared = _infer_shared_account_metadata(cfg)
    assert "Liabilities:Credit:HXB" in shared
    assert "Liabilities:Credit:SPDB" not in shared


def test_shared_accounts_empty_when_all_unique():
    cfg = Config(
        account_mappings=[
            AccountMapping(pattern="a", account="Assets:A"),
            AccountMapping(pattern="b", account="Assets:B"),
        ]
    )
    assert _infer_shared_account_metadata(cfg) == set()


def test_shared_accounts_empty_config():
    assert _infer_shared_account_metadata(Config()) == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api.py -v`
Expected: FAIL with `ImportError: cannot import name '_infer_shared_account_metadata'`.

- [ ] **Step 3: Implement**

In `src/bean_sieve/api.py`, add the helper near the other private helpers (e.g. after `_apply_fixme_fallback`):

```python
def _infer_shared_account_metadata(config: Config) -> set[str]:
    """
    Return the set of accounts that are targeted by 2+ patterns in
    account_mappings. These are "shared" accounts (e.g. HXB/SPDB) where
    posting-level card_last4 is needed to disambiguate physical cards.
    """
    counts: dict[str, int] = defaultdict(int)
    for mapping in config.account_mappings:
        counts[mapping.account] += 1
    return {account for account, n in counts.items() if n >= 2}
```

(`defaultdict` is already imported from `collections` at top of `api.py`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_api.py -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/bean_sieve/api.py tests/test_api.py
git commit -m "feat(api): infer shared accounts from account_mappings"
```

---

## Task 7: Writer auto-injects `card_last4` on shared accounts

**Files:**
- Modify: `src/bean_sieve/core/output.py`
- Test: `tests/core/test_output.py` (append; create if missing)

- [ ] **Step 1: Check test file exists**

Run: `ls tests/core/test_output.py 2>/dev/null || ls tests/test_output.py 2>/dev/null`

Use whichever exists. If neither, create `tests/test_output.py` with:

```python
"""Tests for BeancountWriter."""

from datetime import date
from decimal import Decimal

from bean_sieve.core.output import BeancountWriter
from bean_sieve.core.types import Transaction
```

Refer below as `<output_test_path>`.

- [ ] **Step 2: Write the failing test**

Append to `<output_test_path>`:

```python
def test_shared_account_posting_emits_card_last4():
    writer = BeancountWriter(shared_accounts={"Liabilities:Credit:HXB"})
    txn = Transaction(
        date=date(2025, 3, 15),
        amount=Decimal("28.00"),
        currency="CNY",
        description="拿铁",
        payee="瑞幸咖啡",
        card_last4="3855",
        account="Liabilities:Credit:HXB",
        contra_account="Expenses:Food:Coffee",
        provider="alipay",
    )
    output = writer.format_transaction(txn)
    assert 'card_last4: "3855"' in output
    # The card_last4 should appear on a posting line (indented), not as
    # transaction-level meta (which would be a separate case).
    assert "Liabilities:Credit:HXB" in output


def test_non_shared_account_omits_card_last4_posting_meta():
    writer = BeancountWriter(shared_accounts=set())
    txn = Transaction(
        date=date(2025, 3, 15),
        amount=Decimal("28.00"),
        currency="CNY",
        description="拿铁",
        payee="瑞幸咖啡",
        card_last4="3855",
        account="Assets:Bank:CCB:1386",
        contra_account="Expenses:Food:Coffee",
        provider="alipay",
    )
    output = writer.format_transaction(txn)
    # card_last4 should not be emitted as posting-level meta
    assert 'card_last4: "3855"' not in output


def test_explicit_posting_metadata_does_not_duplicate():
    """Explicit _posting_metadata + shared account -> single card_last4 line."""
    writer = BeancountWriter(shared_accounts={"Liabilities:Credit:HXB"})
    txn = Transaction(
        date=date(2025, 3, 15),
        amount=Decimal("28.00"),
        currency="CNY",
        description="拿铁",
        payee="瑞幸咖啡",
        card_last4="3855",
        account="Liabilities:Credit:HXB",
        contra_account="Expenses:Food:Coffee",
        provider="alipay",
        metadata={"_posting_metadata": ["card_last4"]},
    )
    output = writer.format_transaction(txn)
    assert output.count('card_last4: "3855"') == 1
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest <output_test_path>::test_shared_account_posting_emits_card_last4 -v`
Expected: FAIL — `BeancountWriter.__init__() got an unexpected keyword argument 'shared_accounts'`.

- [ ] **Step 4: Implement**

In `src/bean_sieve/core/output.py`:

Update `BeancountWriter.__init__`:

```python
def __init__(
    self,
    default_expense: str = "Expenses:FIXME",
    default_income: str = "Income:FIXME",
    default_rebate: str = "Rebate:FIXME",
    output_metadata: list[str] | None = None,
    sort_by_time: str | None = "asc",
    default_flag: str = "!",
    shared_accounts: set[str] | None = None,
):
    self.default_expense = default_expense
    self.default_income = default_income
    self.default_rebate = default_rebate
    self.output_metadata = output_metadata
    self.sort_by_time = sort_by_time
    self.default_flag = default_flag
    self.shared_accounts = shared_accounts or set()
```

In `_format_postings`, replace the posting-metadata loop with one that merges explicit + auto keys and dedupes:

```python
# Compute effective posting-metadata keys for this posting:
# explicit provider config + auto-inject for shared accounts.
explicit_meta_keys = list(txn.metadata.get("_posting_metadata", []))
auto_meta_keys: list[str] = []
if txn.account in self.shared_accounts and txn.card_last4:
    auto_meta_keys.append("card_last4")

seen: set[str] = set()
for key in explicit_meta_keys + auto_meta_keys:
    if key in seen:
        continue
    seen.add(key)
    value = getattr(txn, key, None) or txn.metadata.get(key)
    if value:
        postings.append(f'    {key}: "{value}"')
```

(Replace the existing `for key in posting_meta:` block at roughly lines 161-166.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest <output_test_path> -v`
Expected: all 3 new tests PASS.

- [ ] **Step 6: Run the full test suite**

Run: `uv run pytest -x`
Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/bean_sieve/core/output.py <output_test_path>
git commit -m "feat(output): auto-emit card_last4 on shared accounts"
```

---

## Task 8: Render `meta_diagnostics` section in writer output

**Files:**
- Modify: `src/bean_sieve/core/output.py`
- Test: `<output_test_path>`

- [ ] **Step 1: Write the failing test**

Append to `<output_test_path>`:

```python
def test_format_result_renders_meta_diagnostics_section():
    from bean_sieve.core.types import MatchResult, MetaDiagnostic, ReconcileResult

    diagnostics = [
        MetaDiagnostic(
            severity="hint",
            file="books/2025/q1.bean",
            line=1234,
            account="Liabilities:Credit:HXB",
            key="card_last4",
            expected="3855",
            actual=None,
            message='books/2025/q1.bean:1234  hint  missing posting meta `card_last4: "3855"` on Liabilities:Credit:HXB',
        ),
        MetaDiagnostic(
            severity="warn",
            file="books/2025/q2.bean",
            line=88,
            account="Liabilities:Credit:SPDB",
            key="card_last4",
            expected="3855",
            actual="4192",
            message='books/2025/q2.bean:88  warn  posting meta `card_last4` mismatch on Liabilities:Credit:SPDB: ledger "4192", statement "3855"',
        ),
    ]
    mr = MatchResult(meta_diagnostics=diagnostics)
    result = ReconcileResult(match_result=mr)

    writer = BeancountWriter()
    output = writer.format_result(result)

    assert "Metadata diagnostics (2)" in output
    assert "books/2025/q1.bean:1234  hint  missing posting meta" in output
    assert "books/2025/q2.bean:88  warn  posting meta `card_last4` mismatch" in output


def test_format_result_omits_section_when_no_diagnostics():
    from bean_sieve.core.types import MatchResult, ReconcileResult

    result = ReconcileResult(match_result=MatchResult())
    writer = BeancountWriter()
    output = writer.format_result(result)
    assert "Metadata diagnostics" not in output


def test_format_result_sorts_diagnostics(tmp_path):
    """Diagnostics sorted by (file, line, severity)."""
    from bean_sieve.core.types import MatchResult, MetaDiagnostic, ReconcileResult

    diagnostics = [
        MetaDiagnostic(
            severity="warn",
            file="books/b.bean",
            line=10,
            account="A",
            key="card_last4",
            expected="1",
            actual="2",
            message="books/b.bean:10  warn  msg",
        ),
        MetaDiagnostic(
            severity="hint",
            file="books/a.bean",
            line=50,
            account="A",
            key="card_last4",
            expected="1",
            actual=None,
            message="books/a.bean:50  hint  msg",
        ),
        MetaDiagnostic(
            severity="hint",
            file="books/a.bean",
            line=10,
            account="A",
            key="card_last4",
            expected="1",
            actual=None,
            message="books/a.bean:10  hint  msg",
        ),
    ]
    mr = MatchResult(meta_diagnostics=diagnostics)
    result = ReconcileResult(match_result=mr)
    writer = BeancountWriter()
    output = writer.format_result(result)

    idx_a10 = output.index("books/a.bean:10")
    idx_a50 = output.index("books/a.bean:50")
    idx_b10 = output.index("books/b.bean:10")
    assert idx_a10 < idx_a50 < idx_b10
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest <output_test_path>::test_format_result_renders_meta_diagnostics_section -v`
Expected: FAIL — section header not present.

- [ ] **Step 3: Implement**

In `src/bean_sieve/core/output.py`, in `format_result`, insert the diagnostics block after the Extra block and before returning. Replace the existing `format_result` method body after the `extra` loop with:

```python
def format_result(
    self, result: ReconcileResult, source_info: str | None = None
) -> str:
    """Format complete reconcile result."""
    output = StringIO()

    output.write(
        self.format_transactions(result.processed, source_info=source_info)
    )

    output.write("; --- Summary ---\n")
    output.write(f"; {result.match_result.summary}\n")

    if result.match_result.extra:
        output.write("\n")
        output.write("; " + "=" * 60 + "\n")
        output.write(
            f"; Extra entries in ledger ({len(result.match_result.extra)})\n"
        )
        output.write("; These exist in ledger but not found in statement\n")
        output.write("; " + "=" * 60 + "\n\n")

        for entry in result.match_result.extra:
            output.write(self._format_extra_entry(entry) + "\n\n")

    diagnostics = result.match_result.meta_diagnostics
    if diagnostics:
        sorted_diags = sorted(
            diagnostics, key=lambda d: (d.file, d.line, d.severity)
        )
        output.write("\n")
        output.write("; " + "=" * 60 + "\n")
        output.write(f"; Metadata diagnostics ({len(sorted_diags)})\n")
        output.write("; " + "=" * 60 + "\n")
        for d in sorted_diags:
            output.write(f"; {d.message}\n")

    return output.getvalue()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest <output_test_path> -v`
Expected: all new tests PASS.

- [ ] **Step 5: Full test suite**

Run: `uv run pytest -x`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/bean_sieve/core/output.py <output_test_path>
git commit -m "feat(output): render meta diagnostics section in format_result"
```

---

## Task 9: Wire it together in `api.py`

**Files:**
- Modify: `src/bean_sieve/api.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing integration test**

Append to `tests/test_api.py`:

```python
def test_generate_output_passes_shared_accounts_to_writer(tmp_path):
    from bean_sieve.api import generate_output
    from bean_sieve.config.schema import AccountMapping, Config
    from bean_sieve.core.types import (
        MatchResult,
        ReconcileResult,
        Transaction,
    )
    from datetime import date
    from decimal import Decimal

    cfg = Config(
        account_mappings=[
            AccountMapping(pattern="华夏银行信用卡(3855)", account="Liabilities:Credit:HXB"),
            AccountMapping(pattern="华夏银行信用卡(9999)", account="Liabilities:Credit:HXB"),
        ]
    )
    txn = Transaction(
        date=date(2025, 3, 15),
        amount=Decimal("28.00"),
        currency="CNY",
        description="拿铁",
        payee="瑞幸咖啡",
        card_last4="3855",
        account="Liabilities:Credit:HXB",
        contra_account="Expenses:Food:Coffee",
        provider="alipay",
    )
    result = ReconcileResult(
        match_result=MatchResult(),
        processed=[txn],
    )
    content = generate_output(result, config=cfg)
    assert 'card_last4: "3855"' in content


def test_reconcile_honors_diagnostics_meta_check_flag(tmp_path):
    """When diagnostics.meta_check=False, sieve uses hard filter."""
    from bean_sieve.api import reconcile, load_ledger
    from bean_sieve.config.schema import Config, DiagnosticsConfig
    from bean_sieve.core.types import Transaction
    from datetime import date
    from decimal import Decimal

    ledger_file = tmp_path / "ledger.bean"
    ledger_file.write_text(
        """
2025-03-15 * "瑞幸咖啡" "拿铁"
    card_last4: "4192"
    Liabilities:Credit:HXB  -28.00 CNY
    Expenses:Food:Coffee  28.00 CNY

1900-01-01 open Liabilities:Credit:HXB
1900-01-01 open Expenses:Food:Coffee
""".strip(),
        encoding="utf-8",
    )
    sieve = load_ledger(ledger_file, date_tolerance=0)
    txn = Transaction(
        date=date(2025, 3, 15),
        amount=Decimal("28.00"),
        currency="CNY",
        description="拿铁",
        payee="瑞幸咖啡",
        card_last4="3855",
        account="Liabilities:Credit:HXB",
        provider="alipay",
    )
    cfg = Config(diagnostics=DiagnosticsConfig(meta_check=False))
    result = reconcile([txn], sieve, config=cfg)
    # With hard filter, the conflicting meta causes no match -> txn goes to missing -> processed
    assert len(result.processed) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api.py -v`
Expected: `test_generate_output_passes_shared_accounts_to_writer` FAILs (card_last4 not emitted); second test may also fail depending on flag wiring.

- [ ] **Step 3: Implement — thread `meta_check` into `reconcile`**

In `src/bean_sieve/api.py`, update `reconcile`:

```python
def reconcile(
    transactions: list[Transaction],
    sieve: Sieve,
    config: Config | None = None,
    preset_rules: list[PresetRule] | None = None,
    covered_accounts: list[str] | None = None,
    covered_ranges: dict[str, list[tuple[date, date]]] | None = None,
) -> ReconcileResult:
    config = config or Config()

    match_result = sieve.match(
        transactions,
        covered_accounts=covered_accounts,
        covered_ranges=covered_ranges,
        meta_check=config.diagnostics.meta_check,
    )

    missing = list(match_result.missing)
    rules_engine = RulesEngine(config, preset_rules=preset_rules)
    processed = [rules_engine.apply(txn) for txn in missing]
    processed = [t for t in processed if not t.metadata.get("_ignored")]
    processed = _apply_fixme_fallback(processed, config)

    return ReconcileResult(match_result=match_result, processed=processed)
```

- [ ] **Step 4: Pass `shared_accounts` into `generate_output`**

In `src/bean_sieve/api.py`, update `generate_output`:

```python
def generate_output(
    result: ReconcileResult,
    output_path: Path | None = None,
    source_info: str | None = None,
    config: Config | None = None,
) -> str:
    config = config or Config()
    shared_accounts = _infer_shared_account_metadata(config)
    writer = BeancountWriter(
        default_expense=config.defaults.expense_account,
        default_income=config.defaults.income_account,
        output_metadata=config.defaults.output_metadata,
        sort_by_time=config.defaults.sort_by_time,
        default_flag=config.defaults.flag,
        shared_accounts=shared_accounts,
    )

    content = writer.format_result(result, source_info=source_info)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

    return content
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_api.py -v`
Expected: both new tests PASS.

- [ ] **Step 6: Full suite**

Run: `uv run pytest -x`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/bean_sieve/api.py tests/test_api.py
git commit -m "feat(api): wire shared_accounts + diagnostics.meta_check through reconcile/output"
```

---

## Task 10: Update example YAML and JSON schema

**Files:**
- Modify: `bean-sieve.example.yaml`
- Modify: `bean-sieve.schema.json`

- [ ] **Step 1: Read the current example to find insertion point**

Run: `rg -n "^providers:|^defaults:" bean-sieve.example.yaml | head`

- [ ] **Step 2: Add `diagnostics` section to example yaml**

Append a new top-level section to `bean-sieve.example.yaml` (after `providers:` block or wherever feels natural, preserving existing structure):

```yaml
# 诊断开关
# meta_check=true (默认): card_last4 软校验，匹配仍然成立，但会以 lint 风格
#   打印 hint/warn 提示 ledger 中缺失或不一致的 posting 元数据
# meta_check=false: 恢复旧行为，card_last4 作为硬过滤条件
diagnostics:
  meta_check: true
```

- [ ] **Step 3: Add `diagnostics` to `bean-sieve.schema.json`**

Read the file: `rg -n '"providers"|"properties"' bean-sieve.schema.json | head -20` to find the right object.

Inside the top-level `"properties"` object (alongside `"providers"`, `"rules"`, etc.), add:

```json
"diagnostics": {
  "type": "object",
  "description": "Diagnostic behavior toggles.",
  "properties": {
    "meta_check": {
      "type": "boolean",
      "default": true,
      "description": "When true, card_last4 is a soft check with lint-style diagnostics; when false, card_last4 acts as a hard filter (legacy behavior)."
    }
  },
  "additionalProperties": false
}
```

- [ ] **Step 4: Validate JSON schema parses**

Run: `python -c "import json; json.load(open('bean-sieve.schema.json'))"`
Expected: no error.

- [ ] **Step 5: Lint, format, typecheck the whole project**

Run the three together:

```bash
uv run ruff format src/ tests/
uv run ruff check src/ tests/
uv run pyright src/
```

Expected: no errors.

- [ ] **Step 6: Final full test run**

Run: `uv run pytest`
Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add bean-sieve.example.yaml bean-sieve.schema.json
git commit -m "docs(config): document diagnostics.meta_check in example and schema"
```

---

## Self-review

**Spec coverage:**

- Section 1 (Alipay/WeChat card_last4) → Tasks 1, 2 ✓
- Section 2 (shared_accounts inference) → Task 6 ✓
- Section 3 (posting meta injection) → Task 7 ✓
- Section 4 (soft check) → Task 5 ✓
- Section 5 (MetaDiagnostic + MatchResult field) → Task 4 ✓
- Section 6 (diagnostics rendering) → Task 8 ✓
- Section 7 (DiagnosticsConfig, schema.json, example.yaml) → Tasks 3, 10 ✓
- Section "影响范围" (api.py wiring) → Task 9 ✓

**Type/API consistency:**

- `MetaDiagnostic` fields used identically in Tasks 4, 5, 8
- `shared_accounts` param name consistent across Tasks 6, 7, 9
- `meta_check` bool threaded Task 5 (`Sieve.match`) → Task 9 (`reconcile` passes from `config.diagnostics.meta_check`)
- `DiagnosticsConfig.meta_check` introduced Task 3, consumed Task 9

**Placeholder scan:** clean — each step has full code or concrete commands.

**Test coverage:**

- Alipay / WeChat extraction: Tasks 1, 2
- Config defaults + overrides: Task 3
- Type shape: Task 4
- Sieve soft check + legacy hard filter + no-op on matching meta: Task 5
- Inference edge cases: Task 6
- Writer injection + dedupe with explicit posting_metadata + non-shared account: Task 7
- Diagnostics section render + sorting + empty case: Task 8
- End-to-end wiring: Task 9
