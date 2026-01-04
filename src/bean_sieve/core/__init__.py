"""Core modules for Bean-Sieve."""

from .output import BeancountWriter, write_output
from .predictor import SmartPredictor, apply_predictions
from .rules import RulesEngine, apply_rules
from .sieve import Sieve, SieveConfig, create_sieve
from .types import MatchResult, MatchSource, ReconcileResult, Transaction

__all__ = [
    # Types
    "Transaction",
    "MatchResult",
    "ReconcileResult",
    "MatchSource",
    # Sieve
    "Sieve",
    "SieveConfig",
    "create_sieve",
    # Rules
    "RulesEngine",
    "apply_rules",
    # Predictor
    "SmartPredictor",
    "apply_predictions",
    # Output
    "BeancountWriter",
    "write_output",
]
