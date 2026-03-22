---
name: Create Provider
description: This skill should be used when the user asks to "create a provider", "add a new bank provider", "parse this statement", "analyze this bank statement file", mentions a bank statement file (csv/xlsx/xls/eml/pdf) and wants to create a parser for it, or discusses creating new statement parsers for bean-sieve. Also use when user provides a statement file and mentions a bank name expecting provider code to be generated.
version: 1.0.0
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
uv run python -c "import pandas as pd; print(pd.read_excel('<file>').head(20).to_string())"
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
    filename_keywords = ["<keyword>"]
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

For banks that send **separate statements per card** (e.g., BOCOM), set `per_card_statement = True` and:

1. **Extract statement period** from the statement file (e.g., `2025/12/14-2026/01/13`)
2. **Set `statement_period`** on each Transaction
3. **Handle cross-year dates**: For periods like 12/14-1/13, December dates use start year, January uses end year

Example from BOCOM provider:

```python
per_card_statement = True  # Enable per-card Extra filtering

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

This enables correct Extra calculation when processing multiple statements covering different cards and time periods.

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

Write unit tests for the new provider in `tests/providers/` directory:

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
        # Create sample statement file
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

Run tests:

```bash
uv run pytest tests/providers/test_<provider_id>.py -v
```

## Configuration Integration

If the provider requires configuration (e.g., card account mappings), update:

1. **User config** (`bean-sieve.yaml`):

```yaml
providers:
  <provider_id>:
    card_accounts:
      "1234": Liabilities:CreditCard:<Bank>
```

2. **Example config** (`bean-sieve.example.yaml`): Add the same structure with placeholder values.

3. **JSON Schema** (`bean-sieve.schema.json`): Add provider config schema if new fields are introduced.

Test with user's actual configuration:

```bash
# Full reconcile test
uv run bean-sieve reconcile "<sample_file>" -l <ledger_path> -o /tmp/test_output.bean
```

## Documentation Update

After provider is complete and tested, update `README.md`:

1. Add provider to the supported providers table:

```markdown
| Provider ID | Name | Formats | Filename Pattern |
|-------------|------|---------|------------------|
| <provider_id> | <中文名> | CSV | `*<keyword>*.csv` |
```

2. Document any special requirements or notes for this provider.

## Reference Files

Read these for implementation patterns:

- `src/bean_sieve/providers/base.py` - Base class
- `src/bean_sieve/core/types.py` - Transaction model
- `src/bean_sieve/providers/banks/credit/hxb.py` - EML parsing example
- `src/bean_sieve/providers/banks/credit/bocom.py` - Per-card statement example (EML)
- `src/bean_sieve/providers/payment/alipay.py` - CSV parsing example
- `src/bean_sieve/providers/banks/debit/pab.py` - XLSX parsing example
