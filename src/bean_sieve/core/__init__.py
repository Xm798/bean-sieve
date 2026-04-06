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
    # Types
    "Transaction",
    "MatchResult",
    "ReconcileResult",
    "ReconcileContext",
    "MatchSource",
    # Sieve
    "Sieve",
    "SieveConfig",
    "create_sieve",
    # Preset Rules
    "PresetRule",
    "PresetRuleCondition",
    "PresetRuleAction",
    # Rules
    "RulesEngine",
    "apply_rules",
    # Suggest
    "SuggestedRule",
    "suggest_rules",
    # Output
    "BeancountWriter",
    "write_output",
]
