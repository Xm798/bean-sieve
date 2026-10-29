# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Agent skills

### Issue tracker

Issues live in Linear, team Apps (`APP-123`), project Bean-Sieve, via the `linear` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical roles: `needs-triage` and `wontfix` are the `Triage` and `Canceled` states, the other three are labels of the same name. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Project Overview

Bean-Sieve is a rule-based statement importer and reconciler for Beancount. It parses bank/payment statements (Alipay, WeChat, credit cards, etc.), matches them against existing Beancount ledger entries, and generates pending entries for missing transactions.

## Development Commands

```bash
# Install dependencies
uv sync
uv sync --extra dev  # with dev dependencies

# Install git hooks
uv run pre-commit install

# Run tests
uv run pytest                      # all tests
uv run pytest tests/test_rules.py  # single file
uv run pytest -k "test_name"       # by name pattern
uv run pytest tests/test_examples.py --update-examples  # rewrite examples/*/expected.bean

# Linting and formatting
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# Type checking
uv run pyright src/

# CLI usage
uv run bean-sieve reconcile <files> -l <ledger> -o pending.bean
uv run bean-sieve parse <files> -f table
uv run bean-sieve suggest-rules -l <ledger>
uv run bean-sieve providers

# Docs site (Fumadocs + Next.js static export, run inside website/)
pnpm install
pnpm dev           # http://localhost:3000/bean-sieve
pnpm types:check
pnpm build         # static export to website/out/
```

## Architecture

### Data Flow

```
Statement Files → Provider.parse() → [Transaction]
                                          ↓
                 Provider.coverage() → coverage.select() → rows of overlapping files dropped
                                          ↓
Ledger → Sieve.load_ledger() → Sieve.match() → MatchResult
                                          ↓
                        RulesEngine.apply() → processed transactions
                                          ↓
                        BeancountWriter.format_result() → .bean file
```

### Core Components

- **`api.py`**: Public API layer. Entry point for CLI/GUI. `full_reconcile()` orchestrates the complete workflow.
- **`core/types.py`**: `Transaction` (Pydantic model) is the universal internal representation. `MatchResult` and `ReconcileResult` hold reconciliation outputs.
- **`core/sieve.py`**: `Sieve` engine matches statement transactions against ledger entries using fuzzy date/amount matching.
- **`core/rules.py`**: `RulesEngine` applies user-defined regex rules to map transactions to accounts.
- **`core/output.py`**: `BeancountWriter` generates valid Beancount syntax from processed transactions.
- **`core/suggest.py`**: `suggest_rules()` scans ledger history and auto-generates rules from high-frequency payee→account mappings.

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

CSV/XLS providers share their tabular parsing steps (encoding detection, header search, cell decoding, …) via `providers/_tabular.py` — import from it rather than re-adding a private copy.

**Statement selection** (optional, override `coverage(file_path, transactions)`): returns one `Coverage` (account key, inclusive period, rows, `itemized`, `source`, `clip`) per account the file reports. `api._select_coverage` runs `core/coverage.py:select()` once on the parse output, before date filtering and hooks: within one provider and account, overlapping files compete and only one is adopted. `itemized=False` files never compete; whole-period files compete first, the lower index of `source` in the provider's `coverage_priority` winning (unlisted kinds rank last) and ties going to the earlier file on the command line; a loser is dropped for its whole period. `clip` sources (arbitrary-range exports) compete afterwards by the same order and keep only rows booked (`post_date or date`) outside every adopted period, so an export never displaces a statement. A merged statement emits one coverage per card. Count/total differences between two itemized overlapping files are reported as mismatches; `ReconcileResult.coverage` holds the selection and the CLI prints it. `reconcile --keep-overlap` skips the step. A provider without `coverage()` keeps every row; `bocom_credit` implements it from the printed cycle.

Every registered provider needs a docs page `website/content/docs/providers/<id>.mdx` (sections: 账单下载方式, 支持的文件格式与识别方式 with `<ProviderFacts id="<id>" />`, 配置示例, 解析出的元数据字段, 已知限制, 输出样例 embedding `examples/<id>/` via `<include>`; copy `alipay.mdx` as the template). `website/scripts/export_providers.py` (run by `pnpm dev/build/types:check`) exports the registry facts to `website/generated/providers.json` (also category titles and detection facts such as `BaseProvider.CONTENT_HEADER_CHARS`, so `provider-facts.tsx` only renders data), UTF-8 copies of text example statements to `website/generated/examples/`, and the grouped sidebar `providers/meta.json` (支付平台 / 信用卡 / 借记卡, by module package); it exits non-zero, and `tests/test_docs_providers.py` fails, when a provider has no page or a page has no provider.

**Lifecycle Hooks** (optional, override in provider):

- `pre_reconcile(transactions, context)` - transform before matching
- `post_output(content, result, context)` - append to output (e.g., settlement entries)

