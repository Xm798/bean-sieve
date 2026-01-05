"""Configuration schema for Bean-Sieve."""

from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class DefaultsConfig(BaseModel):
    """Default settings."""

    currency: str = "CNY"
    expense_account: str = "Expenses:FIXME"
    income_account: str = "Income:FIXME"
    date_tolerance: Annotated[int, Field(ge=0, le=30)] = 2


class AccountMapping(BaseModel):
    """Unified account mapping by field value."""

    field: Literal["method", "card_suffix"] = "method"
    pattern: str
    account: str
    match: Literal["exact", "contains", "regex"] = "contains"


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
                payee=rule_data.get("payee"),
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


def load_config(path: Path) -> Config:
    """Load configuration from YAML file."""
    if not path.exists():
        return Config()

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return Config.from_dict(data)
