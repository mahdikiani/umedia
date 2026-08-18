"""Storage adapter factory."""

from pathlib import Path
from typing import Any

from .local import LocalStorageProvider
from .telegram import TelegramStorageProvider


def create_provider(
    provider_type: str,
    config: dict[str, Any],
    *,
    storage_root: Path = Path("/storage"),
) -> object:
    """Create the correct provider adapter for a connection."""
    if provider_type == "local":
        return LocalStorageProvider(config, allowed_root=storage_root)
    if provider_type == "telegram":
        return TelegramStorageProvider(config)
    raise ValueError(f"Unsupported provider type: {provider_type}")
