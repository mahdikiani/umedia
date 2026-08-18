"""`Resource` business rules: dedup, versioning, soft/hard delete, restore,
volume stats, and the plugin-backed write lifecycle.

See docs/02-architecture.md "Correctness & consistency" #2 for the
`processing` -> verify -> `completed`/`failed` rule every plugin-backed
write follows here, and docs/03-provider-system.md for why a resource's
`content_reference` (called `id` in the plugin contract) may change across
`update()`.
"""

import asyncio
import hashlib
from collections.abc import AsyncIterator
from typing import Any, Protocol

from plugins.contracts import CreateResourceIn, UpdateResourceIn
from plugins.contracts import Resource as PluginResource

from .errors import (
    ResourceNotFoundError,
    ResourcePermissionError,
    ResourceStateError,
    ResourceValidationError,
    ResourceWriteFailedError,
)
from .permissions import (
    PermissionEnum,
    can_read,
    can_write,
    effective_permission,
)
from .schemas import HistoryEntry, ResourceRecord

PUBLIC_PERMISSIONS = {"none", "read"}
DIRECTORY_CONTENT_TYPE = "inode/directory"
DEFAULT_CONTENT_TYPE = "application/octet-stream"

#: `list_children` scopes -- docs/05-api-design.md `GET /resources?scope=`.
LIST_SCOPES = {"owned", "shared_with_me", "shared_by_me", "all_visible"}


class ResourceRepositoryProtocol(Protocol):
    async def create(self, data: dict[str, Any]) -> ResourceRecord: ...
    async def get(self, uid: str) -> ResourceRecord | None: ...
    async def list(
        self, *, parent_id: str | None, include_deleted: bool = False,
    ) -> list[ResourceRecord]: ...
    async def list_for_actor(
        self,
        *,
        actor_user_id: str,
        parent_id: str | None = None,
        scope: str = "owned",
        include_deleted: bool = False,
        # Quoted: in this class body the name `list` is the `list()`
        # *method* above, not the builtin.
    ) -> "list[ResourceRecord]": ...
    async def find_by_content_hash(
        self,
        *,
        provider_connection_id: str,
        parent_id: str | None,
        content_hash: str,
        owner_id: str,
    ) -> ResourceRecord | None: ...
    async def update(self, uid: str, changes: dict[str, Any]) -> ResourceRecord: ...
    async def touch_access(self, uid: str) -> None: ...
    async def soft_delete(self, uid: str) -> None: ...
    async def hard_delete(self, uid: str) -> None: ...
    async def restore(self, uid: str) -> None: ...
    async def volume_stats(self) -> dict[str, int]: ...


class PluginGatewayProtocol(Protocol):
    """Resolves a `provider_connection_id` to the right plugin and calls
    it -- decrypting the connection's config, looking up its manifest,
    and speaking to the right socket are all behind this (P4.3,
    `PluginResourceGateway`). `ResourceService` never sees any of that."""

    async def create_resource(
        self, provider_connection_id: str, metadata: CreateResourceIn, content: bytes,
    ) -> PluginResource: ...
    async def get_resource(
        self, provider_connection_id: str, content_reference: str,
    ) -> PluginResource: ...
    async def update_resource(
        self,
        provider_connection_id: str,
        content_reference: str,
        changes: UpdateResourceIn,
        content: bytes | None,
    ) -> PluginResource: ...
    async def delete_resource(
        self, provider_connection_id: str, content_reference: str,
    ) -> None: ...
    def read_content(
        self,
        provider_connection_id: str,
        content_reference: str,
        *,
        range_header: str | None,
    ) -> AsyncIterator[bytes]: ...


async def _hash(content: bytes) -> str:
    """SHA-256 of `content`, off the event loop.

    `hashlib.sha256` is a plain synchronous call -- fine for a small file,
    but for a large one (this is content-addressed dedup, so it runs on
    *every* create/content-replacing update, not just occasionally) it can
    take long enough to stall the entire process: this is a single shared
    event loop serving every other request too, including unrelated
    users' reads and the plugin health checks that keep the process
    supervisor's restart logic sane. `asyncio.to_thread` moves the CPU
    work off that loop instead of blocking it in place.
    """
    return await asyncio.to_thread(lambda: hashlib.sha256(content).hexdigest())


