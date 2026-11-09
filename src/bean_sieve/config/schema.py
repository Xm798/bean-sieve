"""Configuration schema for Bean-Sieve."""

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from ruamel.yaml import YAML


class DefaultsConfig(BaseModel):
    ledger: str | None = None
    currency: str = "CNY"
    expense_account: str = "Expenses:FIXME"
    income_account: str = "Income:FIXME"
    # Account for fees a provider reports on a transaction, unless the
    # provider config names its own
    fee_account: str = "Expenses:FIXME"
    # Account for a rebate whose payment method's mapping names no
    # rebate_account
    rebate_account: str = "Income:Rebate:FIXME"
    date_tolerance: Annotated[int, Field(ge=0, le=30)] = 2
    # Metadata fields written to the output; None writes all. Common fields:
    # time, order_id, source, category, method, peer_account.
    output_metadata: list[str] | None = None
    # None keeps the parse order.
    sort_by_time: Annotated[str | None, Field(pattern=r"^(asc|desc)$")] = "asc"
    # Flag of rows no rule matched: "*" (cleared) or "!" (pending).
    flag: Annotated[str, Field(pattern=r"^[*!]$")] = "!"
    # Rebate income accounts (and their sub-accounts) that the gross-amount
    # match of rebate-topped repayments adds back to the fund leg
    rebate_account_prefixes: list[str] = Field(
        default_factory=lambda: ["Income:Rebate:"]
    )


class AccountMapping(BaseModel):
    """Account mapping by payment method (metadata['method'])."""

    pattern: str
    account: str
    rebate_account: str | None = None


class RuleCondition(BaseModel):
    """All set fields must match.

    ``description`` and ``payee`` are case-insensitive regexes, matched as a
    substring when not a valid regex. ``time_range`` is ``HH:MM-HH:MM`` and may
    wrap past midnight; the amount bounds compare the absolute amount.
    """

    description: str | None = None
    payee: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    card_last4: str | None = None
    provider: str | None = None
    time_range: str | None = None
    min_amount: float | None = None
    max_amount: float | None = None
    direction: Literal["expense", "income"] | None = None


class RuleAction(BaseModel):
    contra_account: str | None = None
    payee: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    flag: str = "*"
    ignore: bool = False


class Rule(BaseModel):
    """Higher ``priority`` is tried first; ``Config.from_dict`` gives earlier
    YAML rules the higher priority."""

    condition: RuleCondition
    action: RuleAction
    priority: int = 0

    model_config = ConfigDict(validate_assignment=True)


class FormatConfig(BaseModel):
    """Beanfmt formatting configuration.

    When fields are None, beanfmt's own defaults or project config
    (.beanfmt.toml) values are used.
    """

    enabled: bool = True
    indent: int | None = None
    currency_column: int | None = None
    cost_column: int | None = None
    thousands_separator: Literal["add", "remove", "keep"] | None = None
    spaces_in_braces: bool | None = None
    fixed_cjk_width: bool | None = None
    sort: Literal["asc", "desc", "off"] | None = None
    sort_timeless: Literal["begin", "end", "keep"] | None = None
    sort_exclude: list[str] | None = None

    def to_beanfmt_kwargs(self) -> dict[str, Any]:
        """Return only explicitly set fields as kwargs for beanfmt."""
        kwargs: dict[str, Any] = {}
        for field in [
            "indent",
            "currency_column",
            "cost_column",
            "thousands_separator",
            "spaces_in_braces",
            "fixed_cjk_width",
            "sort",
            "sort_timeless",
            "sort_exclude",
        ]:
            val = getattr(self, field)
            if val is not None:
                kwargs[field] = val
        return kwargs


