---
name: create-provider
description: This skill should be used when the user asks to "create a provider", "add a new bank provider", "parse this statement", "analyze this bank statement file", mentions a bank statement file (csv/xlsx/xls/eml/pdf) and wants to create a parser for it, or discusses creating new statement parsers for bean-sieve. Also use when user provides a statement file and mentions a bank name expecting provider code to be generated.
version: 2.0.0
---

# Create Bank Statement Provider for Bean-Sieve

This skill provides guidance for analyzing bank statement files and creating Provider implementations for the bean-sieve project.

## When This Skill Applies

- User provides a statement file (CSV, XLSX, XLS, EML, PDF) and mentions a bank name
- User asks to create/add a new provider for a specific bank
- User wants to parse a new type of bank statement
- User mentions creating a statement parser for bean-sieve

## Provider System Overview

Providers parse statement files into `Transaction` objects. Each provider:

- Extends `BaseProvider` from `src/bean_sieve/providers/base.py`
- Implements `parse(file_path) -> list[Transaction]`
- Defines detection keywords for auto-matching files

## Reference

There are also other projects that can parse statements, such as:

- external/china_bean_importers
- external/bill-file-converter

Before starting, it is recommended to check whether the project already contains a parser for the target bank, which can serve as a reference for the implementation. However, these projects may not work as expected in cases.

## File Analysis Process

### Step 1: Read and Analyze the Statement File

Based on file extension:

**CSV files:**

```bash
# Try different encodings
head -30 "<file>" 2>/dev/null || iconv -f GBK -t UTF-8 "<file>" | head -30
```

**XLSX/XLS files:**

```bash
# Use xlrd for .xls (BIFF8) files
uv run python -c "
import xlrd
wb = xlrd.open_workbook('<file>')
ws = wb.sheet_by_index(0)
for i in range(min(25, ws.nrows)):
    row = [ws.cell_value(i, j) for j in range(ws.ncols)]
    print(f'Row {i}: {row}')
"

# Also check cell types — xlrd returns floats for numeric cells
uv run python -c "
import xlrd
wb = xlrd.open_workbook('<file>')
ws = wb.sheet_by_index(0)
for i in range(min(5, ws.nrows)):
    types = [ws.cell_type(i, j) for j in range(ws.ncols)]
    print(f'Row {i} types: {types}')  # 0=empty, 1=text, 2=number, 3=date
"
```

**EML files:**

- Extract HTML content using `BaseProvider.extract_html_from_eml()`
- Parse HTML tables with BeautifulSoup

### Step 2: Auto-Detect Fields (DO NOT ask user)

Identify these fields automatically by pattern recognition:

| Field | Detection Pattern |
|-------|------------------|
| Date | `\d{4}[-/]\d{2}[-/]\d{2}`, `\d{2}/\d{2}` |
| Amount | Numeric with optional ¥/$, thousand separators |
| Description | Richest text content column |
| Payee | Separate column or in description |
| Card last 4 | 4-digit numeric |
| Order ID | Long alphanumeric string |

### Step 3: Ask User When Necessary

**Ask for:**

1. Ambiguous business logic (e.g., "Should '刷卡金' rows be filtered?")
2. Amount sign unclear after analyzing samples ("Are expenses positive or negative?")
3. Special transaction handling (annual fees, points redemption)

## Provider Code Structure

### File Location

| Card Type | Directory |
|-----------|-----------|
| Credit card | `src/bean_sieve/providers/banks/credit/` |
| Debit card | `src/bean_sieve/providers/banks/debit/` |
| Payment platform | `src/bean_sieve/providers/payment/` |
| Crypto | `src/bean_sieve/providers/crypto/` |

### Naming Convention

- `provider_id`: lowercase with underscores (e.g., `cmb_credit`)
- Class name: PascalCase (e.g., `CMBCreditProvider`)
- `provider_name`: Chinese display name (e.g., `"招商银行信用卡"`)

### Template

```python
"""<Bank> <card type> statement provider."""

from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

from ....core.types import Transaction
from ... import register_provider
from ...base import BaseProvider


@register_provider
class <Name>Provider(BaseProvider):
    """
    Provider for <Bank> <card type> statements.

    File format:
    - Encoding: <UTF-8/GBK>
    - Header rows: <N>
    - Columns: <key columns>
    """

    provider_id = "<id>"
    provider_name = "<中文名>"
    supported_formats = [".csv"]
    filename_keywords = ["<bank_specific_keyword>"]
    content_keywords = ["<content_keyword>"]

    # Set to True if bank sends separate statements per card
    # When True, Extra calculation filters by (account, date_range)
    per_card_statement = False

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse statement file."""
        transactions = []

        with open(file_path, encoding="utf-8") as f:
            # Parse logic here
            pass

        return transactions
```

