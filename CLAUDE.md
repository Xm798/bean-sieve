# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Bean-Sieve is a rule-based statement importer and reconciler for Beancount. It parses bank/payment statements (Alipay, WeChat, credit cards, etc.), matches them against existing Beancount ledger entries, and generates pending entries for missing transactions.

## Development Commands

```bash
# Install dependencies
uv sync
uv sync --extra dev  # with dev dependencies

# Run tests
uv run pytest                      # all tests
uv run pytest tests/test_rules.py  # single file
uv run pytest -k "test_name"       # by name pattern

# Linting and formatting
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# CLI usage
uv run bean-sieve reconcile <files> -l <ledger> -o pending.bean
uv run bean-sieve parse <files> -f table
uv run bean-sieve providers
```

## Architecture

### Data Flow

```
Statement Files → Provider.parse() → [Transaction]
                                          ↓
Ledger → Sieve.load_ledger() → Sieve.match() → MatchResult
                                          ↓
                        RulesEngine.apply() → processed transactions
                                          ↓
                        SmartPredictor.predict() (optional ML)
                                          ↓
                        BeancountWriter.format_result() → .bean file
```

### Core Components

- **`api.py`**: Public API layer. Entry point for CLI/GUI. `full_reconcile()` orchestrates the complete workflow.
- **`core/types.py`**: `Transaction` (Pydantic model) is the universal internal representation. `MatchResult` and `ReconcileResult` hold reconciliation outputs.
- **`core/sieve.py`**: `Sieve` engine matches statement transactions against ledger entries using fuzzy date/amount matching.
- **`core/rules.py`**: `RulesEngine` applies user-defined regex rules to map transactions to accounts.
- **`core/output.py`**: `BeancountWriter` generates valid Beancount syntax from processed transactions.

### Provider System

Providers parse statement files into `Transaction` objects:

```python
from bean_sieve.providers import register_provider
from bean_sieve.providers.base import BaseProvider

@register_provider
class MyProvider(BaseProvider):
    provider_id = "my_provider"
    provider_name = "My Provider"
    supported_formats = [".csv", ".xlsx"]

    # For auto-detection
    filename_keywords = ["keyword_in_filename"]
    content_keywords = ["keyword_in_file_content"]

    def parse(self, file_path: Path) -> list[Transaction]:
        # Parse and return transactions
        ...
```

**Lifecycle Hooks** (optional, override in provider):

- `pre_reconcile(transactions, context)` - transform before matching
- `post_reconcile(result, context)` - enrich after matching
- `post_output(content, result, context)` - append to output (e.g., settlement entries)

### Configuration (bean-sieve.yaml)

```yaml
defaults:
  ledger: books/main.bean  # can be set here instead of CLI
  currency: CNY
  date_tolerance: 2

account_mappings:  # map payment methods to asset accounts
  - pattern: "建设银行信用卡"
    account: Liabilities:CreditCard:CCB

rules:  # map transactions to expense/income accounts
  - description: ".*瑞幸.*"
    payee: 瑞幸咖啡
    contra_account: Expenses:Food:Coffee

providers:
  hxb_credit:
    card_accounts:
      "1234": Liabilities:CreditCard:HXB
```

## Key Conventions

- **Privacy**: Code and comments must NOT contain any private account info (real account numbers, card numbers, personal data). Use generic examples only.
- **Amount sign**: Expenses are positive, income is negative (in `Transaction.amount`)
- **Matching**: `Transaction.match_key` uses `order_id` if available, else `(date, abs_amount, card_suffix)`
- **Provider detection**: Checks extension first, then `filename_keywords`, then `content_keywords`
- **Rules priority**: Rules earlier in the YAML have higher priority
- **Formatting and linting**: MUST run `uv run ruff format` and `uv run ruff check` after modifying code, and fix all issues
- **Config sync**: When modifying `bean-sieve.example.yaml`, check if user's `bean-sieve.yaml` needs corresponding update. Also update JSON schema `bean-sieve.schema.json` if config structure changes.
- **Doc sync**: If code changes deviate from this CLAUDE.md, update this file accordingly
- **Design docs**: If design changes, update corresponding docs in `external/docs/`

## Test Structure

Tests use pytest with fixtures in `tests/conftest.py`. Sample data for providers is generated programmatically or uses minimal inline fixtures.
