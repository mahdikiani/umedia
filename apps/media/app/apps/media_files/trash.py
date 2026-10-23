"""MediaFileService: Trash operations (mixed into `MediaFileService`)."""

from datetime import datetime, timedelta

from plugins.contracts import ResourceNotFoundError

from .errors import (
    MediaFileDeleteFailedError,
    MediaFileStateError,
)
from .permissions import (
    PermissionEnum,
)
from .schemas import (
    MediaFileRecord,
)
from .shared import (
    TRASH_RETENTION_DAYS,
)


class TrashMixin:
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
