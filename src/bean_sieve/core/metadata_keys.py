"""Names of the `Transaction.metadata` keys bean-sieve writes and reads itself.

Keys prefixed with `_` are consumed inside bean-sieve and never written to the
ledger: `BeancountWriter` skips the whole prefix at both the transaction and
posting level, since Beancount metadata keys must start with a lowercase
letter.

The remaining keys (`method`, `balance`, …) are the provider-facing
vocabulary and stay as literals at their sites, except those the core reads:
any provider setting `ORIGINAL_AMOUNT`/`ORIGINAL_CURRENCY` opts into matching
on the original amount.
"""

IGNORED = "_ignored"
POSTING_METADATA = "_posting_metadata"
OUTPUT_METADATA = "_output_metadata"
REBATE_ACCOUNT = "_rebate_account"
WITHDRAWAL_TARGET = "_withdrawal_target"

MATCHED_RULE = "matched_rule"
MATCHED_PRESET_RULE = "matched_preset_rule"
ORIGINAL_PAYEE = "original_payee"
ORIGINAL_DESCRIPTION = "original_description"
ORIGINAL_AMOUNT = "original_amount"
ORIGINAL_CURRENCY = "original_currency"
REFERENCE = "reference"
