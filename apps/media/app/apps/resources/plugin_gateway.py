"""Resolves a `ProviderConnection` to its plugin and calls it.

The glue between the `Resource` domain (`apps/resources`) and the plugin
runtime (`plugins/`): decrypting a connection's config, looking up its
manifest (for `process_key`/`remote_type`), and finding the right Unix
socket are all behind this -- `ResourceService` (`services.py`) only ever
sees the plain `PluginGatewayProtocol` it depends on, never any of this.
"""

from collections.abc import AsyncIterator
from typing import Any, Protocol

from plugins.client import PluginClient
from plugins.contracts import CreateResourceIn, UpdateResourceIn
from plugins.contracts import Resource as PluginResource
from plugins.manifest import PluginManifest
from plugins.process_manager import PluginProcessManager

from .errors import ResourceValidationError


class ConnectionRepositoryProtocol(Protocol):
    async def get(self, uid: str) -> object | None: ...


class CipherProtocol(Protocol):
    def decrypt_json(self, token: str) -> dict[str, Any]: ...


class RegistryProtocol(Protocol):
    def get(self, provider_type: str) -> PluginManifest | None: ...


class PluginResourceGateway:
    """The default `PluginGatewayProtocol` implementation -- see module
    docstring."""

    def __init__(
        self,
        connections: ConnectionRepositoryProtocol,
        registry: RegistryProtocol,
        process_manager: PluginProcessManager,
        cipher: CipherProtocol,
    ) -> None:
        self._connections = connections
        self._registry = registry
        self._process_manager = process_manager
        self._cipher = cipher

    async def _resolve(
        self, provider_connection_id: str,
    ) -> tuple[PluginClient, dict[str, Any]]:
        connection = await self._connections.get(provider_connection_id)
        if connection is None:
            raise ResourceValidationError(
                f"Provider connection '{provider_connection_id}' not found",
            )
        if not connection.enabled:
            # The one thing `provider_connections.enabled` actually gates
            # (see that model's own docstring) -- checked here, the single
            # choke point every resource operation resolves a connection
            # through, rather than in each caller.
            raise ResourceValidationError(
                f"Provider connection '{provider_connection_id}' is disabled",
            )
        manifest = self._registry.get(connection.provider_type)
        if manifest is None:
            raise ResourceValidationError(
                f"Unknown provider type '{connection.provider_type}'",
            )
        config = self._cipher.decrypt_json(connection.encrypted_config)
        if manifest.remote_type:
            config.setdefault("remote_type", manifest.remote_type)
        client = PluginClient(self._process_manager.socket_path(manifest.process_key))
        return client, config

    async def create_resource(
        self,
        provider_connection_id: str,
        metadata: CreateResourceIn,
        content: bytes,
    ) -> PluginResource:
        client, config = await self._resolve(provider_connection_id)
        return await client.create_resource(config, metadata, content=content)

    async def get_resource(
        self, provider_connection_id: str, content_reference: str,
    ) -> PluginResource:
        client, config = await self._resolve(provider_connection_id)
        return await client.get_resource(config, content_reference)

    async def update_resource(
        self,
        provider_connection_id: str,
        content_reference: str,
        changes: UpdateResourceIn,
        content: bytes | None,
    ) -> PluginResource:
        client, config = await self._resolve(provider_connection_id)
        return await client.update_resource(
            config, content_reference, changes, content=content,
        )

    async def delete_resource(
        self, provider_connection_id: str, content_reference: str,
    ) -> None:
        client, config = await self._resolve(provider_connection_id)
        await client.delete_resource(config, content_reference)

    async def read_content(
        self,
        provider_connection_id: str,
        content_reference: str,
        *,
        range_header: str | None,
    ) -> AsyncIterator[bytes]:
        # Async *generator* function (see plugins/client.py's own
        # `read_content` and test_resource_service.py's FakePluginGateway
        # for why this matters): the `await self._resolve(...)` below only
        # actually runs once the caller starts iterating, but calling this
        # method itself returns the iterator immediately, matching
        # PluginGatewayProtocol's calling convention (no `await` at the
        # call site, `async for` right after).
        client, config = await self._resolve(provider_connection_id)
        async for chunk in client.read_content(
            config, content_reference, range_header=range_header,
        ):
            yield chunk