class ResourceService:
    def __init__(
        self,
        repository: ResourceRepositoryProtocol,
        plugins: PluginGatewayProtocol,
    ) -> None:
        self._repository = repository
        self._plugins = plugins

    # ------------------------------------------------------------------
    # Access control primitives
    # ------------------------------------------------------------------
    async def _get_visible(
        self, uid: str, *, actor_user_id: str, include_deleted: bool = False,
    ) -> ResourceRecord:
        """The record, if the actor may READ it -- `ResourceNotFoundError`
        otherwise (never 403 here: a caller who can't see a resource must
        not learn that its uid exists)."""
        resource = await self._repository.get(uid)
        if resource is None or (resource.is_deleted and not include_deleted):
            raise ResourceNotFoundError(uid)
        if not can_read(resource, actor_user_id):
            raise ResourceNotFoundError(uid)
        return resource

    @staticmethod
    def _require(
        resource: ResourceRecord, actor_user_id: str, level: PermissionEnum,
        operation: str,
    ) -> None:
        """The actor can already see `resource`; 403 if they hold less
        than `level` on it."""
        if effective_permission(resource, actor_user_id) < level:
            raise ResourcePermissionError(
                f"{level.name} permission is required to {operation} "
                f"resource '{resource.uid}'",
            )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    async def get(self, uid: str, *, actor_user_id: str) -> ResourceRecord:
        return await self._get_visible(uid, actor_user_id=actor_user_id)

    async def get_via_public_link(
        self, uid: str, *, actor_user_id: str | None,
    ) -> ResourceRecord:
        """The `/f/{uid}` visibility rule: readable when the resource is
        public (`public_permission == "read"`) *or* the (optional) caller
        holds READ via the ACL. `public_permission` grants nothing
        anywhere else -- see `permissions.py`'s module docstring."""
        resource = await self._repository.get(uid)
        if resource is None or resource.is_deleted:
            raise ResourceNotFoundError(uid)
        if resource.public_permission == "read":
            return resource
        if can_read(resource, actor_user_id):
            return resource
        raise ResourceNotFoundError(uid)

    async def list_children(
        self,
        parent_id: str | None,
        *,
        actor_user_id: str,
        scope: str = "owned",
        include_deleted: bool = False,
    ) -> list[ResourceRecord]:
        if scope not in LIST_SCOPES:
            raise ResourceValidationError(
                f"Invalid scope '{scope}', must be one of {sorted(LIST_SCOPES)}",
            )
        if include_deleted and scope != "owned":
            # Trash browsing is owner-only: deleted rows never appear in
            # shared/visible scopes, so nobody can page through someone
            # else's trash.
            raise ResourceValidationError(
                "include_deleted is only valid with scope 'owned'",
            )
        return await self._repository.list_for_actor(
            actor_user_id=actor_user_id,
            parent_id=parent_id,
            scope=scope,
            include_deleted=include_deleted,
        )

    async def read_content(
        self, uid: str, *, actor_user_id: str, range_header: str | None = None,
    ) -> tuple[ResourceRecord, AsyncIterator[bytes]]:
        resource = await self.get(uid, actor_user_id=actor_user_id)
        return await self._stream(resource, range_header=range_header)

    async def read_content_via_public_link(
        self,
        uid: str,
        *,
        actor_user_id: str | None,
        range_header: str | None = None,
    ) -> tuple[ResourceRecord, AsyncIterator[bytes]]:
        resource = await self.get_via_public_link(uid, actor_user_id=actor_user_id)
        return await self._stream(resource, range_header=range_header)

    async def _stream(
        self, resource: ResourceRecord, *, range_header: str | None,
    ) -> tuple[ResourceRecord, AsyncIterator[bytes]]:
        if resource.content_reference is None:
            raise ResourceNotFoundError(resource.uid)
        stream = self._plugins.read_content(
            resource.provider_connection_id,
            resource.content_reference,
            range_header=range_header,
        )
        await self._repository.touch_access(resource.uid)
        return resource, stream

    async def volume_stats(self) -> dict[str, int]:
        return await self._repository.volume_stats()

    # ------------------------------------------------------------------
    # Sharing
    # ------------------------------------------------------------------
    async def set_public_permission(
        self, uid: str, value: str, *, actor_user_id: str,
    ) -> ResourceRecord:
        """Gate the `/f/{uid}` share link -- `"none"` (default) or
        `"read"`. Requires MANAGE, like every other sharing change."""
        if value not in PUBLIC_PERMISSIONS:
            raise ResourceValidationError(
                f"Invalid public_permission '{value}', must be one of "
                f"{sorted(PUBLIC_PERMISSIONS)}",
            )
        resource = await self.get(uid, actor_user_id=actor_user_id)
        self._require(
            resource, actor_user_id, PermissionEnum.MANAGE, "change sharing of",
        )
        return await self._repository.update(uid, {"public_permission": value})

    async def set_user_permission(
        self,
        uid: str,
        *,
        actor_user_id: str,
        target_user_id: str,
        permission: int,
    ) -> ResourceRecord:
        """Grant/replace `target_user_id`'s level on `uid`; `0` (NONE)
        removes the entry entirely."""
        resource = await self.get(uid, actor_user_id=actor_user_id)
        self._require(
            resource, actor_user_id, PermissionEnum.MANAGE, "change sharing of",
        )
        try:
            level = PermissionEnum(int(permission))
        except ValueError:
            raise ResourceValidationError(
                f"Invalid permission {permission!r}, must be one of "
                f"{[int(member) for member in PermissionEnum]}",
            ) from None
        if target_user_id == resource.owner_id:
            raise ResourceValidationError(
                "The owner already holds full access; their permission "
                "cannot be granted or revoked",
            )
        entries = [
            entry for entry in resource.permissions
            if entry.get("user_id") != target_user_id
        ]
        if level != PermissionEnum.NONE:
            entries.append({"user_id": target_user_id, "permission": int(level)})
        return await self._repository.update(uid, {"permissions": entries})

    async def list_permissions(
        self, uid: str, *, actor_user_id: str,
    ) -> list[dict[str, Any]]:
        resource = await self.get(uid, actor_user_id=actor_user_id)
        self._require(
            resource, actor_user_id, PermissionEnum.MANAGE, "view sharing of",
        )
        return resource.permissions

    async def _resolve_plugin_parent_id(
        self, parent_id: str, *, provider_connection_id: str, actor_user_id: str,
    ) -> str:
        """Translate an app-level `parent_id` (a `Resource.uid`) into
        *that resource's own* `content_reference` -- what the plugin
        actually understands as a parent id (e.g. the `local` plugin's
        POSIX path). The two are unrelated strings; passing our database
        uid straight through to the plugin looks fine at the type level
        but resolves to nothing at the provider (caught the hard way via
        `tests/test_resource_routes.py`'s nested-folder-upload test --
        every fake-repository unit test in `test_resource_service.py`
        missed it because `FakePluginGateway` never actually interprets
        `parent_id`, so this only broke against a real plugin).

        Also rejects a parent that belongs to a *different*
        `provider_connection_id` -- nothing else in `create()`/`update()`
        stops a caller from nesting an S3 object under a Telegram
        message's folder otherwise, which is meaningless at the provider
        level (there is no actual containment relationship to create)."""
        parent = await self._repository.get(parent_id)
        if parent is None or parent.is_deleted:
            raise ResourceValidationError(f"Parent resource '{parent_id}' not found")
        if not can_write(parent, actor_user_id):
            # Same "not found" a nonexistent parent gets when the actor
            # can't even see the folder; a visible-but-read-only share
            # gets the honest 403.
            if not can_read(parent, actor_user_id):
                raise ResourceValidationError(
                    f"Parent resource '{parent_id}' not found",
                )
            raise ResourcePermissionError(
                f"WRITE permission is required to add resources to "
                f"'{parent_id}'",
            )
        if parent.provider_connection_id != provider_connection_id:
            raise ResourceValidationError(
                f"Parent resource '{parent_id}' belongs to a different "
                "provider connection",
            )
        if parent.content_reference is None:
            raise ResourceValidationError(
                f"Parent resource '{parent_id}' is not ready yet (still "
                f"{parent.status})",
            )
        return parent.content_reference

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    async def create(
        self,
        *,
        provider_connection_id: str,
        parent_id: str | None,
        name: str,
        type_: str,
        content: bytes,
        content_type: str | None = None,
        owner_id: str,
    ) -> ResourceRecord:
        """Create a resource owned by `owner_id`. Deduplicated by content
        hash within the same parent + connection + *owner* -- an identical
        re-upload returns the existing row instead of creating a duplicate
        (ported from `apps/media`'s original `FileManager.process_file`).
        Never deduplicated across owners: returning another user's row
        would both leak its existence and bypass its ACL."""
        is_folder = type_ == "folder"
        content_hash = None if is_folder else await _hash(content)

        if content_hash is not None:
            existing = await self._repository.find_by_content_hash(
                provider_connection_id=provider_connection_id,
                parent_id=parent_id,
                content_hash=content_hash,
                owner_id=owner_id,
            )
            if existing is not None:
                return existing

        plugin_parent_id = (
            await self._resolve_plugin_parent_id(
                parent_id,
                provider_connection_id=provider_connection_id,
                actor_user_id=owner_id,
            )
            if parent_id is not None else None
        )

        record = await self._repository.create({
            "provider_connection_id": provider_connection_id,
            "owner_id": owner_id,
            "type": type_,
            "name": name,
            "parent_id": parent_id,
            "content_reference": None,
            "content_hash": content_hash,
            "content_type": content_type
            or (DIRECTORY_CONTENT_TYPE if is_folder else DEFAULT_CONTENT_TYPE),
            "size": 0 if is_folder else len(content),
            "status": "processing",
            "error": None,
            "public_permission": "none",
            "permissions": [],
            "workspace_id": None,
        })

        try:
            plugin_resource = await self._plugins.create_resource(
                provider_connection_id,
                CreateResourceIn(name=name, type=type_, parent_id=plugin_parent_id),
                content,
            )
            await self._verify(provider_connection_id, plugin_resource)
        except Exception as error:
            await self._repository.update(
                record.uid, {"status": "failed", "error": str(error)},
            )
            raise ResourceWriteFailedError(str(error)) from error

        return await self._repository.update(record.uid, {
            "status": "completed",
            "content_reference": plugin_resource.id,
            "size": plugin_resource.size if plugin_resource.size is not None
            else record.size,
            "content_type": plugin_resource.content_type or record.content_type,
        })

    async def update(
        self,
        uid: str,
        *,
        actor_user_id: str,
        name: str | None = None,
        parent_id: str | None = None,
        content: bytes | None = None,
    ) -> ResourceRecord:
        """Rename/move/replace-content -- requires WRITE. A content
        replacement keeps the old content snapshot in `history` (ported
        from `apps/media`'s `FileManager.change_file`)."""
        current = await self.get(uid, actor_user_id=actor_user_id)
        self._require(current, actor_user_id, PermissionEnum.WRITE, "update")
        if current.content_reference is None:
            raise ResourceStateError(f"Resource '{uid}' has no content yet")

        # `parent_id is None` here means "not moving" (see the `changes`
        # dict below), not "move to root" -- only resolve when a move was
        # actually requested, same distinction `LocalBackend.update_resource`
        # itself makes for `UpdateResourceIn.parent_id`.
        plugin_parent_id = (
            await self._resolve_plugin_parent_id(
                parent_id,
                provider_connection_id=current.provider_connection_id,
                actor_user_id=actor_user_id,
            )
            if parent_id is not None else None
        )

        await self._repository.update(uid, {"status": "processing"})

        try:
            plugin_resource = await self._plugins.update_resource(
                current.provider_connection_id,
                current.content_reference,
                UpdateResourceIn(
                    name=name,
                    parent_id=plugin_parent_id,
                    overwrite_content=content is not None,
                ),
                content,
            )
            await self._verify(current.provider_connection_id, plugin_resource)
        except Exception as error:
            await self._repository.update(
                uid, {"status": "failed", "error": str(error)},
            )
            raise ResourceWriteFailedError(str(error)) from error

        changes: dict[str, Any] = {
            "status": "completed",
            "content_reference": plugin_resource.id,
            "name": name or current.name,
            "parent_id": parent_id if parent_id is not None else current.parent_id,
        }
        if content is not None:
            changes["content_hash"] = await _hash(content)
            changes["size"] = (
                plugin_resource.size if plugin_resource.size is not None
                else len(content)
            )
            changes["history"] = [
                *current.history,
                HistoryEntry(
                    content_reference=current.content_reference,
                    content_hash=current.content_hash,
                    content_type=current.content_type,
                    size=current.size,
                ),
            ]

        return await self._repository.update(uid, changes)

    async def _verify(
        self, provider_connection_id: str, plugin_resource: PluginResource,
    ) -> None:
        """Confirm a write actually landed -- a 200 from the plugin isn't
        sufficient proof by itself (docs/02-architecture.md)."""
        verified = await self._plugins.get_resource(
            provider_connection_id, plugin_resource.id,
        )
        if verified.id != plugin_resource.id:
            raise ResourceWriteFailedError(
                "post-write verification returned a different resource",
            )

    # ------------------------------------------------------------------
    # Delete lifecycle (two-step, cascades into folders)
    # ------------------------------------------------------------------
    # The ACL is checked once, at the root of the operation -- the
    # recursive `_*_tree` helpers below then act on every descendant
    # regardless of per-child grants. Deleting a folder you may DELETE
    # deletes its contents; children don't get to survive because a
    # share entry is missing on them.

    async def soft_delete(self, uid: str, *, actor_user_id: str) -> None:
        resource = await self.get(uid, actor_user_id=actor_user_id)
        self._require(resource, actor_user_id, PermissionEnum.DELETE, "delete")
        await self._soft_delete_tree(resource)

    async def _soft_delete_tree(self, resource: ResourceRecord) -> None:
        if resource.type == "folder":
            for child in await self._repository.list(parent_id=resource.uid):
                await self._soft_delete_tree(child)
        await self._repository.soft_delete(resource.uid)

    async def hard_delete(self, uid: str, *, actor_user_id: str) -> None:
        resource = await self._get_visible(
            uid, actor_user_id=actor_user_id, include_deleted=True,
        )
        self._require(
            resource, actor_user_id, PermissionEnum.DELETE, "permanently delete",
        )
        if not resource.is_deleted:
            raise ResourceStateError(
                f"Resource '{uid}' must be soft-deleted before hard delete",
            )
        await self._hard_delete_tree(resource)

    async def _hard_delete_tree(self, resource: ResourceRecord) -> None:
        if resource.type == "folder":
            children = await self._repository.list(
                parent_id=resource.uid, include_deleted=True,
            )
            for child in children:
                await self._hard_delete_tree(child)
        elif resource.content_reference is not None:
            try:
                await self._plugins.delete_resource(
                    resource.provider_connection_id, resource.content_reference,
                )
            except Exception as error:
                raise ResourceWriteFailedError(str(error)) from error
        await self._repository.hard_delete(resource.uid)

    async def restore(self, uid: str, *, actor_user_id: str) -> None:
        resource = await self._get_visible(
            uid, actor_user_id=actor_user_id, include_deleted=True,
        )
        self._require(resource, actor_user_id, PermissionEnum.DELETE, "restore")
        await self._restore_tree(resource)

    async def _restore_tree(self, resource: ResourceRecord) -> None:
        if resource.type == "folder":
            children = await self._repository.list(
                parent_id=resource.uid, include_deleted=True,
            )
            for child in children:
                await self._restore_tree(child)
        await self._repository.restore(resource.uid)
