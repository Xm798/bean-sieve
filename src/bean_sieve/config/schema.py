"""Configuration schema for Bean-Sieve."""

from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field
from ruamel.yaml import YAML


class DefaultsConfig(BaseModel):
    """Default settings."""

    ledger: str | None = None
    currency: str = "CNY"
    expense_account: str = "Expenses:FIXME"
    income_account: str = "Income:FIXME"
    date_tolerance: Annotated[int, Field(ge=0, le=30)] = 2
    # Metadata fields to include in output (None means include all)
    # Common fields: time, order_id, source, category, method, peer_account, etc.
    output_metadata: list[str] | None = None
    # Sort output by datetime: "asc" (ascending), "desc" (descending), or None (no sort)
    sort_by_time: Annotated[str | None, Field(pattern=r"^(asc|desc)$")] = "asc"
    # Default transaction flag: "*" (cleared) or "!" (pending)
    flag: Annotated[str, Field(pattern=r"^[*!]$")] = "!"


class AccountMapping(BaseModel):
    """Account mapping by payment method (metadata['method'])."""

    pattern: str
    account: str


class RuleCondition(BaseModel):
    """Rule matching conditions."""

    description: str | None = None
    payee: str | None = None
    card_suffix: str | None = None
    provider: str | None = None
    time_range: str | None = None
    min_amount: float | None = None
    max_amount: float | None = None


class RuleAction(BaseModel):
    """Rule action to apply when matched."""

    contra_account: str | None = None
    payee: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    flag: str = "*"
    ignore: bool = False


class Rule(BaseModel):
    """A single mapping rule."""

    condition: RuleCondition
    action: RuleAction
    priority: int = 0

    model_config = ConfigDict(validate_assignment=True)


class PredictorConfig(BaseModel):
    """Smart-importer configuration."""

    enabled: bool = False
    min_confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.8
    training_data: str = "books/"


class ProviderConfig(BaseModel):
    """Provider-specific configuration."""

    # Card suffix (last 4 digits) -> account name mapping
    # Used by credit card providers like HXB to map transactions to specific card accounts
    card_accounts: dict[str, str] = Field(default_factory=dict)

    # Bill account for credit card settlements
    bill_account: str = ""


class Config(BaseModel):
    """Complete Bean-Sieve configuration."""

    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    account_mappings: list[AccountMapping] = Field(default_factory=list)
    rules: list[Rule] = Field(default_factory=list)
    predictor: PredictorConfig = Field(default_factory=PredictorConfig)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)

    model_config = ConfigDict(validate_assignment=True)

    def get_provider_config(self, provider_id: str) -> ProviderConfig:
        """Get configuration for a specific provider."""
        return self.providers.get(provider_id, ProviderConfig())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        """Create Config from a dictionary (parsed YAML)."""
        defaults = DefaultsConfig(**data.get("defaults", {}))

        account_mappings = [
            AccountMapping(**item) for item in data.get("account_mappings", [])
        ]

        rules = []
        for i, rule_data in enumerate(data.get("rules", [])):
            condition = RuleCondition(
                description=rule_data.get("description"),
                payee=rule_data.get("payee"),
                card_suffix=rule_data.get("card_suffix"),
                provider=rule_data.get("provider"),
                time_range=rule_data.get("time"),
                min_amount=rule_data.get("min_amount"),
                max_amount=rule_data.get("max_amount"),
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

        predictor_data = data.get("predictor", {})
        predictor = (
            PredictorConfig(**predictor_data) if predictor_data else PredictorConfig()
        )

        providers = {
            provider_id: ProviderConfig(**provider_data)
            for provider_id, provider_data in data.get("providers", {}).items()
        }

        return cls(
            defaults=defaults,
            account_mappings=account_mappings,
            rules=rules,
            predictor=predictor,
            providers=providers,
        )


def get_yaml() -> YAML:
    """Get a configured YAML instance that preserves comments and formatting."""
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def load_config(path: Path) -> Config:
    """Load configuration from YAML file."""
    if not path.exists():
        return Config()

    yaml = get_yaml()
    with open(path, encoding="utf-8") as f:
        data = yaml.load(f) or {}

    return Config.from_dict(data)
