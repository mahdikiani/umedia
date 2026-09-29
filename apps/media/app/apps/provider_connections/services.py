"""Provider connection business rules."""

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from fastapi_mongo_base.core.exceptions import BaseHTTPException

from plugins.client import PluginClient
from plugins.manifest import PluginManifest
from plugins.process_manager import PluginProcessManager


class RepositoryProtocol(Protocol):
    async def create(self, data: dict) -> object: ...
    async def get(
        self,
        uid: str,
        *,
        owner_id: str | None = None,
    ) -> object | None: ...
    async def update(
        self,
        uid: str,
        changes: dict,
        *,
        owner_id: str | None = None,
    ) -> object | None: ...
    async def delete(
        self,
        uid: str,
        *,
        owner_id: str | None = None,
    ) -> bool: ...


class LibrarySoftDeleteProtocol(Protocol):
    async def soft_delete_for_connection(
        self,
        provider_connection_id: str,
    ) -> int: ...


class PlacementClearProtocol(Protocol):
    async def clear_connection_references(self, connection_id: str) -> None: ...


class CipherProtocol(Protocol):
    def encrypt_json(self, value: dict) -> str: ...


class RegistryProtocol(Protocol):
    def get(self, provider_type: str) -> PluginManifest | None: ...


Connector = Callable[[PluginManifest, dict[str, Any]], Awaitable[None]]

#: Capability required to turn on `mirror_structure` (same gate the
#: MediaFile mirror path uses — flat providers like Telegram omit it).
MIRROR_CAPABILITY = "move"

LOCAL_PROVIDER_TYPE = "local"


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


class LocalProviderAdminRequired(BaseHTTPException):
    """Local filesystem storage is restricted to administrators."""

    def __init__(self) -> None:
        super().__init__(
            status_code=403,
            error_code="local_admin_required",
            detail="Local storage is restricted to administrators",
            message="Local storage is restricted to administrators",
        )


def build_plugin_connector(process_manager: PluginProcessManager) -> Connector:
    """The default `Connector`: calls the manifest's plugin process's
    real `POST /connect` over its Unix socket (docs/03-provider-system.md).

    A manifest's `remote_type` (set for plugins fronting several remote
    types, e.g. `rclone`'s `s3`/`google_drive`) is injected into the
    config the plugin receives -- the connection's own stored config never
    needs to carry it; the *manifest* the admin picked already says which
    remote type they mean.
    """

    async def connect(manifest: PluginManifest, config: dict[str, Any]) -> None:
        socket_path = process_manager.socket_path(manifest.process_key)
        client = PluginClient(socket_path)
        plugin_config = dict(config)
        if manifest.remote_type:
            plugin_config.setdefault("remote_type", manifest.remote_type)
        await client.connect(plugin_config)

    return connect


def connection_usable_by(
    connection: object,
    *,
    actor_user_id: str,
    is_admin: bool,
) -> bool:
    """Whether `actor` may use this connection for placement / file ops.

    `owner_id is None` is treated as unrestricted (test fakes); production
    rows always have an owner after migration 0010.
    """
    owner_id = getattr(connection, "owner_id", None)
    if owner_id is not None and owner_id != actor_user_id:
        return False
    provider_type = getattr(connection, "provider_type", None)
    return not (provider_type == LOCAL_PROVIDER_TYPE and not is_admin)


