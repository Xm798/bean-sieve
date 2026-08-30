"""Names of the `Transaction.metadata` keys bean-sieve writes and reads itself.

Keys prefixed with `_` are consumed inside bean-sieve. They are kept out of the
generated ledger only by `BeancountWriter`'s explicit denylist, so a new one is
emitted verbatim unless it is added there.

The remaining ~60 keys (`method`, `balance`, …) are the provider-facing
vocabulary and stay as literals at their sites.
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
REFERENCE = "reference"
