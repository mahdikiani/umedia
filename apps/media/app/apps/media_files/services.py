"""MediaFile business rules -- the user-library half of the dual-layer
model (docs/11-dual-layer-library.md).

The invariants this module owns:

- **Listing is local.** `list_children` reads SQLite only; no code path
  from it may touch the plugin gateway.
- **Uploads write through.** plugin write -> verify -> upsert
  `StorageObject` -> create `MediaFile` + `primary` link; a failed plugin
  write leaves the MediaFile row terminal (`failed`), never `processing`.
- **Folders are library-only.** No plugin call, no StorageObject.
- **Mirror is opt-in and gated.** A library move/rename touching a linked
  file reaches the provider only when the connection has
  `mirror_structure` AND the plugin declares the `move` capability;
  otherwise structure lives only in UMedia. The provider call happens
  *before* the library write, so a mirror failure leaves the library
  unchanged.
- **Import is idempotent.** plugin list -> upsert StorageObjects ->
  MediaFiles under a connection-named library root; an object already
  linked is never imported twice. A complete walk marks unseen index
  rows `missing` and soft-deletes their MediaFiles; they restore to the
  same uid if the remote object reappears. A failed/partial listing
  never marks missing.
- **Deletes are two-step.** Soft-delete cascades through the folder tree.
  Permanent delete removes unshared provider objects before removing local
  library rows; shared objects remain until their last link is removed.
"""

import asyncio
import dataclasses
import hashlib
import logging
import time
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlparse

from apps.s3.auth import generate_presigned_url
from apps.s3.paths import file_keys
from apps.storage_objects.schemas import StorageObjectRecord
from plugins.contracts import CreateResourceIn, ResourceNotFoundError, UpdateResourceIn
from plugins.contracts import Resource as PluginResource
from utils.pagination import Page

from .content_type import index_content_type
from .errors import (
    MediaFileDeleteFailedError,
    MediaFileNotFoundError,
    MediaFilePermissionError,
    MediaFileStateError,
    MediaFileValidationError,
    MediaFileWriteFailedError,
)
from .legacy_staging import is_legacy_staging_folder
from .permissions import (
    PermissionEnum,
    can_open_content,
    can_read,
    effective_permission,
)
from .placement import PlacementSettings, pick_connection
from .provider_layout import (
    is_umedia_managed_reference,
    plugin_upload_parent,
)
from .schemas import (
    DIRECTORY_CONTENT_TYPE,
    HistoryEntry,
    MediaFileRecord,
)
from .search import MediaFileSearchMixin

PUBLIC_PERMISSIONS = {"none", "read"}

#: Bounds for a temporary presigned link's lifetime (seconds): at least a
#: minute, at most 7 days -- the API schema enforces the same window, the
#: service re-checks so no other caller can mint an eternal link.
TEMPORARY_LINK_MIN_SECONDS = 60
TEMPORARY_LINK_MAX_SECONDS = 7 * 24 * 60 * 60

#: `list_children` scopes -- the Resource layer's set plus `trash`
#: (the recycle bin: the actor's own soft-deleted roots).
LIST_SCOPES = {
    "owned",
    "shared_with_me",
    "shared_by_me",
    "all_visible",
    "starred",
    "trash",
}

#: How long soft-deleted items sit in the trash before the nightly
#: purge job removes them permanently.
TRASH_RETENTION_DAYS = 30

#: The manifest capability that means "this provider has structure worth
#: mirroring into" -- flat providers (e.g. Telegram) don't declare it.
MIRROR_CAPABILITY = "move"

#: "Not passed" sentinel for `update(parent_id=...)` -- `None` is a real
#: value there (move to the library root), so absence needs its own marker.
UNSET: Any = object()


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


async def _hash(content: bytes) -> str:
    """SHA-256 off the event loop -- same rationale as the Resource
    layer's `_hash`: this single shared loop serves everyone."""
    return await asyncio.to_thread(lambda: hashlib.sha256(content).hexdigest())


async def _hash_stream(chunks: AsyncIterator[bytes]) -> str:
    digest = hashlib.sha256()
    async for chunk in chunks:
        if chunk:
            await asyncio.to_thread(digest.update, chunk)
    return digest.hexdigest()


@dataclasses.dataclass(frozen=True)
class TemporaryLink:
    """A minted SigV4-presigned GET URL for the projected library path.
    `url` is path-absolute; `key_id` is the public id of the access key
    that signed it (never the secret)."""

    url: str
    key_id: str
    expires: int

    @property
    def expires_at(self) -> datetime:
        return datetime.fromtimestamp(self.expires, tz=UTC)