## Transaction Model

```python
Transaction(
    date=date,              # Required
    time=time,              # Optional
    amount=Decimal,         # Required: positive=expense, negative=income
    currency="CNY",         # Required
    description=str,        # Required
    payee=str,              # Optional
    order_id=str,           # Optional
    card_last4=str,         # Optional
    provider=str,           # Required: self.provider_id
    source_file=Path,       # Required
    source_line=int,        # Required
    statement_period=tuple[date, date],  # Optional: (start, end) for per-card statements
    metadata=dict,          # Optional: extra fields
)
```

## Key Conventions

1. **Amount sign**: bean-sieve uses expense=positive, income=negative. Negate if source is opposite.

2. **Privacy**: Never include real account numbers in code. Use `"1234"` placeholders.

3. **Error handling**: Skip malformed rows with warning, don't crash.

4. **Encoding**: Try UTF-8 first, then GBK/GB2312 for Chinese statements.

5. **match_key**: Prefer order_id if available, else use `(date, abs_amount, card_suffix)`.

## Filename Detection — Avoiding False Positives

`filename_keywords` is the primary detection mechanism (especially for binary formats like XLS where `content_keywords` can't work). A poorly chosen keyword can cause one provider to hijack files meant for another.

**Rules:**

1. **Always include a bank-identifying term** — e.g., `["中信", "citic", "已出账单明细"]`, not just `["已出账单明细"]`. Generic phrases like "账单明细", "交易明细", "已出账单" appear across multiple banks and will eventually collide.
2. **Check for collisions** before finalizing — search existing providers for any that share the same `supported_formats` and could match the same filename. Run: `rg "supported_formats.*\.xls" src/bean_sieve/providers/` (or `.csv`, etc.).
3. **Prefer `filename_pattern`** (regex) over `filename_keywords` (substring) when the bank's export filename has a predictable structure (e.g., `交易明细_\d{4}_\d{8}_\d{8}`).

## XLS/XLSX Parsing — xlrd Cell Type Pitfalls

When parsing XLS (BIFF8) files with `xlrd`, cell values come back as Python types that may not match what you expect. Understanding this prevents subtle bugs that pass in tests but fail on real data.

**The core issue:** `sheet.cell_value()` returns `float` for numeric cells, even when the content looks like text (e.g., card number "8888" stored as a number returns `8888.0`). Calling `str(8888.0)` gives `"8888.0"`, not `"8888"` — breaking downstream matching, config lookups, and match_key comparison.

**Required pattern — `_normalize_cell_str`:**

```python
@staticmethod
def _normalize_cell_str(value) -> str:
    """Convert cell value to string, handling xlrd float-as-int values."""
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value).strip()
```

Apply to: `card_last4`, and any field that might be stored as a number in the XLS.

**Date cells:** Excel can store dates as serial numbers (float). `_parse_date` must handle both string `"YYYY-MM-DD"` and float date values:

```python
@staticmethod
def _parse_date(value: object, datemode: int = 0) -> date:
    """Parse date from string 'YYYY-MM-DD' or Excel serial date number."""
    if isinstance(value, float):
        y, m, d, _, _, _ = xlrd.xldate_as_tuple(value, datemode)  # type: ignore[arg-type]
        return date(y, m, d)
    date_str = str(value).strip()
    parts = date_str.split("-")
    return date(int(parts[0]), int(parts[1]), int(parts[2]))
```

Pass `wb.datemode` from the workbook to the parser.

**Amount cells:** Floats are fine for amounts since `Decimal(str(56.0))` gives `Decimal('56.0')` which is arithmetically correct. Handle both types:

```python
def _parse_amount(self, value) -> Decimal | None:
    try:
        if isinstance(value, float):
            return Decimal(str(value))
        cleaned = str(value).replace(",", "").strip()
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
```

**Column count validation:** Add an early guard before iterating rows — if the sheet has fewer columns than expected, return empty with a warning rather than silently failing on every row:

```python
if sheet.ncols < 8:  # adjust to your expected column count
    logger.warning("Expected 8+ columns, found %d in %s", sheet.ncols, file_path)
    return []
```

**Header search resilience:** When scanning for the header row, search up to 10 rows (not 5) — banks sometimes add extra metadata rows:

```python
for row_idx in range(min(10, sheet.nrows)):
```

## Optional Hooks

Override these in provider class if needed:

```python
def pre_reconcile(self, transactions, context) -> list[Transaction]:
    """Transform transactions before matching."""
    pass

def post_output(self, content, result, context) -> str:
    """Append content to output (e.g., settlement entries)."""
    pass

def get_covered_accounts(self, transactions, config) -> list[str]:
    """Return accounts covered by this statement."""
    pass

def get_covered_ranges(self, transactions, config) -> dict[str, list[tuple[date, date]]] | None:
    """Return covered date ranges per account for Extra calculation.

    Only needed if per_card_statement=True.
    Default implementation uses card_last4 + statement_period from transactions.
    """
    pass
```

## Per-Card Statement Support

For banks that send **separate statements per card** (e.g., BOCOM, CITIC), set `per_card_statement = True` and:

1. **Always add an inline comment** explaining the behavior:
   ```python
   per_card_statement = True  # <Bank> sends separate statements per card
   ```

2. **Always set `statement_period`** on each Transaction — `get_covered_ranges()` depends on it for Extra date-range filtering. Without it, all ledger entries for that card are treated as potential "Extra" regardless of date, leading to noisy reconciliation output.

3. **Extract statement period** from the statement file if available (e.g., `2025/12/14-2026/01/13` in email subject/body)

4. **If the file has no explicit period**, infer from the transaction date range after parsing:
   ```python
   if transactions:
       dates = [t.date for t in transactions]
       statement_period = (min(dates), max(dates))
       for t in transactions:
           t.statement_period = statement_period
   ```

5. **Handle cross-year dates**: For periods like 12/14-1/13, December dates use start year, January uses end year

Example from BOCOM provider:

```python
per_card_statement = True  # BOCOM sends separate statements per card

def parse(self, file_path: Path) -> list[Transaction]:
    # Extract statement period from file
    statement_period = self._extract_statement_period(soup)  # (date, date)

    # Set on each transaction
    return Transaction(
        ...
        card_last4=card_last4,
        statement_period=statement_period,
        ...
    )

def _parse_date_with_period(self, date_str: str, period: tuple[date, date] | None) -> date:
    """Handle cross-year date parsing."""
    month, day = map(int, date_str.split("/"))
    if period and period[0].year != period[1].year:
        # Cross-year: use start year for months >= start month
        if month >= period[0].month:
            return date(period[0].year, month, day)
        return date(period[1].year, month, day)
    return date(period[0].year if period else date.today().year, month, day)
```

## Validation Steps

After creating the provider:

```bash
# Format and lint
uv run ruff format src/bean_sieve/providers/
uv run ruff check src/bean_sieve/providers/ --fix
uv run pyright src/bean_sieve/providers/

# Test parsing
uv run bean-sieve parse "<sample_file>" -f table
```

## Testing

Write unit tests for the new provider in `tests/providers/` directory.

### Privacy in Test Data

Tests must NEVER use real statement data. All fixtures must be clearly synthetic:

- **Dates**: Use future dates (e.g., 2026-01-15) so they're obviously fake
- **Merchants**: Prefix with "测试" (e.g., "财付通－测试超市", "支付宝－测试餐厅")
- **Card numbers**: Use obvious placeholders like "8888", "1234" (not real suffixes)
- **Amounts**: Use round or clearly arbitrary numbers, not values copied from real statements
- **Names**: Never use real names — use generic descriptions like "消费A", "大额消费"

### Test Structure

```python
# tests/providers/test_<provider_id>.py
import pytest
from decimal import Decimal
from pathlib import Path

from bean_sieve.providers.banks.credit.<provider_id> import <Name>Provider


class Test<Name>Provider:
    """Tests for <Name>Provider."""

    def test_parse_basic(self, tmp_path: Path) -> None:
        """Test basic parsing functionality."""
        # Create sample statement file with MOCK data
        sample_file = tmp_path / "sample.csv"
        sample_file.write_text("...", encoding="utf-8")

        provider = <Name>Provider()
        transactions = provider.parse(sample_file)

        assert len(transactions) > 0
        assert transactions[0].amount == Decimal("100.00")
        # Add more assertions

    def test_skip_invalid_rows(self, tmp_path: Path) -> None:
        """Test that invalid rows are skipped gracefully."""
        pass

    def test_encoding_fallback(self, tmp_path: Path) -> None:
        """Test GBK encoding fallback for Chinese statements."""
        pass
```

### Required Test Scenarios

Beyond basic parsing, cover these edge cases:

| Scenario | Why it matters |
|----------|---------------|
| Empty statement (no data rows) | Should return `[]`, not crash |
| Invalid/empty rows mixed with valid | Should skip gracefully |
| Amounts with thousand separators | `"12,345.67"` → `Decimal("12345.67")` |
| Negative amounts (payments/refunds) | Verify sign convention |
| Foreign currency (if supported) | Test currency mapping end-to-end through `parse()` |
| Different post_date vs trans_date | Verify both are captured |
| **XLS: numeric cell values** | Write `card_last4` and amounts as numbers in xlwt to verify `_normalize_cell_str` works |
| **XLS: fewer columns than expected** | Should return `[]` with warning |
| **per_card_statement: statement_period** | Verify `statement_period` is set on all transactions |

### XLS Test Helper

For XLS providers, use `xlwt` to create mock files. Include a test that writes numeric values explicitly to exercise the float handling path:

```python
def test_numeric_cell_values(self, tmp_path):
    """Test parsing when xlrd returns floats instead of strings."""
    import xlwt
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Sheet1")
    # ... write headers ...
    ws.write(2, 3, 8888)   # numeric card_last4
    ws.write(2, 7, 56.0)   # numeric amount
    # ...
    provider = <Name>Provider()
    txns = provider.parse(path)
    assert txns[0].card_last4 == "8888"  # not "8888.0"
```

Run tests:

```bash
uv run pytest tests/providers/test_<provider_id>.py -v
```

## Configuration Integration

If the provider requires configuration (e.g., card account mappings), update:

1. **Example config** (`bean-sieve.example.yaml`): Add provider config with placeholder values (e.g., `"1234"` for card numbers). Use the same structure as existing per-card providers:

```yaml
providers:
  <provider_id>:
    accounts:
      "1234": Liabilities:CreditCard:<Bank>:1234
```

2. **JSON Schema** (`bean-sieve.schema.json`): Check if the schema uses `additionalProperties` for providers (it does — new provider IDs are automatically valid). Only update if introducing new config fields not covered by the existing schema.

3. **User config** (`bean-sieve.yaml`): Check if the user's config needs a corresponding update.

Test with user's actual configuration:

```bash
# Full reconcile test
uv run bean-sieve reconcile "<sample_file>" -l <ledger_path> -o /tmp/test_output.bean
```

## Documentation Update

After provider is complete and tested, update `README.md`:

1. **Provider table**: Add to the correct section (信用卡/借记卡/支付平台), maintaining alphabetical order within the section:

```markdown
| `<provider_id>` | <中文名> | <format> | <说明> |
```

2. **Download instructions**: If the bank supports web download (not just email), add to the 账单下载方式 section. For credit cards, update the 信用卡 subsection table:

```markdown
### 信用卡

| 银行 | 下载方式 | 备注 |
| :--- | :--- | :--- |
| <银行名> | [<链接文字>](<URL>) | <简要说明> |
```

3. **Provider-specific features**: If the provider has special behavior (e.g., rebate handling for ABC), add a dedicated section under "Provider 特定功能".

## Provider Registration

After creating the provider file, register it in `src/bean_sieve/providers/__init__.py`:

```python
from .banks.credit import (  # noqa: E402, F401
    abc,
    ...
    <new_provider>,  # Add in alphabetical order
    ...
)
```

## Code Review with Agent Teams

After the provider is created, tests pass, and linting/type-checking is clean, launch a multi-reviewer code review before committing. This catches issues that are easy to miss in isolation — xlrd type handling, privacy leaks, architecture drift, and test coverage gaps.

Use the `agent-teams:team-review` skill (or invoke `/team-review` directly) with these 4 review dimensions:

1. **Functional correctness** — parsing logic, amount sign convention, xlrd cell type handling, date parsing edge cases
2. **Privacy/security** — no real account data in code/tests/comments, compliance with CLAUDE.md privacy rules
3. **Architecture consistency** — naming conventions, per_card_statement usage, filename keyword uniqueness, config/README/schema sync
4. **Testing quality + Challenger** — mock data completeness, edge case coverage, adversarial cross-examination of design decisions (amount sign, detection keywords, statement_period)

At least one reviewer must act as a **challenger** — questioning assumptions, verifying claims against real data, and cross-examining other dimensions' findings.

Fix all confirmed findings before committing. Typical issues caught in past reviews:

- `card_last4` returning `"8888.0"` from xlrd float cells (missing `_normalize_cell_str`)
- `_parse_date` crashing on Excel serial date numbers (missing float handling)
- `per_card_statement=True` without `statement_period` silently breaking Extra date-range filtering
- Generic `filename_keywords` risking false-positive provider detection
- Missing test coverage for numeric cells, foreign currency, truncated files

## Reference Files

Read these for implementation patterns:

- `src/bean_sieve/providers/base.py` - Base class
- `src/bean_sieve/core/types.py` - Transaction model
- `src/bean_sieve/providers/banks/credit/hxb.py` - EML parsing example
- `src/bean_sieve/providers/banks/credit/bocom.py` - Per-card statement example (EML)
- `src/bean_sieve/providers/banks/credit/citic.py` - XLS parsing example (xlrd with float handling)
- `src/bean_sieve/providers/banks/debit/ccb.py` - XLS parsing example (xlrd with _normalize_cell_str)
- `src/bean_sieve/providers/payment/alipay.py` - CSV parsing example
- `src/bean_sieve/providers/banks/debit/pab.py` - XLSX parsing example
