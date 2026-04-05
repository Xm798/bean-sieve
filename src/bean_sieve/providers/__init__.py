"""Statement providers registry."""

from pathlib import Path

from .base import BaseProvider

# Provider registry: provider_id -> provider class
# Providers are registered when their modules are imported
PROVIDERS: dict[str, type[BaseProvider]] = {}


def register_provider(provider_cls: type[BaseProvider]) -> type[BaseProvider]:
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
    hxb,
)
from .banks.debit import abc as abc_debit  # noqa: E402, F401
from .banks.debit import boc as boc_debit  # noqa: E402, F401
from .banks.debit import bocom as bocom_debit  # noqa: E402, F401
from .banks.debit import ccb as ccb_debit  # noqa: E402, F401
from .banks.debit import cib as cib_debit  # noqa: E402, F401
from .banks.debit import cmb as cmb_debit  # noqa: E402, F401
from .banks.debit import icbc, pab  # noqa: E402, F401

# from .banks.credit import ccb, abc, cib, cmb, bosc, cgb, cmbc
# from .banks.debit import abc, cmb, bocom
from .payment import alipay, app_store, jd, wechat  # noqa: E402, F401

# from .crypto import binance, okx

__all__ = [
    "BaseProvider",
    "PROVIDERS",
    "register_provider",
    "get_provider",
    "auto_detect_provider",
    "list_providers",
]
