"""
Bean-Sieve: Rule-based statement importer and reconciler for Beancount.

Usage:
    from bean_sieve import api

    # Parse statements
    transactions = api.parse_statements([Path("statement.eml")])

    # Full reconciliation
    result = api.full_reconcile(
        statement_paths=[Path("statement.eml")],
        ledger_path=Path("books/"),
        config_path=Path("bean-sieve.yaml"),
        output_path=Path("pending.bean"),
    )
"""

__version__ = "0.1.0"

from . import api
from .config import Config, load_config
from .core.types import MatchResult, MatchSource, ReconcileResult, Transaction
from .providers import BaseProvider, get_provider, list_providers

__all__ = [
    "__version__",
    "api",
    # Config
    "Config",
    "load_config",
    # Core types
    "Transaction",
    "MatchResult",
    "ReconcileResult",
    "MatchSource",
    # Providers
    "BaseProvider",
    "get_provider",
    "list_providers",
]
