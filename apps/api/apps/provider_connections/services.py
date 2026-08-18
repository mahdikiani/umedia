"""Provider connection business rules."""

from collections.abc import Callable
from typing import Any, Protocol

from fastapi_mongo_base.core.exceptions import BaseHTTPException

from providers.catalog import PROVIDER_CATALOG
from providers.factory import create_provider


class RepositoryProtocol(Protocol):
    async def create(self, data: dict) -> object: ...


class CipherProtocol(Protocol):
    def encrypt_json(self, value: dict) -> str: ...


class ProviderProtocol(Protocol):
    async def test_connection(self) -> None: ...


class ProviderValidationError(BaseHTTPException):
    """Raised when provider configuration is incomplete."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=422,
            error_code="invalid_provider_configuration",
            detail=detail,
            message=detail,
        )


class ProviderConnectionError(BaseHTTPException):
    """Raised when a provider rejects its connection configuration."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=400,
            error_code="provider_connection_failed",
            detail=detail,
            message="Could not connect to the storage provider",
        )


class ProviderConnectionService:
    """Validate and encrypt provider connection configuration."""

    def __init__(
        self,
        repository: RepositoryProtocol,
        cipher: CipherProtocol,
        provider_factory: Callable[[str, dict[str, Any]], ProviderProtocol] = (
            create_provider
        ),
    ) -> None:
        self._repository = repository
        self._cipher = cipher
        self._provider_factory = provider_factory

    async def create(
        self,
        *,
        provider_type: str,
        name: str,
        config: dict,
    ) -> object:
        definition = PROVIDER_CATALOG.get(provider_type)
        if definition is None:
            raise ProviderValidationError("Unknown provider type")
        missing = [
            field.label
            for field in definition.fields
            if field.required and not config.get(field.key)
        ]
        if missing:
            raise ProviderValidationError(
                f"Missing required fields: {', '.join(missing)}",
            )
        allowed_keys = {field.key for field in definition.fields}
        normalized_config = {
            key: value for key, value in config.items() if key in allowed_keys
        }
        provider = self._provider_factory(provider_type, normalized_config)
        try:
            await provider.test_connection()
        except Exception as error:
            raise ProviderConnectionError(str(error)) from error
        return await self._repository.create(
            {
                "provider_type": provider_type,
                "name": name.strip(),
                "encrypted_config": self._cipher.encrypt_json(normalized_config),
                "status": "configured",
            },
        )
