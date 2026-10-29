"""MediaFileService: ImportSync operations (mixed into `MediaFileService`)."""

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from apps.storage_objects.schemas import StorageObjectRecord
from plugins.client import PluginRPCError
from plugins.contracts import Resource as PluginResource
from plugins.contracts import UpdateResourceIn

from .content_type import index_content_type
from .errors import (
    MediaFileValidationError,
)
from .hashing import pick_native_hash
from .junk import is_junk_folder
from .provider_layout import (
    is_umedia_managed_reference,
)
from .schemas import (
    DIRECTORY_CONTENT_TYPE,
    HistoryEntry,
    MediaFileRecord,
)

logger = logging.getLogger(__name__)

# Hidden entries (".cargo", ".ssh", ".DS_Store") and dependency or cache
# folders are not media. An SFTP connection rooted at a home directory
# imported ~25k toolchain files and its walk (~2.5 s per folder) held the
# poller for days, so no other connection synced (2026-10-25). They are
# neither imported nor walked. Rows already indexed under them are left
# as they are: never marked missing because of this rule.
IGNORED_FOLDER_NAMES = frozenset({"node_modules", "__pycache__"})

_COUNTERS = ("imported", "updated", "pushed", "seen", "restored", "ignored")


def is_ignored_resource(resource: PluginResource) -> bool:
    """Whether the walk leaves this listed entry alone (see above)."""
    name = resource.name or resource.id.rsplit("/", 1)[-1]
    if name.startswith("."):
        return True
    return resource.type == "folder" and name in IGNORED_FOLDER_NAMES


@dataclass
class ImportWalk:
    """Progress of one walk, so the poller can stop it at `deadline`
    (a `time.monotonic()` value) and resume it on the next tick.

    Objects are marked missing only once the whole tree has been listed,
    possibly over several ticks; a paused walk never marks anything.
    """

    deadline: float | None = None
    started_at: float = field(default_factory=time.monotonic)
    stack: list[tuple[str | None, str]] | None = None
    visited: set[str | None] = field(default_factory=set)
    observed: set[str] = field(default_factory=set)
    skipped: set[str] = field(default_factory=set)
    excluded: set[str] = field(default_factory=set)
    counts: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(_COUNTERS, 0),
    )

    @property
    def done(self) -> bool:
        """True once every folder has been listed."""
        return self.stack is not None and not self.stack

    def out_of_time(self) -> bool:
        return self.deadline is not None and time.monotonic() >= self.deadline


def _under_skipped(
    obj: StorageObjectRecord,
    parents: dict[str, str | None],
    skipped: set[str],
) -> bool:
    """Whether `obj` sits (at any depth) inside a folder whose listing was
    skipped this pass. Follows the indexed parent chain, with a guard
    against a cycle in bad provider data."""
    parent = obj.provider_parent_ref
    seen: set[str] = set()
    while parent is not None and parent not in seen:
        if parent in skipped:
            return True
        seen.add(parent)
        parent = parents.get(parent)
    return False


