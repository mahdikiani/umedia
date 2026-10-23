"""Structural protocols the MediaFile service depends on."""

from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from typing import Any, Protocol

from apps.storage_objects.schemas import StorageObjectRecord
from plugins.contracts import CreateResourceIn, UpdateResourceIn
from plugins.contracts import Resource as PluginResource
from utils.pagination import Page

from .placement import PlacementSettings
from .schemas import (
    MediaFileRecord,
)


class MediaFileRepositoryProtocol(Protocol):
    async def create(self, data: dict[str, Any]) -> MediaFileRecord: ...
    async def get(self, uid: str) -> MediaFileRecord | None: ...
    async def list(
        self,
        *,
        parent_id: str | None,
        include_deleted: bool = False,
        sort: str = "name",
        order: str = "asc",
    ) -> "list[MediaFileRecord]": ...
    async def list_visible(
        self,
        *,
        actor_user_id: str,
    ) -> "list[MediaFileRecord]": ...
    async def list_for_actor(
        self,
        *,
        actor_user_id: str,
        parent_id: str | None = None,
        scope: str = "owned",
        include_deleted: bool = False,
        sort: str = "name",
        order: str = "asc",
        limit: int = 50,
        offset: int = 0,
    ) -> Page[MediaFileRecord]: ...
    async def set_starred(
        self,
        *,
        actor_user_id: str,
        media_file_uid: str,
        starred: bool,
    ) -> None: ...
    async def starred_ids(
        self,
        *,
        actor_user_id: str,
        media_file_uids: "list[str]",
    ) -> set[str]: ...
    async def add_temporary_item(
        self,
        *,
        actor_user_id: str,
        media_file_uid: str,
    ) -> bool: ...
    async def list_temporary_items(
        self,
        *,
        actor_user_id: str,
    ) -> "list[MediaFileRecord]": ...
    async def remove_temporary_item(
        self,
        *,
        actor_user_id: str,
        media_file_uid: str,
    ) -> None: ...
    async def clear_temporary_items(
        self,
        *,
        actor_user_id: str,
    ) -> None: ...
    async def update(
        self,
        uid: str,
        changes: dict[str, Any],
    ) -> MediaFileRecord: ...
    async def touch_access(self, uid: str) -> None: ...
    async def soft_delete(self, uid: str) -> None: ...
    async def soft_delete_many(self, uids: Sequence[str]) -> None: ...
    async def restore(self, uid: str) -> None: ...
    async def hard_delete(self, uid: str) -> None: ...
    async def list_expired_trash(
        self,
        *,
        cutoff: datetime,
    ) -> "list[MediaFileRecord]": ...
    async def volume_stats(
        self,
        *,
        actor_user_id: str,
    ) -> dict[str, int]: ...
    async def link_object(
        self,
        *,
        media_file_uid: str,
        storage_object_uid: str,
        role: str = "primary",
    ) -> None: ...
    async def get_by_storage_object(
        self,
        storage_object_uid: str,
    ) -> MediaFileRecord | None: ...
    async def list_uids_by_storage_object(
        self,
        storage_object_uid: str,
    ) -> "list[str]": ...
    async def find_import_root(
        self,
        *,
        provider_connection_id: str,
        owner_id: str | None = None,
    ) -> MediaFileRecord | None: ...
    async def list_for_connection(
        self,
        provider_connection_id: str,
    ) -> "list[MediaFileRecord]": ...


class StorageObjectRepositoryProtocol(Protocol):
    async def get(self, uid: str) -> StorageObjectRecord | None: ...
    async def get_by_reference(
        self,
        *,
        provider_connection_id: str,
        content_reference: str,
    ) -> StorageObjectRecord | None: ...
    async def upsert(self, data: dict[str, Any]) -> StorageObjectRecord: ...
    async def update(
        self,
        uid: str,
        changes: dict[str, Any],
    ) -> StorageObjectRecord: ...
    async def list(
        self,
        *,
        provider_connection_id: str,
        parent_ref: str | None = None,
        filter_by_parent: bool = False,
        include_deleted: bool = False,
    ) -> list[StorageObjectRecord]: ...
    async def soft_delete(self, uid: str) -> None: ...


class PluginGatewayProtocol(Protocol):
    """See `plugin_gateway.MediaPluginGateway` -- connection resolution,
    config decryption and socket plumbing all live behind this."""

    async def capabilities(
        self,
        provider_connection_id: str,
    ) -> tuple[str, ...]: ...
    async def list_resources(
        self,
        provider_connection_id: str,
        *,
        parent_id: str | None = None,
    ) -> list[PluginResource]: ...
    async def create_resource(
        self,
        provider_connection_id: str,
        metadata: CreateResourceIn,
        content: bytes,
    ) -> PluginResource: ...
    async def get_resource(
        self,
        provider_connection_id: str,
        content_reference: str,
    ) -> PluginResource: ...
    async def update_resource(
        self,
        provider_connection_id: str,
        content_reference: str,
        changes: UpdateResourceIn,
        content: bytes | None,
    ) -> PluginResource: ...
    async def delete_resource(
        self,
        provider_connection_id: str,
        content_reference: str,
    ) -> None: ...
    def read_content(
        self,
        provider_connection_id: str,
        content_reference: str,
        *,
        range_header: str | None,
    ) -> AsyncIterator[bytes]: ...


class ConnectionRepositoryProtocol(Protocol):
    """The slice of `ProviderConnection` this service reads: identity,
    provider type (local dump namespacing), and the two dual-layer flags."""

    async def get(self, uid: str) -> Any | None: ...  # noqa: ANN401
    async def list(self) -> list[Any]: ...


class InstanceSettingsRepositoryProtocol(Protocol):
    async def get(self) -> PlacementSettings: ...


class AccessKeyRecordProtocol(Protocol):
    """The slice of `UserAccessKeyRecord` signing/verifying reads."""

    uid: str
    user_id: str
    access_key_id: str


class AccessKeyServiceProtocol(Protocol):
    """See `apps.user_access_keys.services.UserAccessKeyService` -- key
    generation and secret decryption live there; this service only
    decides *when* to mint a temporary share link."""

    async def ensure_default_key(
        self,
        user_id: str,
    ) -> AccessKeyRecordProtocol: ...
    def decrypt_secret_str(self, key: AccessKeyRecordProtocol) -> str: ...