class ProviderConnectionService:
    """Validate and encrypt provider connection configuration."""

    def __init__(
        self,
        repository: RepositoryProtocol,
        cipher: CipherProtocol,
        registry: RegistryProtocol,
        connect: Connector,
        *,
        server_configs: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self._repository = repository
        self._cipher = cipher
        self._registry = registry
        self._connect = connect
        self._server_configs = server_configs or {}

    def availability(self, provider_type: str) -> tuple[bool, str | None]:
        manifest = self._registry.get(provider_type)
        if manifest is None:
            return False, "Unknown provider type."
        server_config = self._server_configs.get(provider_type, {})
        missing_environment = [
            field.environment_variable or field.label
            for field in manifest.config_fields
            if field.server_managed
            and field.required
            and not str(server_config.get(field.key, "")).strip()
        ]
        if not missing_environment:
            return True, None
        return (
            False,
            "Set "
            + " and ".join(missing_environment)
            + " in the server environment to enable this provider.",
        )

    def _require_mirror_supported(self, provider_type: str) -> None:
        """Refuse `mirror_structure` when the provider has no folder/move
        capability (e.g. Telegram's flat channel)."""
        manifest = self._registry.get(provider_type)
        if manifest is None:
            raise ProviderValidationError("Unknown provider type")
        if MIRROR_CAPABILITY not in manifest.capabilities:
            raise ProviderValidationError(
                f"Provider '{provider_type}' does not support mirror_structure "
                f"(missing '{MIRROR_CAPABILITY}' capability)",
            )

    async def create(
        self,
        *,
        provider_type: str,
        name: str,
        config: dict,
        owner_id: str,
        is_admin: bool = False,
        import_existing: bool = False,
        mirror_structure: bool = False,
    ) -> object:
        if provider_type == LOCAL_PROVIDER_TYPE and not is_admin:
            raise LocalProviderAdminRequired()
        manifest = self._registry.get(provider_type)
        if manifest is None:
            raise ProviderValidationError("Unknown provider type")
        available, unavailable_reason = self.availability(provider_type)
        if not available:
            raise ProviderValidationError(
                unavailable_reason or "Provider is unavailable",
            )
        if mirror_structure:
            self._require_mirror_supported(provider_type)
        normalized_input = dict(config)
        normalized_input.update(self._server_configs.get(provider_type, {}))
        missing = [
            field.label
            for field in manifest.config_fields
            if field.required and not normalized_input.get(field.key)
        ]
        if missing:
            raise ProviderValidationError(
                f"Missing required fields: {', '.join(missing)}",
            )
        allowed_keys = {field.key for field in manifest.config_fields}
        normalized_config = {
            key: value for key, value in normalized_input.items() if key in allowed_keys
        }
        try:
            await self._connect(manifest, normalized_config)
        except Exception as error:
            raise ProviderConnectionError(str(error)) from error
        return await self._repository.create(
            {
                "owner_id": owner_id,
                "provider_type": provider_type,
                "name": name.strip(),
                "encrypted_config": self._cipher.encrypt_json(normalized_config),
                "status": "configured",
                "import_existing": import_existing,
                "mirror_structure": mirror_structure,
            },
        )

    async def update(
        self,
        uid: str,
        *,
        owner_id: str | None = None,
        name: str | None = None,
        enabled: bool | None = None,
        import_existing: bool | None = None,
        mirror_structure: bool | None = None,
    ) -> object | None:
        """Rename, enable/disable and/or toggle the dual-layer flags --
        not a config change (that goes through delete + recreate, or a
        future provider-connect-flow redesign; see docs/09-tasks.md)."""
        current = await self._repository.get(uid, owner_id=owner_id)
        if current is None:
            return None
        if mirror_structure is True:
            self._validate_connection_mirror(current)

        changes = self._connection_changes(
            name=name,
            enabled=enabled,
            import_existing=import_existing,
            mirror_structure=mirror_structure,
        )
        if not changes:
            return current
        return await self._repository.update(uid, changes, owner_id=owner_id)

    def _validate_connection_mirror(self, current: object) -> None:
        provider_type = getattr(current, "provider_type", None)
        if provider_type is None and isinstance(current, dict):
            provider_type = current.get("provider_type")
        if not isinstance(provider_type, str):
            raise ProviderValidationError("Connection is missing provider_type")
        self._require_mirror_supported(provider_type)

    @staticmethod
    def _connection_changes(
        *,
        name: str | None,
        enabled: bool | None,
        import_existing: bool | None,
        mirror_structure: bool | None,
    ) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        if name is not None:
            stripped = name.strip()
            if not stripped:
                raise ProviderValidationError("Name cannot be empty")
            changes["name"] = stripped
        if enabled is not None:
            changes["enabled"] = enabled
        if import_existing is not None:
            changes["import_existing"] = import_existing
        if mirror_structure is not None:
            changes["mirror_structure"] = mirror_structure
        return changes

    async def delete(
        self,
        uid: str,
        *,
        owner_id: str | None = None,
        library: LibrarySoftDeleteProtocol | None = None,
        placement: PlacementClearProtocol | None = None,
    ) -> bool:
        """Soft-delete the connection and trash library files bound to it.

        StorageObjects and provider bytes stay. Optional `library` /
        `placement` hooks run before the connection row is marked deleted
        so placement never points at a gone uid and MediaFiles land in
        trash in the same request. Returns False when the connection is
        missing or not owned by `owner_id`.
        """
        current = await self._repository.get(uid, owner_id=owner_id)
        if current is None:
            return False
        if library is not None:
            await library.soft_delete_for_connection(uid)
        if placement is not None:
            await placement.clear_connection_references(uid)
        return await self._repository.delete(uid, owner_id=owner_id)