class ImportSyncMixin:
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
        walk: ImportWalk | None = None,
    ) -> dict[str, int]:
        """Walk the provider, reconcile each object, upsert the index.

        `walk` (the poller's) carries a deadline and the walk's progress:
        past the deadline the pass stops after the current folder and
        returns with `pending` > 0; calling again with the same `walk`
        continues where it stopped. Without it the whole tree is walked.

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
        - A sub-folder the plugin cannot list (`PluginRPCError` after the
          client's own retries -- e.g. OneDrive's locked "Personal Vault",
          or a listing that keeps timing out) is skipped with a warning and
          the walk goes on. Objects under a skipped folder are never marked
          missing. Only a failed *root* listing aborts the pass (the
          connection itself is unusable; the poller backs off).
        - Hidden entries and dependency/cache folders are skipped (see
          `is_ignored_resource`); counted in `ignored`.
        """
        connection = await self._connections.get(provider_connection_id)
        if connection is None:
            raise MediaFileValidationError(
                f"Provider connection '{provider_connection_id}' not found",
            )
        mirror = bool(getattr(connection, "mirror_structure", False))
        root = await self._ensure_import_root(
            connection,
            actor_user_id=actor_user_id,
        )

        if walk is None:
            walk = ImportWalk()
        if walk.stack is None:
            walk.stack = [(None, root.uid)]
        stack = walk.stack
        listed = 0
        while stack:
            # Always list at least one folder per call, so a resumed walk
            # moves forward even when it starts past its deadline.
            if listed and walk.out_of_time():
                break
            listed += 1
            provider_ref, library_parent_uid = stack.pop()
            if provider_ref in walk.visited:
                continue
            walk.visited.add(provider_ref)
            try:
                resources = await self._plugins.list_resources(
                    provider_connection_id,
                    parent_id=provider_ref,
                )
            except PluginRPCError as error:
                if provider_ref is None:
                    raise
                # One unreadable folder used to abort the whole walk on
                # every poll (UMEDIA-4/6): nothing after it was ever
                # imported and missing-marking never ran.
                walk.skipped.add(provider_ref)
                logger.warning(
                    "Import for '%s': skipping folder %r, the provider "
                    "could not list it: %s",
                    provider_connection_id,
                    provider_ref,
                    error,
                )
                continue
            await self._reconcile_listing(
                provider_connection_id,
                resources,
                walk,
                actor_user_id=actor_user_id,
                library_parent_uid=library_parent_uid,
                mirror=mirror,
            )

        missing = 0
        if not stack:
            missing = await self._mark_unseen_missing(
                provider_connection_id,
                walk.observed,
                skipped=walk.skipped | walk.excluded,
            )
        return {
            **walk.counts,
            "missing": missing,
            "skipped": len(walk.skipped),
            "pending": len(stack),
        }

    async def _reconcile_listing(
        self,
        provider_connection_id: str,
        resources: list[PluginResource],
        walk: ImportWalk,
        *,
        actor_user_id: str,
        library_parent_uid: str,
        mirror: bool,
    ) -> None:
        """Reconcile one folder's listed entries; queue sub-folders."""
        counts = walk.counts
        stack = walk.stack if walk.stack is not None else []
        for resource in resources:
            counts["seen"] += 1
            walk.observed.add(resource.id)
            if is_umedia_managed_reference(resource.id):
                # Core-owned local dump (per-user upload paths). Keep
                # walking so descendants are observed, but never import
                # them as library files.
                if resource.type == "folder":
                    stack.append((resource.id, library_parent_uid))
                continue
            if is_ignored_resource(resource):
                counts["ignored"] += 1
                if resource.type == "folder":
                    if is_junk_folder(resource.id, resource.name):
                        # Tool caches (`junk.py`) leave the index, the
                        # folder row included.
                        walk.observed.discard(resource.id)
                    else:
                        # Not listed, so its indexed contents are unknown,
                        # not gone: keep them out of missing-marking.
                        walk.excluded.add(resource.id)
                continue
            media_file, deltas = await self._sync_listed_resource(
                provider_connection_id,
                resource,
                actor_user_id=actor_user_id,
                library_parent_uid=library_parent_uid,
                mirror=mirror,
            )
            for key in ("imported", "updated", "pushed", "restored"):
                counts[key] += deltas[key]
            if resource.type == "folder":
                stack.append((resource.id, media_file.uid))

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
        hash_algorithm = (
            previous.hash_algorithm if content_hash and previous else None
        )
        native = None if is_folder else pick_native_hash(resource.hashes)
        if native is not None and (
            content_hash is None
            or (hash_algorithm == native[0] and content_hash != native[1])
        ):
            # The provider already knows the file's hash: index it from the
            # listing instead of leaving it for the download backfill. An
            # unchanged file keeps the hash it has, unless the provider's
            # own hash for the same algorithm says the bytes changed.
            hash_algorithm, content_hash = native
        elif content_hash and hash_algorithm is None:
            hash_algorithm = "sha256"
        obj = await self._objects.upsert({
            "provider_connection_id": provider_connection_id,
            "content_reference": resource.id,
            "provider_parent_ref": resource.parent_id,
            "type": resource.type,
            "name": resource.name,
            "content_hash": content_hash,
            "hash_algorithm": hash_algorithm,
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
        *,
        skipped: set[str] | None = None,
    ) -> int:
        """After a walk: index rows not seen this pass become `missing`
        (still listed in Storage browse) and their linked library files go
        to trash. Already-missing rows are left alone, and so is anything
        under a folder in `skipped` (it could not be listed, so its
        contents are unknown, not gone).
        """
        missing = 0
        indexed = await self._objects.list(
            provider_connection_id=provider_connection_id,
        )
        parents = {obj.content_reference: obj.provider_parent_ref for obj in indexed}
        for obj in indexed:
            if (
                obj.content_reference in observed
                or obj.status == "missing"
                or is_umedia_managed_reference(obj.content_reference)
                or (skipped and _under_skipped(obj, parents, skipped))
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