Both hooks run in first-seen order of the statement files. `post_output` runs once per provider in the run, receiving the whole output and passing it on to the next provider; it is dispatched from the statement paths, so a provider whose rows were all date-filtered still gets it. `pre_reconcile` runs once per provider that still has transactions, receiving that batch — or, when a provider is named explicitly, all of them. A `post_output` implementation that reads statement files must take them from `self.own_statement_paths(context)`, since `context.statement_paths` spans every provider in the run; it follows `context.provider_ids` (the run's actual assignment), so an explicitly named provider also owns files detection would not recognise.

`context.parsed` maps each statement path to that file's parse output, captured before date filtering, hooks and rules run, so a hook can compare against the whole statement instead of re-parsing it. Amounts stay at their parsed values (the pipeline only changes them via `model_copy`); other fields are shared objects and may show later in-place edits.

`post_output` returns text by design: its only implementer emits a comment report, which no Beancount directive shape can carry. Directive formatting for hooks will be revisited when a second implementer needs real directives.

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
    accounts:
      "1234": Liabilities:CreditCard:HXB
```

## 信用卡账单管理方式

| 管理方式 | 银行 | 特点 | 账户设置 |
| :--- | :--- | :--- | :--- |
| **按户管理** | 招商银行、民生银行、华夏银行、平安银行、浦发银行、北京银行、上海银行 | 信报、还款、积分按户**合并管理** | 一个账户 |
| **按卡管理** | 建设银行、广发 | 独立信报，**账单日合并**，**独立还款** | 按卡建账户 |
| **按卡管理** | 中信银行、光大银行、交通银行、农业银行、工商银行、兴业银行、中国银行、邮政储蓄 | 独立信报，独立账单，独立还款 | 按卡建账户 |

## Key Conventions

- **Privacy**: Code and comments must NOT contain any private account info (real account numbers, card numbers, personal data). Use generic examples only.
- **Test fixtures must be synthetic — NEVER copy from user-supplied files**: When writing tests for a provider, do NOT copy any field (date, amount, payee, description, summary, location, counterparty, account, order ID) from a user-provided sample file (anything under `~/Downloads/`, attached uploads, or files the user pasted/referenced). Even partially masked values (e.g. `支付宝-*张三`, real amounts paired with the user's actual dates) leak the user's data into the repo. Use clearly fake values: synthetic dates far from the real range (e.g. `2030-01-02`), round amounts (`10.00`, `20.00`), placeholder strings (`payee-a`, `loc-b`, `later-same-day`). Real-file content is for debugging only — it must never reach `tests/`.
  - **Build fixtures top-down from a synthetic template, never by editing a real failing row.** When a regression test comes from a real bug, reuse ONLY the structural shape that triggers it (e.g. "trans date carries a HH:MM time"); generate *every* value fresh. Editing the real row and keeping the fields that "look harmless" is the #1 leak vector — the harmless-looking field (a round amount like `10.00`, a generic description like `充电站消费`) is often the real transaction's actual value. A round amount is only safe if it does NOT coincide with any figure in the user's statement; if a chosen value matches a real figure, change it. The pre-commit PII scan will NOT catch this, because these fields are classified as non-sensitive — the only defense is not copying them in the first place.
- **Sensitive data check (pre-commit)**: Before completing any task and committing, MUST scan all changed/new files for sensitive information leaks — including but not limited to: real account numbers, card numbers, phone numbers, ID numbers, real names, addresses, transaction details from real statements, API keys/tokens. Any sensitive data found MUST be replaced with mock data or masked (e.g., `6222****1234`, `张*三`, `1390000****`). This applies to: source code, test fixtures, sample data, comments, commit messages, and PR descriptions.
- **Amount sign**: Expenses are positive, income is negative (in `Transaction.amount`)
- **Matching**: `Transaction.match_key` uses `order_id` if available, else `(date, abs_amount, card_suffix)`
- **Rebate top-up match**: An Alipay 信用借还 row whose combined method has a rebate-like segment after `&` (`红包|立减|优惠|补贴|满减`, e.g. `card&红包`) reports the gross amount and carries `_rebate_topup_match`. `Sieve` matches flagged rows after all other rows; a flagged row tries exact amounts first and only then a fund leg whose amount plus the ledger transaction's net rebate postings equals the row. Cross-statement dedup pairs every indirect row by exact amount first, then pairs each still-unpaired flagged row with the largest unused direct row of strictly smaller amount (same account, date and 5-minute rule). Purchases with combined methods already report the net card charge and keep exact matching. Rebate postings are those on an account listed in `defaults.rebate_account_prefixes` (default `["Income:Rebate:"]`) or on one of its sub-accounts, with any trailing colon ignored; `Config.rebate_account_prefixes()` also adds every configured `account_mappings[*].rebate_account` and `providers.*.rebate_income_account`, deduplicated in order.
- **Extra exemption**: One statement row settles one two-sided transfer. An unmatched Assets/Liabilities posting is exempt from Extra when it is the opposite leg of a matched posting of the same ledger transaction: a different account in the same coverage group, same currency, negated amount within `amount_tolerance`. Each matched posting exempts at most one sibling, taken in posting order. `api._collect_covered_groups` puts all accounts of a statement-wide provider (`per_card_statement = False`) in one group and each account of a per-card provider in a group of its own; `Sieve.match(covered_groups=None)` treats `covered_accounts` as one group.
- **Provider detection**: Checks extension first, then `filename_keywords`, then `content_keywords` (matched against the first 500 characters of a text file, or the first page of a PDF)
- **Rules priority**: Rules earlier in the YAML have higher priority
- **Internal metadata keys**: `Transaction.metadata` keys starting with `_` (named in `core/metadata_keys.py`) are bean-sieve internals and are never written to output, at either the transaction- or posting-level
- **`account_mappings` resolution**: All lookups live in `core/accounts.py` and take the first hit in YAML order, except that the two keyword resolvers first look for a pattern equal to the keyword (case-insensitive; `re.fullmatch` for the regex one) and only then fall back to their containment rule in YAML order, so keyword `零钱` resolves to a `零钱` mapping even when `零钱通` is listed first. Five rules coexist and must be picked by name, never merged: `resolve_by_method` (config `pattern` is a case-sensitive substring of `metadata['method']`; used by `api.py`), `resolve_by_method_ci` (same, case-insensitive; used by `RulesEngine` for `method` and for `contra_account_metadata_key` values), `resolve_by_bank_and_suffix` (a `bank(XXXX)` value is split, and config `pattern` must contain both the bank name and the 4-digit suffix, case-insensitive; used by `RulesEngine` as the fallback for `contra_account_metadata_key` values when `resolve_by_method_ci` misses, e.g. WeChat `零钱通转出-到bank(XXXX)` against a `bank储蓄卡(XXXX)` pattern), `resolve_by_keyword_substring` (preset `account_keyword` is a case-insensitive substring of `pattern`; used by `RulesEngine`), and `resolve_by_keyword_regex` (preset `account_keyword` is regex-searched against `pattern`, IGNORECASE; used by `api.py`). Direction matters: a generic method must not resolve to a card-specific pattern.
- **Formatting and linting**: MUST run `uv run ruff format`, `uv run ruff check`, and `uv run pyright src/` after modifying code, and fix all issues
- **Config sync**: When modifying `bean-sieve.example.yaml`, check if user's `bean-sieve.yaml` needs corresponding update. Also update JSON schema `bean-sieve.schema.json` if config structure changes.
- **Doc sync**: If code changes deviate from this CLAUDE.md, update this file accordingly
- **User docs**: The docs site lives in `website/` (pages in `website/content/docs/*.mdx`, published to GitHub Pages at `https://xm798.github.io/bean-sieve/` on push to master). When a change alters user-visible behaviour, CLI usage or config, update the matching page; README stays a short entry (intro, features, minimal install/usage, development commands) that links to the site, so feature docs go on site pages only. `website/` is excluded from the Python sdist/wheel.
- **Doc YAML snippets**: Every ```` ```yaml ```` fence in README.md and `website/content/**/*.mdx` carries one marker word after the language (other attributes such as `title="..."` may sit alongside): `config` (validated against `bean-sieve.schema.json` and loaded with `Config.from_dict`), `config-partial` (an excerpt: schema-validated with `required` skipped), or `not-config` (unchecked). `tests/test_doc_snippets.py` enforces this. The schema rejects unknown keys everywhere except the `providers` map (custom provider ids), so a new config field must be added to the schema before docs can use it.

## Test Structure

Tests use pytest with fixtures in `tests/conftest.py`. Sample data for providers is generated programmatically or uses minimal inline fixtures.

`examples/<provider_id>/` holds one golden example per provider: a synthetic statement file (any name, detected as that provider), `bean-sieve.yaml`, `ledger.bean`, and `expected.bean`. `tests/test_examples.py` runs `full_reconcile` on each directory and compares the output with `expected.bean` byte for byte, printing a unified diff on mismatch; the `; Date:` header is frozen to `2000-01-01 00:00:00` and the example directory prefix is stripped from Extra `; Source:` paths. Adding a directory adds a test case. The test also fails when a registered provider has no example directory or a directory names no registered provider, and validates each `bean-sieve.yaml` against the full `bean-sieve.schema.json` (provider pages embed it as a complete config). The example layout (reserved file names, statement listing) is defined once in `website/scripts/export_providers.py` and imported by the test. After an intended output change, rerun with `--update-examples` and review the diff. Example values follow the synthetic-fixture rules above.
