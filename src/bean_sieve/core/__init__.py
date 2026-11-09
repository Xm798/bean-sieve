"""Core modules for Bean-Sieve."""

from .output import BeancountWriter, write_output
from .preset_rules import PresetRule, PresetRuleAction, PresetRuleCondition
from .rules import RulesEngine, apply_rules
from .sieve import Sieve, SieveConfig, create_sieve
from .suggest import SuggestedRule, suggest_rules
from .types import (
    MatchResult,
    MatchSource,
    ReconcileContext,
    ReconcileResult,
    Transaction,
)

__all__ = [
    "Transaction",
    "MatchResult",
    "ReconcileResult",
    "ReconcileContext",
    "MatchSource",
    "Sieve",
    "SieveConfig",
    "create_sieve",
    "PresetRule",
    "PresetRuleCondition",
    "PresetRuleAction",
    "RulesEngine",
    "apply_rules",
    "SuggestedRule",
    "suggest_rules",
    "BeancountWriter",
    "write_output",
]
