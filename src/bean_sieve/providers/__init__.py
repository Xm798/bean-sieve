"""Statement providers registry."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from ..core.types import Transaction
from .base import BaseProvider

_P = TypeVar("_P", bound=BaseProvider)

# Provider registry: provider_id -> provider class
# Providers are registered when their modules are imported
PROVIDERS: dict[str, type[BaseProvider]] = {}


def register_provider(provider_cls: type[_P]) -> type[_P]:
    """
    Decorator to register a provider class.

    Usage:
        @register_provider
        class MyProvider(BaseProvider):
            provider_id = "my_provider"
            ...
    """
    PROVIDERS[provider_cls.provider_id] = provider_cls
    return provider_cls


def get_provider(provider_id: str) -> BaseProvider:
    """
    Get a provider instance by ID.

    Args:
        provider_id: The provider identifier (e.g., "hxb_credit")

    Returns:
        Provider instance

    Raises:
        ValueError: If provider is not found
    """
    if provider_id not in PROVIDERS:
        available = ", ".join(sorted(PROVIDERS.keys()))
        raise ValueError(
            f"Unknown provider: {provider_id}. Available: {available or 'none'}"
        )
    return PROVIDERS[provider_id]()


def auto_detect_provider(file_path: Path) -> BaseProvider | None:
    """
    Auto-detect the appropriate provider for a file.

    Args:
        file_path: Path to the statement file

    Returns:
        Provider instance if detected, None otherwise
    """
    for provider_cls in PROVIDERS.values():
        if provider_cls.can_handle(file_path):
            # Could add content-based detection here
            return provider_cls()
    return None


@dataclass(frozen=True)
class ProviderSet:
    """
    The providers taking part in one run.

    Attributes:
        files: One (path, provider) pair per statement path, in input order.
        explicit_provider: The provider named by the caller, which overrides
            per-file detection and takes every transaction of the run.
    """

    files: tuple[tuple[Path, BaseProvider], ...]
    explicit_provider: BaseProvider | None = None

    @property
    def providers(self) -> list[BaseProvider]:
        """Distinct providers, in first-appearance order of the statement paths."""
        unique: dict[str, BaseProvider] = {}
        for _path, provider in self.files:
            unique.setdefault(provider.provider_id, provider)
        return list(unique.values())

    def group(
        self, transactions: Sequence[Transaction]
    ) -> list[tuple[BaseProvider, list[Transaction]]]:
        """
        Split transactions into the batch each provider is responsible for.

        A named provider owns the whole run; otherwise each transaction goes to
        the provider that stamped it, groups keeping first-appearance order.

        Raises:
            ValueError: If a transaction carries an unknown provider id.
        """
        if self.explicit_provider is not None:
            return [(self.explicit_provider, list(transactions))]

        instances = {p.provider_id: p for p in self.providers}

        by_provider: dict[str, list[Transaction]] = defaultdict(list)
        for txn in transactions:
            by_provider[txn.provider].append(txn)

        return [
            (instances.get(pid) or get_provider(pid), txns)
            for pid, txns in by_provider.items()
        ]


def resolve_providers(
    paths: Sequence[Path],
    provider_id: str | None = None,
) -> ProviderSet:
    """
    Decide which provider handles each statement path.

    Args:
        paths: Statement files, in the order the caller gave them
        provider_id: Provider ID to use for every path, or None to detect

    Returns:
        ProviderSet for the run

    Raises:
        ValueError: If the provider ID is unknown, or a path is undetectable
    """
    if provider_id:
        provider = get_provider(provider_id)
        return ProviderSet(tuple((path, provider) for path in paths), provider)

    detected: dict[str, BaseProvider] = {}
    files: list[tuple[Path, BaseProvider]] = []
    for path in paths:
        provider = auto_detect_provider(path)
        if not provider:
            raise ValueError(f"Cannot auto-detect provider for: {path}")
        files.append((path, detected.setdefault(provider.provider_id, provider)))

    return ProviderSet(tuple(files))


def list_providers() -> list[dict[str, str]]:
    """
    List all registered providers.

    Returns:
        List of dicts with provider info
    """
    return [
        {
            "id": cls.provider_id,
            "name": cls.provider_name,
            "formats": ", ".join(cls.supported_formats),
        }
        for cls in PROVIDERS.values()
    ]


# Import provider submodules to register them
from .banks.credit import (  # noqa: E402, F401
    abc,
    boc,
    bocom,
    bosc,
    ccb,
    cgb,
    cib,
    cmb,
    cmbc,
    cncb,
    hsbchk,
    hxb,
    psbc,
    spdb,
)
from .banks.debit import abc as abc_debit  # noqa: E402, F401
from .banks.debit import boc as boc_debit  # noqa: E402, F401
from .banks.debit import bocom as bocom_debit  # noqa: E402, F401
from .banks.debit import ccb as ccb_debit  # noqa: E402, F401
from .banks.debit import cib as cib_debit  # noqa: E402, F401
from .banks.debit import cmb as cmb_debit  # noqa: E402, F401
from .banks.debit import cncbi as cncbi_debit  # noqa: E402, F401
from .banks.debit import hsbchk as hsbchk_debit  # noqa: E402, F401
from .banks.debit import (  # noqa: E402, F401
    icbc,
    pab,
    welab,
    zabank,  # noqa: E402, F401
)

# from .banks.credit import ccb, abc, cib, cmb, bosc, cgb, cmbc
# from .banks.debit import abc, cmb, bocom
from .payment import alipay, app_store, jd, meituan, wechat  # noqa: E402, F401

# from .crypto import binance, okx

__all__ = [
    "BaseProvider",
    "PROVIDERS",
    "ProviderSet",
    "register_provider",
    "get_provider",
    "auto_detect_provider",
    "resolve_providers",
    "list_providers",
]
