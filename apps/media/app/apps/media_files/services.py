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

import dataclasses
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from apps.storage_objects.schemas import StorageObjectRecord
from plugins.contracts import CreateResourceIn, ResourceNotFoundError, UpdateResourceIn
from plugins.contracts import Resource as PluginResource
from utils.pagination import Page

from .content_type import index_content_type
from .errors import (
    MediaFileNotFoundError,
    MediaFilePermissionError,
    MediaFileStateError,
    MediaFileValidationError,
    MediaFileWriteFailedError,
)
from .import_sync import ImportSyncMixin
from .legacy_staging import is_legacy_staging_folder
from .permissions import (
    PermissionEnum,
    can_open_content,
    can_read,
    effective_permission,
)
from .placement import PlacementSettings, pick_connection
from .protocols import (
    AccessKeyServiceProtocol,
    ConnectionRepositoryProtocol,
    InstanceSettingsRepositoryProtocol,
    MediaFileRepositoryProtocol,
    PluginGatewayProtocol,
    StorageObjectRepositoryProtocol,
)
from .provider_layout import (
    plugin_upload_parent,
)
from .schemas import (
    HistoryEntry,
    MediaFileRecord,
)
from .search import MediaFileSearchMixin
from .shared import (
    LIST_SCOPES,
    MIRROR_CAPABILITY,
    UNSET,
    _hash,
    _hash_stream,
)
from .sharing import SharingMixin
from .trash import TrashMixin

# A file whose download keeps failing (e.g. an unreadable symlink: the plugin
# answers 200 and closes the body early) used to be retried on EVERY sync cycle,
# forever, one Sentry event each. Remember failures per object in this process
# and back off exponentially (10 min .. 24 h).
HASH_RETRY_BASE_SECONDS = 600.0
HASH_RETRY_MAX_SECONDS = 24 * 3600.0
HASH_FAILURE_CACHE_LIMIT = 10_000
# uid -> (attempts, retry_not_before)
_hash_failures: dict[str, tuple[int, float]] = {}


def _hash_in_backoff(uid: str, now: float) -> bool:
    entry = _hash_failures.get(uid)
    return entry is not None and now < entry[1]


def _note_hash_failure(uid: str, now: float) -> int:
    attempts = _hash_failures.get(uid, (0, 0.0))[0] + 1
    delay = min(HASH_RETRY_BASE_SECONDS * 2 ** (attempts - 1), HASH_RETRY_MAX_SECONDS)
    if uid not in _hash_failures and len(_hash_failures) >= HASH_FAILURE_CACHE_LIMIT:
        _hash_failures.pop(next(iter(_hash_failures)))  # drop the oldest entry
    _hash_failures[uid] = (attempts, now + delay)
    return attempts


class MediaFileService(
    SharingMixin,
    ImportSyncMixin,
    TrashMixin,
    MediaFileSearchMixin,
):
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
                parent_id,
                {"provider_connection_id": picked},
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
                dest_parent_id,
                actor_user_id=actor_user_id,
            )

        if byte_copy:
            if (
                source.provider_connection_id is None
                or source.content_reference is None
            ):
                raise MediaFileValidationError(
                    f"File '{source.uid}' has no linked storage object",
                )
            chunks: list[bytes] = [
                chunk
                async for chunk in self._plugins.read_content(
                    source.provider_connection_id,
                    source.content_reference,
                    range_header=None,
                )
                if chunk
            ]
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
                discard_failed_record=True,
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
        discard_failed_record: bool = False,
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
            await self._handle_upload_failure(
                record.uid,
                provider_connection_id,
                error,
                discard_failed_record=discard_failed_record,
            )
            raise MediaFileValidationError(str(error)) from error

        plugin_resource: PluginResource | None = None
        try:
            plugin_resource = await self._plugins.create_resource(
                provider_connection_id,
                CreateResourceIn(
                    name=name,
                    type="file",
                    parent_id=plugin_parent,
                ),
                content,
            )
            await self._verify(provider_connection_id, plugin_resource)
        except Exception as error:
            await self._handle_upload_failure(
                record.uid,
                provider_connection_id,
                error,
                discard_failed_record=discard_failed_record,
                plugin_resource=plugin_resource,
            )
            raise MediaFileWriteFailedError(str(error)) from error

        obj = await self._objects.upsert({
            "provider_connection_id": provider_connection_id,
            "content_reference": plugin_resource.id,
            "provider_parent_ref": plugin_resource.parent_id,
            "type": plugin_resource.type or "file",
            "name": plugin_resource.name or name,
            "content_hash": await _hash(content),
            "hash_algorithm": "sha256",
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

    async def _handle_upload_failure(
        self,
        media_file_uid: str,
        provider_connection_id: str | None,
        error: Exception,
        *,
        discard_failed_record: bool,
        plugin_resource: PluginResource | None = None,
    ) -> None:
        if not discard_failed_record:
            await self._files.update(
                media_file_uid,
                {"status": "failed", "error": str(error)},
            )
            return
        if plugin_resource is not None:
            try:
                await self._plugins.delete_resource(
                    provider_connection_id,
                    plugin_resource.id,
                )
            except ResourceNotFoundError:
                pass
            except Exception:
                logging.getLogger(__name__).exception(
                    "failed to remove unverified copied resource: %s",
                    media_file_uid,
                )
        await self._files.hard_delete(media_file_uid)

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
            "hash_algorithm": "sha256",
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
                new_parent.uid,
                {"provider_connection_id": item_conn},
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
        failed = 0
        skipped = 0
        after_uid: str | None = None
        while pending := await self._objects.list_missing_hashes(
            provider_connection_id=provider_connection_id,
            after_uid=after_uid,
            limit=batch_size,
        ):
            after_uid = pending[-1].uid
            for item in pending:
                if _hash_in_backoff(item.uid, time.monotonic()):
                    skipped += 1
                    continue
                try:
                    content_hash = await _hash_stream(
                        self._plugins.read_content(
                            provider_connection_id,
                            item.content_reference,
                            range_header=None,
                        ),
                    )
                except Exception:
                    failed += 1
                    attempts = _note_hash_failure(item.uid, time.monotonic())
                    if attempts == 1:  # one error event per file; then quiet
                        logging.exception(
                            "Content hash failed for %s on connection %s",
                            item.content_reference,
                            provider_connection_id,
                        )
                    continue
                _hash_failures.pop(item.uid, None)
                stored = await self._objects.set_content_hash_if_unchanged(
                    uid=item.uid,
                    expected_size=item.size,
                    expected_metadata=item.metadata,
                    content_hash=content_hash,
                )
                completed += int(stored)
        if failed or skipped:
            logging.warning(
                "Content hash pass for connection %s: %d failed, %d in backoff",
                provider_connection_id,
                failed,
                skipped,
            )
        return completed

    # ------------------------------------------------------------------
    # Delete lifecycle (soft-delete MediaFile only; StorageObject stays)
    # ------------------------------------------------------------------
    # The ACL is checked once, at the root of the operation -- the
    # recursive `_*_tree` helpers act on every descendant regardless of
    # per-child grants, same rule the Resource layer had.