class ProviderConfig(BaseModel):
    # Key -> account. A payment platform keys on metadata['method']
    # ("余额" -> Assets:Wallet:Alipay:Balance), a bank on card_last4
    # ("1234" -> Assets:Bank:PAB:1234). These accounts also bound the
    # accounts Extra is reported for.
    accounts: dict[str, str] = Field(default_factory=dict)
    # Transaction-level metadata fields, appended to the global output_metadata.
    output_metadata: list[str] = Field(default_factory=list)
    # Posting-level metadata fields; there is no global counterpart.
    posting_metadata: list[str] = Field(default_factory=list)
    rebate_income_account: str | None = None
    # Identify the rebate transactions already in the ledger.
    rebate_keywords: list[str] = Field(default_factory=list)
    fee_account: str | None = None
    # Append a balance directive from the last row's metadata["balance"].
    balance: bool = False


class DiagnosticsConfig(BaseModel):
    meta_check: bool = True
    # Account-name substrings that opt into card_last4 checks and
    # posting-meta injection. Empty list = no accounts in scope.
    meta_check_accounts: list[str] = Field(default_factory=list)
    # Warn when a statement transaction matched a ledger posting but other
    # postings from different ledger transactions were equally valid.
    ambiguous_match: bool = True


class Config(BaseModel):
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    account_mappings: list[AccountMapping] = Field(default_factory=list)
    rules: list[Rule] = Field(default_factory=list)

    format: FormatConfig | None = None
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    diagnostics: DiagnosticsConfig = Field(default_factory=DiagnosticsConfig)

    model_config = ConfigDict(validate_assignment=True)

    def get_provider_config(self, provider_id: str) -> ProviderConfig:
        return self.providers.get(provider_id, ProviderConfig())

    def rebate_account_prefixes(self) -> tuple[str, ...]:
        """Rebate accounts for matching: the defaults list, then every
        configured rebate account, deduplicated in first-seen order."""
        configured = [m.rebate_account for m in self.account_mappings]
        configured += [p.rebate_income_account for p in self.providers.values()]
        merged = [
            *self.defaults.rebate_account_prefixes,
            *configured,
            self.defaults.rebate_account,
        ]
        return tuple(dict.fromkeys(a for a in merged if a))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        """Build from parsed YAML, whose rule keys (``time``, ``target_payee``,
        ``target_description``) differ from the model's field names."""
        defaults = DefaultsConfig(**data.get("defaults", {}))

        account_mappings = [
            AccountMapping(**item) for item in data.get("account_mappings", [])
        ]

        rules = []
        for i, rule_data in enumerate(data.get("rules", [])):
            condition = RuleCondition(
                description=rule_data.get("description"),
                payee=rule_data.get("payee"),
                metadata=rule_data.get("metadata", {}),
                card_last4=rule_data.get("card_last4"),
                provider=rule_data.get("provider"),
                time_range=rule_data.get("time"),
                min_amount=rule_data.get("min_amount"),
                max_amount=rule_data.get("max_amount"),
                direction=rule_data.get("direction"),
            )
            action = RuleAction(
                contra_account=rule_data.get("contra_account"),
                payee=rule_data.get("target_payee"),
                description=rule_data.get("target_description"),
                tags=rule_data.get("tags", []),
                flag=rule_data.get("flag", "*"),
                ignore=rule_data.get("ignore", False),
            )
            rules.append(
                Rule(
                    condition=condition,
                    action=action,
                    priority=len(data.get("rules", [])) - i,
                )
            )

        format_data = data.get("format")
        if format_data is True:
            format_config = FormatConfig()
        elif isinstance(format_data, dict):
            format_config = FormatConfig(**format_data)
        else:
            format_config = None

        providers = {
            provider_id: ProviderConfig(**provider_data)
            for provider_id, provider_data in data.get("providers", {}).items()
        }

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


def get_yaml() -> YAML:
    """Get a configured YAML instance that preserves comments and formatting."""
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def load_config(path: Path) -> Config:
    """A missing file yields the default Config."""
    if not path.exists():
        return Config()

    yaml = get_yaml()
    with open(path, encoding="utf-8") as f:
        data = yaml.load(f) or {}

    return Config.from_dict(data)