class MediaFileService(MediaFileSearchMixin):
    def __init__(
        self,
        files: MediaFileRepositoryProtocol,
        objects: StorageObjectRepositoryProtocol,
        plugins: PluginGatewayProtocol,
        connections: ConnectionRepositoryProtocol,
        *,
        access_keys: AccessKeyServiceProtocol | None = None,
        settings: "InstanceSettingsRepositoryProtocol | None" = None,
    ) -> None:
        self._files = files
        self._objects = objects
        self._plugins = plugins
        self._connections = connections
        #: Per-user access keys (`apps/user_access_keys`) -- temporary
        #: share links are SigV4-presigned with the minting user's secret.
        #: Optional so contexts that never mint (the trash purge worker)
        #: need not carry it.
        self._access_keys = access_keys
        self._settings = settings

    async def _bound_connection(self, folder_id: str) -> str | None:
        """Walk toward the library root until a folder names a connection."""
        cursor_id: str | None = folder_id
        seen: set[str] = set()
        while cursor_id and cursor_id not in seen:
            seen.add(cursor_id)
            record = await self._files.get(cursor_id)
            if record is None:
                return None
            if record.provider_connection_id:
                return record.provider_connection_id
            cursor_id = record.parent_id
        return None

    async def resolve_placement(
        self,
        *,
        parent_id: str | None,
        preferred_connection_id: str | None = None,
        actor_user_id: str,
        is_admin: bool = False,
    ) -> str:
        """The connection a new file/folder under `parent_id` should use.

        Only connections the actor owns (and may use -- `local` requires
        admin) are candidates. An explicit preferred id that is missing,
        unowned, or otherwise unusable is rejected rather than silently
        ignored.
        """
        from apps.provider_connections.services import connection_usable_by

        parent_connection_id = None
        if parent_id is not None:
            parent_connection_id = await self._bound_connection(parent_id)
        settings = (
            await self._settings.get()
            if self._settings is not None
            else PlacementSettings()
        )
        owned = [
            connection
            for connection in await self._connections.list(
                owner_id=actor_user_id,
            )
            if connection_usable_by(
                connection,
                actor_user_id=actor_user_id,
                is_admin=is_admin,
            )
        ]
        enabled = [
            connection.uid
            for connection in owned
            if getattr(connection, "enabled", True)
        ]
        if preferred_connection_id and parent_connection_id is None:
            preferred = await self._connections.get(preferred_connection_id)
            if preferred is None or not connection_usable_by(
                preferred,
                actor_user_id=actor_user_id,
                is_admin=is_admin,
            ):
                raise MediaFileValidationError(
                    f"Unknown provider_connection_id '{preferred_connection_id}'",
                )
            if not getattr(preferred, "enabled", True):
                raise MediaFileValidationError(
                    f"Provider connection '{preferred_connection_id}' is disabled",
                )
        if parent_connection_id is not None:
            parent_conn = await self._connections.get(parent_connection_id)
            if parent_conn is None or not connection_usable_by(
                parent_conn,
                actor_user_id=actor_user_id,
                is_admin=is_admin,
            ):
                raise MediaFileValidationError(
                    "This folder's storage is disabled or missing",
                )
            if parent_connection_id not in enabled:
                enabled.append(parent_connection_id)
        picked = pick_connection(
            settings=settings,
            enabled_ids=enabled,
            parent_connection_id=parent_connection_id,
            preferred_connection_id=(
                None if parent_connection_id else preferred_connection_id
            ),
        )
        if parent_id is not None and parent_connection_id is None:
            await self._files.update(
                parent_id, {"provider_connection_id": picked},
            )
        return picked

    async def _provider_type(self, provider_connection_id: str) -> str | None:
        connection = await self._connections.get(provider_connection_id)
        if connection is None:
            return None
        return getattr(connection, "provider_type", None)

    async def _with_starred(
        self,
        records: list[MediaFileRecord],
        *,
        actor_user_id: str,
    ) -> "list[MediaFileRecord]":
        starred_ids = await self._files.starred_ids(
            actor_user_id=actor_user_id,
            media_file_uids=[record.uid for record in records],
        )
        return [
            dataclasses.replace(record, starred=record.uid in starred_ids)
            for record in records
        ]

    # ------------------------------------------------------------------
    # Access control primitives (ported from ResourceService)
    # ------------------------------------------------------------------

    async def _is_under_legacy_staging(self, record: MediaFileRecord) -> bool:
        """True when `record` is the old Temporary folder or lives under it."""
        current: MediaFileRecord | None = record
        while current is not None:
            if is_legacy_staging_folder(current):
                return True
            if current.parent_id is None:
                return False
            current = await self._files.get(current.parent_id)
        return False

    async def _get_visible(
        self,
        uid: str,
        *,
        actor_user_id: str,
        include_deleted: bool = False,
    ) -> MediaFileRecord:
        """The record, if the actor may READ it -- `MediaFileNotFoundError`
        otherwise (never 403 here: a caller who can't see a file must not
        learn that its uid exists)."""
        record = await self._files.get(uid)
        if record is None or (record.is_deleted and not include_deleted):
            raise MediaFileNotFoundError(uid)
        if await self._is_under_legacy_staging(record):
            raise MediaFileNotFoundError(uid)
        if not can_read(record, actor_user_id):
            raise MediaFileNotFoundError(uid)
        return (
            await self._with_starred(
                [record],
                actor_user_id=actor_user_id,
            )
        )[0]

    @staticmethod
    def _require(
        record: MediaFileRecord,
        actor_user_id: str,
        level: PermissionEnum,
        operation: str,
    ) -> None:
        if effective_permission(record, actor_user_id) < level:
            raise MediaFilePermissionError(
                f"{level.name} permission is required to {operation} "
                f"file '{record.uid}'",
            )

    async def _writable_folder(
        self,
        parent_id: str,
        *,
        actor_user_id: str,
    ) -> MediaFileRecord:
        """Resolve a library parent: it must exist, be a folder, and be
        writable by the actor. Purely a library check -- library folders
        are provider-agnostic, unlike the Resource era's plugin-parent
        resolution."""
        parent = await self._files.get(parent_id)
        if parent is None or parent.is_deleted:
            raise MediaFileValidationError(f"Parent folder '{parent_id}' not found")
        if not can_read(parent, actor_user_id):
            # Same "not found" a nonexistent parent gets.
            raise MediaFileValidationError(f"Parent folder '{parent_id}' not found")
        if parent.type != "folder":
            raise MediaFileValidationError(
                f"Parent '{parent_id}' is not a folder",
            )
        if effective_permission(parent, actor_user_id) < PermissionEnum.WRITE:
            raise MediaFilePermissionError(
                f"WRITE permission is required to add files to '{parent_id}'",
            )
        if is_legacy_staging_folder(parent):
            raise MediaFileValidationError(
                "Temporary is a pointer clipboard, not a library folder",
            )
        return parent

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(self, uid: str, *, actor_user_id: str) -> MediaFileRecord:
        return await self._get_visible(uid, actor_user_id=actor_user_id)

    async def get_via_public_link(
        self,
        uid: str,
        *,
        actor_user_id: str | None,
        caller_workspace_ids: list[str] | None = None,
    ) -> MediaFileRecord:
        """Content-URL open gate for `/f/{uid}` and `/files/{uid}/content`.

        Allows owner / ACL share / workspace membership / permanent public.
        Short-lived links are SigV4 on `/s3/...`, not this path. Deny is
        always `MediaFileNotFoundError` (404) -- never confirm the uid.
        """
        record = await self._files.get(uid)
        if record is None or record.is_deleted:
            raise MediaFileNotFoundError(uid)
        if can_open_content(
            record,
            actor_user_id,
            caller_workspace_ids=caller_workspace_ids,
        ):
            return record
        raise MediaFileNotFoundError(uid)

    async def list_children(
        self,
        parent_id: str | None,
        *,
        actor_user_id: str,
        scope: str = "owned",
        include_deleted: bool = False,
        sort: str = "name",
        order: str = "asc",
        limit: int = 50,
        offset: int = 0,
    ) -> Page[MediaFileRecord]:
        """`GET /files?parent_id=` -- SQLite only, never provider I/O.

        `scope=trash` is the flat recycle bin (the actor's own deleted
        roots, newest deletion first); `parent_id` is ignored there."""
        if scope not in LIST_SCOPES:
            raise MediaFileValidationError(
                f"Invalid scope '{scope}', must be one of {sorted(LIST_SCOPES)}",
            )
        if include_deleted and scope != "owned":
            # Trash browsing is owner-only.
            raise MediaFileValidationError(
                "include_deleted is only valid with scope 'owned'",
            )
        if parent_id is not None:
            parent = await self._files.get(parent_id)
            if parent is not None and is_legacy_staging_folder(parent):
                return Page.build([], total=0, limit=limit, offset=offset)
        page = await self._files.list_for_actor(
            actor_user_id=actor_user_id,
            parent_id=parent_id,
            scope=scope,
            include_deleted=include_deleted,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset,
        )
        return dataclasses.replace(
            page,
            items=await self._with_starred(
                page.items,
                actor_user_id=actor_user_id,
            ),
        )

    async def search(
        self,
        query: str,
        *,
        actor_user_id: str,
        under_parent_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[MediaFileRecord]:
        page = await super().search(
            query,
            actor_user_id=actor_user_id,
            under_parent_id=under_parent_id,
            limit=limit,
            offset=offset,
        )
        return dataclasses.replace(
            page,
            items=await self._with_starred(
                page.items,
                actor_user_id=actor_user_id,
            ),
        )

    async def volume_stats(self, *, actor_user_id: str) -> dict[str, int]:
        """Library usage for the actor -- SQLite only, no provider I/O."""
        return await self._files.volume_stats(actor_user_id=actor_user_id)

    async def read_content(
        self,
        uid: str,
        *,
        actor_user_id: str,
        range_header: str | None = None,
    ) -> tuple[MediaFileRecord, AsyncIterator[bytes]]:
        record = await self.get(uid, actor_user_id=actor_user_id)
        return await self._stream(record, range_header=range_header)

    async def read_content_via_public_link(
        self,
        uid: str,
        *,
        actor_user_id: str | None,
        range_header: str | None = None,
    ) -> tuple[MediaFileRecord, AsyncIterator[bytes]]:
        record = await self.get_via_public_link(
            uid,
            actor_user_id=actor_user_id,
        )
        return await self._stream(record, range_header=range_header)

    async def _stream(
        self,
        record: MediaFileRecord,
        *,
        range_header: str | None,
    ) -> tuple[MediaFileRecord, AsyncIterator[bytes]]:
        """MediaFile -> primary StorageObject -> plugin stream."""
        if record.content_reference is None or record.provider_connection_id is None:
            raise MediaFileNotFoundError(record.uid)
        stream = self._plugins.read_content(
            record.provider_connection_id,
            record.content_reference,
            range_header=range_header,
        )
        await self._files.touch_access(record.uid)
        return record, stream

    # ------------------------------------------------------------------
    # Sharing (ported from ResourceService)
    # ------------------------------------------------------------------

    async def set_public_permission(
        self,
        uid: str,
        value: str,
        *,
        actor_user_id: str,
    ) -> MediaFileRecord:
        if value not in PUBLIC_PERMISSIONS:
            raise MediaFileValidationError(
                f"Invalid public_permission '{value}', must be one of "
                f"{sorted(PUBLIC_PERMISSIONS)}",
            )
        record = await self.get(uid, actor_user_id=actor_user_id)
        self._require(
            record,
            actor_user_id,
            PermissionEnum.MANAGE,
            "change sharing of",
        )
        return await self._files.update(uid, {"public_permission": value})

    async def create_temporary_link(
        self,
        uid: str,
        *,
        actor_user_id: str,
        expires_in: int,
        base_url: str,
    ) -> TemporaryLink:
        """Mint a SigV4-presigned GET for the projected S3 library path,
        signed with the *actor's* default access key (created on the spot for
        accounts that predate access keys). Same MANAGE gate as
        `set_public_permission`. Deactivating that key revokes every URL
        it signed. A later rename changes future mints and invalidates the
        old projected path."""
        if self._access_keys is None:
            raise MediaFileStateError(
                "Temporary links are not configured (no access-key service)",
            )
        if not (TEMPORARY_LINK_MIN_SECONDS <= expires_in <= TEMPORARY_LINK_MAX_SECONDS):
            raise MediaFileValidationError(
                f"expires_in must be between {TEMPORARY_LINK_MIN_SECONDS} and "
                f"{TEMPORARY_LINK_MAX_SECONDS} seconds",
            )
        record = await self.get(uid, actor_user_id=actor_user_id)
        self._require(
            record,
            actor_user_id,
            PermissionEnum.MANAGE,
            "change sharing of",
        )
        key = await self._access_keys.ensure_default_key(actor_user_id)
        secret = self._access_keys.decrypt_secret_str(key)
        expires = int(time.time()) + expires_in
        full_url = generate_presigned_url(
            method="GET",
            key=file_keys(
                await self._files.list_visible(actor_user_id=actor_user_id),
            )[uid],
            expires_in=expires_in,
            base_url=base_url,
            access_key=key.access_key_id,
            secret_key=secret,
        )
        parsed = urlparse(full_url)
        return TemporaryLink(
            url=f"{parsed.path}?{parsed.query}",
            key_id=key.access_key_id,
            expires=expires,
        )

    async def set_starred(
        self,
        uid: str,
        *,
        actor_user_id: str,
        starred: bool,
    ) -> MediaFileRecord:
        record = await self.get(uid, actor_user_id=actor_user_id)
        await self._files.set_starred(
            actor_user_id=actor_user_id,
            media_file_uid=uid,
            starred=starred,
        )
        return dataclasses.replace(record, starred=starred)

    async def add_temporary_items(
        self,
        ids: list[str],
        *,
        actor_user_id: str,
    ) -> int:
        unique_ids = list(dict.fromkeys(ids))
        for media_file_uid in unique_ids:
            await self._get_visible(
                media_file_uid,
                actor_user_id=actor_user_id,
            )

        added = 0
        for media_file_uid in unique_ids:
            was_added = await self._files.add_temporary_item(
                actor_user_id=actor_user_id,
                media_file_uid=media_file_uid,
            )
            if was_added:
                added += 1
        return added

    async def list_temporary_items(
        self,
        *,
        actor_user_id: str,
    ) -> list[MediaFileRecord]:
        records = await self._files.list_temporary_items(
            actor_user_id=actor_user_id,
        )
        visible = [
            record
            for record in records
            if can_read(record, actor_user_id)
        ]
        return await self._with_starred(
            visible,
            actor_user_id=actor_user_id,
        )

    async def remove_temporary_item(
        self,
        media_file_id: str,
        *,
        actor_user_id: str,
    ) -> None:
        await self._files.remove_temporary_item(
            actor_user_id=actor_user_id,
            media_file_uid=media_file_id,
        )

    async def clear_temporary_items(self, *, actor_user_id: str) -> None:
        await self._files.clear_temporary_items(
            actor_user_id=actor_user_id,
        )

    async def set_user_permission(
        self,
        uid: str,
        *,
        actor_user_id: str,
        target_user_id: str,
        permission: int,
    ) -> MediaFileRecord:
        """Grant/replace `target_user_id`'s level on `uid`; `0` (NONE)
        removes the entry entirely."""
        record = await self.get(uid, actor_user_id=actor_user_id)
        self._require(
            record,
            actor_user_id,
            PermissionEnum.MANAGE,
            "change sharing of",
        )
        try:
            level = PermissionEnum(int(permission))
        except ValueError:
            raise MediaFileValidationError(
                f"Invalid permission {permission!r}, must be one of "
                f"{[int(member) for member in PermissionEnum]}",
            ) from None
        if target_user_id == record.owner_id:
            raise MediaFileValidationError(
                "The owner already holds full access; their permission "
                "cannot be granted or revoked",
            )
        entries = [
            entry
            for entry in record.permissions
            if entry.get("user_id") != target_user_id
        ]
        if level != PermissionEnum.NONE:
            entries.append({"user_id": target_user_id, "permission": int(level)})
        return await self._files.update(uid, {"permissions": entries})

    async def list_permissions(
        self,
        uid: str,
        *,
        actor_user_id: str,
    ) -> list[dict[str, Any]]:
        record = await self.get(uid, actor_user_id=actor_user_id)
        self._require(
            record,
            actor_user_id,
            PermissionEnum.MANAGE,
            "view sharing of",
        )
        return record.permissions

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def create_folder(
        self,
        *,
        name: str,
        parent_id: str | None,
        owner_id: str,
        is_admin: bool = False,
    ) -> MediaFileRecord:
        """A library folder: MediaFile only -- no plugin call, no
        StorageObject (docs/11-dual-layer-library.md)."""
        if parent_id is not None:
            await self._writable_folder(parent_id, actor_user_id=owner_id)
        connection_id = await self.resolve_placement(
            parent_id=parent_id,
            actor_user_id=owner_id,
            is_admin=is_admin,
        )
        return await self._files.create({
            "owner_id": owner_id,
            "type": "folder",
            "name": name,
            "parent_id": parent_id,
            "provider_connection_id": connection_id,
            "metadata": {},
            "status": "completed",
            "error": None,
            "public_permission": "none",
            "permissions": [],
            "workspace_id": None,
        })

    async def copy_file(
        self,
        source: MediaFileRecord,
        *,
        dest_parent_id: str | None,
        name: str,
        actor_user_id: str,
        byte_copy: bool,
        is_admin: bool = False,
    ) -> MediaFileRecord:
        """Copy one file into `dest_parent_id`.

        Same-storage (`byte_copy=False`): new MediaFile sharing the source
        StorageObject. Cross-storage: stream bytes and upload onto the
        destination connection.
        """
        # Re-load through ACL so callers cannot pass a foreign record.
        source = await self.get(source.uid, actor_user_id=actor_user_id)
        self._require(source, actor_user_id, PermissionEnum.READ, "copy")
        if source.type != "file":
            raise MediaFileValidationError(
                f"File '{source.uid}' is not content-bearing",
            )
        if dest_parent_id is not None:
            await self._writable_folder(
                dest_parent_id, actor_user_id=actor_user_id,
            )

        if byte_copy:
            if (
                source.provider_connection_id is None
                or source.content_reference is None
            ):
                raise MediaFileValidationError(
                    f"File '{source.uid}' has no linked storage object",
                )
            chunks: list[bytes] = [chunk async for chunk in self._plugins.read_content(
                source.provider_connection_id,
                source.content_reference,
                range_header=None,
            ) if chunk]
            dest_connection_id = None
            if dest_parent_id is not None:
                dest_connection_id = await self._bound_connection(dest_parent_id)
            return await self.upload(
                provider_connection_id=dest_connection_id,
                parent_id=dest_parent_id,
                name=name,
                content=b"".join(chunks),
                content_type=source.content_type,
                owner_id=actor_user_id,
                is_admin=is_admin,
            )

        if source.storage_object_uid is None:
            raise MediaFileValidationError(
                f"File '{source.uid}' has no linked storage object",
            )
        record = await self._files.create({
            "owner_id": actor_user_id,
            "type": "file",
            "name": name,
            "parent_id": dest_parent_id,
            "provider_connection_id": source.provider_connection_id,
            "metadata": {},
            "status": "processing",
            "error": None,
            "public_permission": "none",
            "permissions": [],
            "workspace_id": None,
        })
        await self._files.link_object(
            media_file_uid=record.uid,
            storage_object_uid=source.storage_object_uid,
        )
        return await self._files.update(record.uid, {"status": "completed"})

    async def upload(
        self,
        *,
        provider_connection_id: str | None = None,
        parent_id: str | None,
        name: str,
        content: bytes,
        content_type: str | None = None,
        owner_id: str,
        is_admin: bool = False,
    ) -> MediaFileRecord:
        """Upload: plugin write -> verify -> upsert StorageObject ->
        MediaFile + `primary` link.

        The provider write happens at the provider's root regardless of
        the library `parent_id`, except on `local`: that connection is
        shared, so bytes go under
        `.umedia/users/{owner_id}/{media_file_uid}/` (docs/12-file-storage.md).
        A mirrored connection reflects later *moves*, not the initial
        placement (`update()` below).
        """
        if parent_id is not None:
            await self._writable_folder(parent_id, actor_user_id=owner_id)
        provider_connection_id = await self.resolve_placement(
            parent_id=parent_id,
            preferred_connection_id=provider_connection_id,
            actor_user_id=owner_id,
            is_admin=is_admin,
        )

        record = await self._files.create({
            "owner_id": owner_id,
            "type": "file",
            "name": name,
            "parent_id": parent_id,
            "provider_connection_id": provider_connection_id,
            "metadata": {},
            "status": "processing",
            "error": None,
            "public_permission": "none",
            "permissions": [],
            "workspace_id": None,
        })

        try:
            plugin_parent = plugin_upload_parent(
                provider_type=await self._provider_type(provider_connection_id),
                owner_id=owner_id,
                media_file_uid=record.uid,
            )
        except ValueError as error:
            await self._files.update(
                record.uid,
                {"status": "failed", "error": str(error)},
            )
            raise MediaFileValidationError(str(error)) from error

        try:
            plugin_resource = await self._plugins.create_resource(
                provider_connection_id,
                CreateResourceIn(
                    name=name, type="file", parent_id=plugin_parent,
                ),
                content,
            )
            await self._verify(provider_connection_id, plugin_resource)
        except Exception as error:
            await self._files.update(
                record.uid,
                {"status": "failed", "error": str(error)},
            )
            raise MediaFileWriteFailedError(str(error)) from error

        obj = await self._objects.upsert({
            "provider_connection_id": provider_connection_id,
            "content_reference": plugin_resource.id,
            "provider_parent_ref": plugin_resource.parent_id,
            "type": plugin_resource.type or "file",
            "name": plugin_resource.name or name,
            "content_hash": await _hash(content),
            "content_type": index_content_type(
                name,
                plugin_resource.content_type,
                content_type,
            ),
            "size": plugin_resource.size
            if plugin_resource.size is not None
            else len(content),
            "metadata": dict(plugin_resource.metadata or {}),
            "status": "active",
        })

        existing = await self._files.get_by_storage_object(obj.uid)
        if existing is not None and existing.uid != record.uid:
            # The provider reported the same physical object an earlier
            # upload already linked (same dump path, or an opaque id).
            # One object, one link (v1): drop the placeholder row.
            await self._files.hard_delete(record.uid)
            if existing.owner_id == owner_id and not existing.is_deleted:
                return existing
            raise MediaFileWriteFailedError(
                "The written object is already linked to another file",
            )

        await self._files.link_object(
            media_file_uid=record.uid,
            storage_object_uid=obj.uid,
        )
        return await self._files.update(record.uid, {"status": "completed"})

    async def replace_content(
        self,
        uid: str,
        *,
        content: bytes,
        content_type: str | None = None,
        actor_user_id: str,
    ) -> MediaFileRecord:
        current = await self.get(uid, actor_user_id=actor_user_id)
        self._require(current, actor_user_id, PermissionEnum.WRITE, "update")
        if current.type != "file":
            raise MediaFileValidationError(f"File '{uid}' is not content-bearing")
        if (
            current.storage_object_uid is None
            or current.provider_connection_id is None
            or current.content_reference is None
        ):
            raise MediaFileValidationError(
                f"File '{uid}' has no linked storage object",
            )
        storage_object = await self._objects.get(current.storage_object_uid)
        if storage_object is None:
            raise MediaFileValidationError(
                f"File '{uid}' has no linked storage object",
            )
        await self._files.update(uid, {"status": "processing", "error": None})
        try:
            plugin_resource = await self._plugins.update_resource(
                current.provider_connection_id,
                current.content_reference,
                UpdateResourceIn(overwrite_content=True),
                content,
            )
            await self._verify(current.provider_connection_id, plugin_resource)
        except Exception as error:
            await self._files.update(
                uid,
                {"status": "failed", "error": str(error)},
            )
            raise MediaFileWriteFailedError(str(error)) from error

        await self._objects.update(
            storage_object.uid,
            {
                "content_reference": plugin_resource.id,
                "content_hash": await _hash(content),
                "content_type": index_content_type(
                    current.name,
                    plugin_resource.content_type,
                    content_type,
                    storage_object.content_type,
                ),
                "size": (
                    plugin_resource.size
                    if plugin_resource.size is not None
                    else len(content)
                ),
                "status": "active",
            },
        )
        history = [
            *current.history,
            HistoryEntry(
                storage_object_uid=storage_object.uid,
                content_hash=storage_object.content_hash,
                content_type=storage_object.content_type,
                size=storage_object.size,
            ),
        ]
        return await self._files.update(
            uid,
            {"history": history, "status": "completed", "error": None},
        )

    async def _verify(
        self,
        provider_connection_id: str,
        plugin_resource: PluginResource,
    ) -> None:
        """Confirm a write actually landed -- a 200 from the plugin isn't
        sufficient proof by itself (docs/02-architecture.md)."""
        verified = await self._plugins.get_resource(
            provider_connection_id,
            plugin_resource.id,
        )
        if verified.id != plugin_resource.id:
            raise MediaFileWriteFailedError(
                "post-write verification returned a different resource",
            )

    async def update(
        self,
        uid: str,
        *,
        actor_user_id: str,
        name: str | None = None,
        parent_id: Any = UNSET,  # noqa: ANN401 -- see UNSET
    ) -> MediaFileRecord:
        """Rename and/or move within the library -- requires WRITE.

        Mirror rule (docs/11-dual-layer-library.md): if the file has a
        linked StorageObject, the connection has `mirror_structure`, and
        the plugin declares `move`, the change is reflected on the
        provider *first* -- a provider failure leaves the library
        untouched. A move is mirrorable only when the destination folder
        is itself provider-backed on the same connection (e.g. an
        imported folder); pure library folders have no provider
        counterpart, so such moves are library-only by definition.
        """
        current = await self.get(uid, actor_user_id=actor_user_id)
        self._require(current, actor_user_id, PermissionEnum.WRITE, "update")

        renaming = name is not None and name != current.name
        moving = parent_id is not UNSET and parent_id != current.parent_id
        new_parent: MediaFileRecord | None = None
        if moving and parent_id is not None:
            new_parent = await self._writable_folder(
                parent_id,
                actor_user_id=actor_user_id,
            )
            new_parent = await self._bind_move_to_storage(current, new_parent)
        if not renaming and not moving:
            return current

        await self._mirror_structure_change(
            current,
            new_name=name if renaming else None,
            new_parent=new_parent,
            moving=moving,
        )

        changes: dict[str, Any] = {}
        if renaming:
            changes["name"] = name
        if moving:
            changes["parent_id"] = parent_id
        return await self._files.update(uid, changes)

    async def _bind_move_to_storage(
        self,
        current: MediaFileRecord,
        new_parent: MediaFileRecord,
    ) -> MediaFileRecord:
        """Keep a folder (and its files) on one storage. Moving into a
        folder of a different connection is a copy, not a nest.

        """
        dest_conn = await self._bound_connection(new_parent.uid)
        item_conn = current.provider_connection_id
        if not item_conn and current.type == "folder":
            item_conn = await self._bound_connection(current.uid)
        if dest_conn and item_conn and dest_conn != item_conn:
            raise MediaFileValidationError(
                "Cannot move items into a folder on a different storage",
            )
        if item_conn and not dest_conn:
            return await self._files.update(
                new_parent.uid, {"provider_connection_id": item_conn},
            )
        return new_parent

    async def _mirror_structure_change(
        self,
        current: MediaFileRecord,
        *,
        new_name: str | None,
        new_parent: MediaFileRecord | None,
        moving: bool,
    ) -> None:
        """Reflect a library rename/move on the provider when (and only
        when) the mirror gate passes -- see `update()`'s docstring."""
        if current.storage_object_uid is None or (
            current.provider_connection_id is None
        ):
            return  # nothing linked, nothing to mirror
        connection = await self._connections.get(current.provider_connection_id)
        if connection is None or not getattr(connection, "mirror_structure", False):
            return
        capabilities = await self._plugins.capabilities(
            current.provider_connection_id,
        )
        if MIRROR_CAPABILITY not in capabilities:
            return  # flat provider: structure lives only in UMedia

        plugin_parent_ref: str | None = None
        if (
            moving
            and new_parent is not None
            and new_parent.provider_connection_id == current.provider_connection_id
            and new_parent.content_reference is not None
        ):
            plugin_parent_ref = new_parent.content_reference

        if new_name is None and plugin_parent_ref is None:
            return  # nothing the plugin contract can express (e.g. move to root)

        try:
            result = await self._plugins.update_resource(
                current.provider_connection_id,
                current.content_reference,
                UpdateResourceIn(name=new_name, parent_id=plugin_parent_ref),
                None,
            )
            await self._verify(current.provider_connection_id, result)
        except MediaFileWriteFailedError:
            raise
        except Exception as error:
            raise MediaFileWriteFailedError(str(error)) from error

        await self._objects.update(
            current.storage_object_uid,
            {
                # A path-addressed provider changes the object's identity on
                # move/rename -- keep the index pointing at the new one.
                "content_reference": result.id,
                "provider_parent_ref": result.parent_id,
                "name": result.name,
            },
        )

    async def link_object(
        self,
        uid: str,
        storage_object_uid: str,
        *,
        actor_user_id: str,
    ) -> MediaFileRecord:
        """`POST /files/{id}/link`: attach an indexed-but-unlinked
        StorageObject as this file's `primary` content."""
        record = await self.get(uid, actor_user_id=actor_user_id)
        self._require(record, actor_user_id, PermissionEnum.WRITE, "link")
        if record.type == "folder":
            raise MediaFileValidationError("Cannot link content to a folder")
        if record.storage_object_uid is not None:
            raise MediaFileStateError(
                f"File '{uid}' already has a primary storage object",
            )
        obj = await self._objects.get(storage_object_uid)
        if obj is None or obj.is_deleted:
            raise MediaFileValidationError(
                f"Storage object '{storage_object_uid}' not found",
            )
        already = await self._files.get_by_storage_object(storage_object_uid)
        if already is not None:
            raise MediaFileStateError(
                f"Storage object '{storage_object_uid}' is already linked",
            )
        await self._files.link_object(
            media_file_uid=uid,
            storage_object_uid=storage_object_uid,
        )
        return await self._files.update(uid, {"status": "completed"})

    # ------------------------------------------------------------------
    # Import / sync (docs/11-dual-layer-library.md "Sync")
    # ------------------------------------------------------------------

    async def _resolve_content_hash(
        self,
        provider_connection_id: str,
        resource: PluginResource,
        previous: StorageObjectRecord | None,
        *,
        remote_newer: bool,
    ) -> str | None:
        if resource.type == "folder":
            return None

        remote_size = resource.size or 0
        if (
            previous is not None
            and previous.content_hash
            and not remote_newer
            and remote_size == previous.size
        ):
            remote_mtime = (resource.metadata or {}).get("mtime")
            previous_mtime = (previous.metadata or {}).get("mtime")
            have_mtimes = isinstance(remote_mtime, (int, float)) and isinstance(
                previous_mtime,
                (int, float),
            )
            if not have_mtimes or remote_mtime == previous_mtime:
                return previous.content_hash

        return None

    async def hash_missing_content(
        self,
        provider_connection_id: str,
        *,
        batch_size: int = 100,
    ) -> int:
        """Hash indexed files in bounded pages without delaying provider sync."""
        completed = 0
        after_uid: str | None = None
        while pending := await self._objects.list_missing_hashes(
            provider_connection_id=provider_connection_id,
            after_uid=after_uid,
            limit=batch_size,
        ):
            after_uid = pending[-1].uid
            for item in pending:
                try:
                    content_hash = await _hash_stream(
                        self._plugins.read_content(
                            provider_connection_id,
                            item.content_reference,
                            range_header=None,
                        ),
                    )
                except Exception:
                    logging.exception(
                        "Content hash failed for %s on connection %s",
                        item.content_reference,
                        provider_connection_id,
                    )
                    continue
                stored = await self._objects.set_content_hash_if_unchanged(
                    uid=item.uid,
                    expected_size=item.size,
                    expected_metadata=item.metadata,
                    content_hash=content_hash,
                )
                completed += int(stored)
        return completed

    async def import_if_enabled(
        self,
        provider_connection_id: str,
        *,
        actor_user_id: str,
    ) -> dict[str, int] | None:
        """The connect-time entry point: runs the import only when the
        connection asked for it (`import_existing`). `POST
        /providers/{uid}/sync` calls `import_from_provider` directly --
        an explicit sync always runs."""
        connection = await self._connections.get(provider_connection_id)
        if connection is None or not getattr(connection, "import_existing", False):
            return None
        return await self.import_from_provider(
            provider_connection_id,
            actor_user_id=actor_user_id,
        )

    async def import_from_provider(
        self,
        provider_connection_id: str,
        *,
        actor_user_id: str,
    ) -> dict[str, int]:
        """Walk the provider, reconcile each object, upsert the index.

        Per-file policy (explicit Sync):
        - Remote newer (mtime/size): keep prior indexed bytes snapshot on
          the linked MediaFile `history` (tmp), then adopt remote metadata.
        - Ours newer (indexed mtime ahead of remote): push our content to
          the provider when `mirror_structure` is on, then refresh the index.
        - New remote object: import MediaFile under the connection root.
        - Complete walk only: StorageObjects not observed this pass become
          `status=missing` and their linked MediaFiles are soft-deleted.
          A raised listing (or any walk error) skips that missing-mark so
          a partial scan cannot look like mass deletion.
        - Reappear of a previously-missing object restores the same
          MediaFile uid; user trash while the object was still `active`
          is left in trash.
        """
        connection = await self._connections.get(provider_connection_id)
        if connection is None:
            raise MediaFileValidationError(
                f"Provider connection '{provider_connection_id}' not found",
            )
        mirror = bool(getattr(connection, "mirror_structure", False))
        root = await self._ensure_import_root(
            connection, actor_user_id=actor_user_id,
        )

        imported = 0
        updated = 0
        pushed = 0
        seen = 0
        restored = 0
        observed: set[str] = set()
        visited: set[str | None] = set()
        stack: list[tuple[str | None, str]] = [(None, root.uid)]
        while stack:
            provider_ref, library_parent_uid = stack.pop()
            if provider_ref in visited:
                continue
            visited.add(provider_ref)
            resources = await self._plugins.list_resources(
                provider_connection_id,
                parent_id=provider_ref,
            )
            for resource in resources:
                seen += 1
                observed.add(resource.id)
                if is_umedia_managed_reference(resource.id):
                    # Core-owned local dump (per-user upload paths). Keep
                    # walking so descendants are observed, but never
                    # import them as library files.
                    if resource.type == "folder":
                        stack.append((resource.id, library_parent_uid))
                    continue
                media_file, deltas = await self._sync_listed_resource(
                    provider_connection_id,
                    resource,
                    actor_user_id=actor_user_id,
                    library_parent_uid=library_parent_uid,
                    mirror=mirror,
                )
                imported += deltas["imported"]
                updated += deltas["updated"]
                pushed += deltas["pushed"]
                restored += deltas["restored"]
                if resource.type == "folder":
                    stack.append((resource.id, media_file.uid))

        missing = await self._mark_unseen_missing(
            provider_connection_id,
            observed,
        )
        return {
            "imported": imported,
            "updated": updated,
            "pushed": pushed,
            "seen": seen,
            "missing": missing,
            "restored": restored,
        }

    async def _ensure_import_root(
        self,
        connection: Any,  # noqa: ANN401 -- protocol get() is untyped
        *,
        actor_user_id: str,
    ) -> MediaFileRecord:
        """Reuse the connection's library root regardless of which admin
        is the actor, so polling cannot fork a second folder."""
        root = await self._files.find_import_root(
            provider_connection_id=connection.uid,
            owner_id=actor_user_id,
        )
        if root is None:
            root = await self._files.find_import_root(
                provider_connection_id=connection.uid,
            )
        if root is None:
            root = await self._files.create({
                "owner_id": actor_user_id,
                "type": "folder",
                "name": connection.name,
                "parent_id": None,
                "provider_connection_id": connection.uid,
                "metadata": {"import_root": connection.uid},
                "status": "completed",
                "error": None,
                "public_permission": "none",
                "permissions": [],
                "workspace_id": None,
            })
        elif root.provider_connection_id is None:
            root = await self._files.update(
                root.uid,
                {"provider_connection_id": connection.uid},
            )
        return root

    async def _sync_listed_resource(
        self,
        provider_connection_id: str,
        resource: PluginResource,
        *,
        actor_user_id: str,
        library_parent_uid: str,
        mirror: bool,
    ) -> tuple[MediaFileRecord, dict[str, int]]:
        """Upsert one listed object and create/restore its MediaFile.

        Counter deltas are 0/1 for imported/updated/pushed/restored.
        """
        deltas = {"imported": 0, "updated": 0, "pushed": 0, "restored": 0}
        is_folder = resource.type == "folder"
        remote_meta = dict(resource.metadata or {})
        remote_size = resource.size or 0

        previous = await self._objects.get_by_reference(
            provider_connection_id=provider_connection_id,
            content_reference=resource.id,
        )
        was_missing = previous is not None and previous.status == "missing"
        remote_newer = await self._apply_freshness_policy(
            provider_connection_id,
            resource,
            previous,
            mirror=mirror,
            deltas=deltas,
        )
        content_hash = await self._resolve_content_hash(
            provider_connection_id,
            resource,
            previous,
            remote_newer=remote_newer,
        )
        obj = await self._objects.upsert({
            "provider_connection_id": provider_connection_id,
            "content_reference": resource.id,
            "provider_parent_ref": resource.parent_id,
            "type": resource.type,
            "name": resource.name,
            "content_hash": content_hash,
            "content_type": (
                DIRECTORY_CONTENT_TYPE
                if is_folder
                else index_content_type(resource.name, resource.content_type)
            ),
            "size": remote_size,
            "metadata": remote_meta,
            "status": "active",
        })
        existing = await self._files.get_by_storage_object(obj.uid)
        if existing is None:
            media_file = await self._files.create({
                "owner_id": actor_user_id,
                "type": resource.type,
                "name": resource.name,
                "parent_id": library_parent_uid,
                "provider_connection_id": provider_connection_id,
                "metadata": {},
                "status": "completed",
                "error": None,
                "public_permission": "none",
                "permissions": [],
                "workspace_id": None,
            })
            await self._files.link_object(
                media_file_uid=media_file.uid,
                storage_object_uid=obj.uid,
            )
            deltas["imported"] = 1
            return media_file, deltas
        if existing.is_deleted and was_missing:
            await self._restore_tree(existing)
            deltas["restored"] = 1
        return existing, deltas

    async def _apply_freshness_policy(
        self,
        provider_connection_id: str,
        resource: PluginResource,
        previous: StorageObjectRecord | None,
        *,
        mirror: bool,
        deltas: dict[str, int],
    ) -> bool:
        """Remote-newer snapshots history; ours-newer with mirror pushes.

        Returns whether the remote side won this pass.
        """
        if previous is None or resource.type == "folder":
            return False
        remote_mtime = (resource.metadata or {}).get("mtime")
        remote_size = resource.size or 0
        prev_mtime = (previous.metadata or {}).get("mtime")
        have_mtimes = isinstance(remote_mtime, (int, float)) and isinstance(
            prev_mtime, (int, float)
        )
        remote_newer = False
        ours_newer = False
        if have_mtimes:
            if remote_mtime > prev_mtime:
                remote_newer = True
            elif prev_mtime > remote_mtime:
                ours_newer = True
            elif remote_size != previous.size:
                remote_newer = True
        elif remote_size != previous.size:
            remote_newer = True

        linked = await self._files.get_by_storage_object(previous.uid)
        if remote_newer and linked is not None:
            history = [
                *linked.history,
                HistoryEntry(
                    storage_object_uid=previous.uid,
                    content_hash=previous.content_hash,
                    content_type=previous.content_type,
                    size=previous.size,
                ),
            ]
            await self._files.update(linked.uid, {"history": history})
            deltas["updated"] = 1
        elif ours_newer and mirror and linked is not None:
            await self._push_indexed_content(provider_connection_id, previous)
            deltas["pushed"] = 1
        return remote_newer

    async def _mark_unseen_missing(
        self,
        provider_connection_id: str,
        observed: set[str],
    ) -> int:
        """After a complete walk: index rows not seen this pass become
        `missing` (still listed in Storage browse) and their linked
        library files go to trash. Already-missing rows are left alone.
        """
        missing = 0
        indexed = await self._objects.list(
            provider_connection_id=provider_connection_id,
        )
        for obj in indexed:
            if (
                obj.content_reference in observed
                or obj.status == "missing"
                or is_umedia_managed_reference(obj.content_reference)
            ):
                continue
            await self._objects.update(obj.uid, {"status": "missing"})
            missing += 1
            linked = await self._files.get_by_storage_object(obj.uid)
            if linked is not None and not linked.is_deleted:
                await self._soft_delete_tree(linked)
        return missing

    async def _push_indexed_content(
        self,
        provider_connection_id: str,
        obj: StorageObjectRecord,
    ) -> None:
        """Re-write the indexed object onto the provider (ours wins)."""
        chunks: list[bytes] = [
            chunk
            async for chunk in self._plugins.read_content(
                provider_connection_id,
                obj.content_reference,
                range_header=None,
            )
        ]
        content = b"".join(chunks)
        await self._plugins.update_resource(
            provider_connection_id,
            obj.content_reference,
            UpdateResourceIn(name=None, parent_id=None),
            content,
        )

    # ------------------------------------------------------------------
    # Delete lifecycle (soft-delete MediaFile only; StorageObject stays)
    # ------------------------------------------------------------------
    # The ACL is checked once, at the root of the operation -- the
    # recursive `_*_tree` helpers act on every descendant regardless of
    # per-child grants, same rule the Resource layer had.

    async def soft_delete_for_connection(
        self,
        provider_connection_id: str,
    ) -> int:
        """Trash every live MediaFile bound or synced to a connection
        being removed. Provider objects are not touched by this operation.

        No actor ACL -- this is a system cascade when the connection is
        deleted, same stance as `_mark_unseen_missing`. Soft-deletes
        forest roots only (parent not also a candidate) so `_soft_delete_tree`
        covers each subtree once. Returns the number of roots trashed.
        """
        by_uid: dict[str, MediaFileRecord] = {}
        for record in await self._files.list_for_connection(
            provider_connection_id,
        ):
            by_uid[record.uid] = record
        for obj in await self._objects.list(
            provider_connection_id=provider_connection_id,
        ):
            linked = await self._files.get_by_storage_object(obj.uid)
            if linked is not None and not linked.is_deleted:
                by_uid[linked.uid] = linked

        candidate_ids = set(by_uid)
        roots = [
            record
            for record in by_uid.values()
            if record.parent_id not in candidate_ids
        ]
        tree_uids = set(candidate_ids)
        pending = list(roots)
        while pending:
            record = pending.pop()
            tree_uids.add(record.uid)
            if record.type == "folder":
                pending.extend(await self._files.list(parent_id=record.uid))
        if candidate_ids:
            await self._files.soft_delete_many(sorted(tree_uids))
        return len(roots)

    async def soft_delete(self, uid: str, *, actor_user_id: str) -> None:
        record = await self.get(uid, actor_user_id=actor_user_id)
        self._require(record, actor_user_id, PermissionEnum.DELETE, "delete")
        await self._soft_delete_tree(record)

    async def _soft_delete_tree(self, record: MediaFileRecord) -> None:
        if record.type == "folder":
            for child in await self._files.list(parent_id=record.uid):
                await self._soft_delete_tree(child)
        await self._files.soft_delete(record.uid)

    async def restore(self, uid: str, *, actor_user_id: str) -> None:
        record = await self._get_visible(
            uid,
            actor_user_id=actor_user_id,
            include_deleted=True,
        )
        self._require(record, actor_user_id, PermissionEnum.DELETE, "restore")
        await self._restore_tree(record)

    async def _restore_tree(self, record: MediaFileRecord) -> None:
        if record.type == "folder":
            children = await self._files.list(
                parent_id=record.uid,
                include_deleted=True,
            )
            for child in children:
                await self._restore_tree(child)
        await self._files.restore(record.uid)

    async def hard_delete(self, uid: str, *, actor_user_id: str) -> None:
        record = await self._get_visible(
            uid,
            actor_user_id=actor_user_id,
            include_deleted=True,
        )
        self._require(
            record,
            actor_user_id,
            PermissionEnum.DELETE,
            "permanently delete",
        )
        if not record.is_deleted:
            raise MediaFileStateError(
                f"File '{uid}' must be deleted before permanent removal",
            )
        await self._hard_delete_tree(record, delete_provider=True)

    async def _hard_delete_tree(
        self,
        record: MediaFileRecord,
        *,
        delete_provider: bool = False,
    ) -> None:
        records = [record]
        if record.type == "folder":
            children = await self._files.list(
                parent_id=record.uid,
                include_deleted=True,
            )
            for child in children:
                records.extend(
                    await self._collect_hard_delete_records(child),
                )
        if delete_provider:
            await self._delete_provider_objects(records)
        for item in reversed(records):
            await self._files.hard_delete(item.uid)

    async def _collect_hard_delete_records(
        self,
        record: MediaFileRecord,
    ) -> list[MediaFileRecord]:
        records = [record]
        if record.type == "folder":
            children = await self._files.list(
                parent_id=record.uid,
                include_deleted=True,
            )
            for child in children:
                records.extend(await self._collect_hard_delete_records(child))
        return records

    async def _delete_provider_objects(
        self,
        records: list[MediaFileRecord],
    ) -> None:
        target_ids = {record.uid for record in records}
        objects: dict[str, MediaFileRecord] = {}
        for record in records:
            if record.storage_object_uid is not None:
                objects.setdefault(record.storage_object_uid, record)

        for storage_object_uid, fallback in objects.items():
            linked_ids = set(
                await self._files.list_uids_by_storage_object(
                    storage_object_uid,
                ),
            )
            if linked_ids - target_ids:
                continue
            storage_object = await self._objects.get(storage_object_uid)
            provider_connection_id = (
                storage_object.provider_connection_id
                if storage_object is not None
                else fallback.provider_connection_id
            )
            content_reference = (
                storage_object.content_reference
                if storage_object is not None
                else fallback.content_reference
            )
            if provider_connection_id is None or content_reference is None:
                continue
            try:
                await self._plugins.delete_resource(
                    provider_connection_id,
                    content_reference,
                )
            except ResourceNotFoundError:
                pass
            except Exception as error:
                raise MediaFileDeleteFailedError(str(error)) from error
            if storage_object is not None:
                await self._objects.soft_delete(storage_object_uid)

    async def purge_expired_trash(
        self,
        *,
        older_than_days: int = TRASH_RETENTION_DAYS,
    ) -> int:
        """Permanently remove every trash root soft-deleted more than
        `older_than_days` days ago -- each root taking its whole subtree
        with it, exactly like `hard_delete` but with no per-user ACL:
        this is the nightly system job (apps/media_files/worker.py), not
        a user action. Returns the number of expired *roots* purged (a
        folder tree counts once, however many rows it removed).

        The cutoff compares against the naive `datetime.now()` the
        repository's `soft_delete` stamps into `deleted_at`. As always
        (doc 11 v1), only library rows go -- StorageObjects and provider
        bytes survive because this system purge is library-only.
        """
        cutoff = datetime.now() - timedelta(days=older_than_days)
        roots = await self._files.list_expired_trash(cutoff=cutoff)
        for root in roots:
            await self._hard_delete_tree(root)
        return len(roots)
