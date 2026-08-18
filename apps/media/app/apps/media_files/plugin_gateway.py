"""Resolves a `ProviderConnection` to its plugin and calls it.

The MediaFile-era successor of `apps/resources/plugin_gateway.py` (same
resolve-connection -> decrypt-config -> socket-client pattern), extended
with the two calls the dual-layer model needs and the Resource layer
didn't: `list_resources` (the import/sync job's remote walk) and
`capabilities` (the mirror gate -- whether the provider supports
structure at all). `MediaFileService` only ever sees the plain
`PluginGatewayProtocol` in services.py, never any of this.
"""

from collections.abc import AsyncIterator
from typing import Any, Protocol

from plugins.client import PluginClient
from plugins.contracts import CreateResourceIn, UpdateResourceIn
from plugins.contracts import Resource as PluginResource
from plugins.manifest import PluginManifest
from plugins.process_manager import PluginProcessManager

from .errors import MediaFileValidationError


class ConnectionRepositoryProtocol(Protocol):
    async def get(self, uid: str) -> object | None: ...


class CipherProtocol(Protocol):
    def decrypt_json(self, token: str) -> dict[str, Any]: ...


class RegistryProtocol(Protocol):
    def get(self, provider_type: str) -> PluginManifest | None: ...


class MediaPluginGateway:
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

    async def _manifest(self, provider_connection_id: str) -> PluginManifest:
        connection = await self._connections.get(provider_connection_id)
        if connection is None:
            raise MediaFileValidationError(
                f"Provider connection '{provider_connection_id}' not found",
            )
        if not connection.enabled:
            # The one thing `provider_connections.enabled` gates --
            # checked here, the single choke point every media file
            # operation resolves a connection through.
            raise MediaFileValidationError(
                f"Provider connection '{provider_connection_id}' is disabled",
            )
        manifest = self._registry.get(connection.provider_type)
        if manifest is None:
            raise MediaFileValidationError(
                f"Unknown provider type '{connection.provider_type}'",
            )
        return manifest

    async def _resolve(
        self, provider_connection_id: str,
    ) -> tuple[PluginClient, dict[str, Any]]:
        connection = await self._connections.get(provider_connection_id)
        manifest = await self._manifest(provider_connection_id)
        config = self._cipher.decrypt_json(connection.encrypted_config)
        if manifest.remote_type:
            config.setdefault("remote_type", manifest.remote_type)
        client = PluginClient(self._process_manager.socket_path(manifest.process_key))
        return client, config

    async def capabilities(self, provider_connection_id: str) -> tuple[str, ...]:
        """The connection's manifest-declared capabilities -- the mirror
        gate reads this ("move" means the provider supports structure)."""
        manifest = await self._manifest(provider_connection_id)
        return tuple(manifest.capabilities)

    async def list_resources(
        self, provider_connection_id: str, *, parent_id: str | None = None,
    ) -> list[PluginResource]:
        client, config = await self._resolve(provider_connection_id)
        return await client.list_resources(config, parent_id=parent_id)

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
        # Async *generator* function -- calling it returns the iterator
        # immediately, matching the protocol's calling convention (no
        # `await` at the call site, `async for` right after). See
        # apps/resources/plugin_gateway.py's identical note.
        client, config = await self._resolve(provider_connection_id)
        async for chunk in client.read_content(
            config, content_reference, range_header=range_header,
        ):
            yield chunk
