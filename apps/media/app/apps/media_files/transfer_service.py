"""Bulk library move/copy jobs with progress.

Routes only enqueue; all decisions live here. Single-item copy helpers
and ACL checks stay on `MediaFileService`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .errors import MediaFileNotFoundError, MediaFileValidationError
from .names import resolve_conflict_name
from .notifications import NotificationService
from .schemas import MediaFileRecord
from .services import MediaFileService
from .transfer_repository import TransferRecord, TransferRepository

logger = logging.getLogger(__name__)

TransferOperation = Literal["move", "copy"]
TransferConflict = Literal["rename", "skip"]


def _exception_message(error: BaseException) -> str:
    """Prefer BaseHTTPException.detail over `422: …` str() form."""
    detail = getattr(error, "detail", None)
    if isinstance(detail, str) and detail:
        return detail
    message = getattr(error, "message", None)
    if isinstance(message, str) and message:
        return message
    return str(error)


def _humanize_transfer_error(message: str) -> str:
    """Map raw plugin/rclone noise to a short, actionable line."""
    lower = message.lower()
    compact = lower.replace(" ", "")
    # Source-read failures (RFS GetObject 403, truncated gzip, rclone cat).
    # Check before the generic 403 → PutObject mapping: the same status
    # code appears when the *source* key cannot download the object.
    source_read = (
        "getobject" in compact
        or "incomplete chunked read" in lower
        or "failed to cat" in lower
        or (
            "gzip" in lower
            and (
                "serializ" in lower
                or "invalid" in lower
                or "unexpected eof" in lower
                or "corrupt" in lower
            )
        )
    )
    if source_read:
        return (
            "Source storage denied reading the file (GetObject 403). "
            "Temporary is a pointer clipboard, not a library folder; "
            "same-storage paste still works for real folders; "
            "fix the source key's read permission for cross-storage copy."
        )
    if "403" in message or "forbidden" in lower or "accessdenied" in compact:
        return (
            "Destination storage denied the write (403 Forbidden). "
            "The access key for that connection can list/import objects "
            "but cannot upload — grant PutObject (or use a write-capable key)."
        )
    if "plugin content read failed" in lower:
        return (
            "Could not read the source file from storage "
            "(plugin read failed / timed out)."
        )
    return message


@dataclass(frozen=True)
class TransferCreate:
    operation: TransferOperation
    source_ids: list[str]
    dest_parent_id: str | None = None
    conflict: TransferConflict = "rename"


class TransferService:
    def __init__(
        self,
        transfers: TransferRepository,
        media_files: MediaFileService,
        *,
        tasks: set[asyncio.Task] | None = None,
        notifications: NotificationService | None = None,
    ) -> None:
        self._transfers = transfers
        self._media = media_files
        self._tasks = tasks if tasks is not None else set()
        self._notifications = notifications

    async def create_and_enqueue(
        self,
        actor_user_id: str,
        body: TransferCreate,
        *,
        is_admin: bool = False,
        schedule: bool = True,
    ) -> TransferRecord:
        if not body.source_ids:
            raise MediaFileValidationError("source_ids must contain at least one id")
        # Validate sources + dest up front so a bad request fails before 202.
        sources = [
            await self._media.get(source_id, actor_user_id=actor_user_id)
            for source_id in body.source_ids
        ]
        if body.operation == "move" and any(
            source.parent_id == body.dest_parent_id
            or (source.type == "folder" and source.uid == body.dest_parent_id)
            for source in sources
        ):
            raise MediaFileValidationError(
                "A move destination must differ from each source's current folder",
            )
        if body.dest_parent_id is not None:
            await self._media._writable_folder(
                body.dest_parent_id,
                actor_user_id=actor_user_id,
            )

        total = await self._count_items(
            sources,
            operation=body.operation,
            dest_parent_id=body.dest_parent_id,
        )
        record = await self._transfers.create({
            "owner_id": actor_user_id,
            "operation": body.operation,
            "status": "queued",
            "source_ids": list(body.source_ids),
            "dest_parent_id": body.dest_parent_id,
            "conflict": body.conflict,
            "total_items": total,
            "done_items": 0,
            "failed_items": 0,
            "meta_data": {"is_admin": bool(is_admin)},
        })
        if schedule:
            task = asyncio.create_task(
                self.run_job(record.uid),
                name=f"library-transfer:{record.uid}",
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return record

    async def get(
        self,
        uid: str,
        *,
        actor_user_id: str,
    ) -> TransferRecord:
        record = await self._transfers.get(uid)
        if record is None or record.owner_id != actor_user_id:
            raise MediaFileNotFoundError(uid)
        return record

    async def list_for_user(
        self,
        *,
        actor_user_id: str,
        limit: int = 50,
    ) -> list[TransferRecord]:
        return await self._transfers.list_for_owner(
            owner_id=actor_user_id,
            limit=limit,
        )

    async def cancel(
        self,
        uid: str,
        *,
        actor_user_id: str,
    ) -> TransferRecord:
        record = await self.get(uid, actor_user_id=actor_user_id)
        if record.status == "queued":
            cancelled = await self._transfers.update_if_status(
                uid,
                "queued",
                {
                    "status": "cancelled",
                    "finished_at": datetime.now(),
                    "current_name": None,
                },
            )
            if cancelled:
                return await self.get(uid, actor_user_id=actor_user_id)
            record = await self.get(uid, actor_user_id=actor_user_id)
        if record.status == "running":
            await self._transfers.update_if_status(
                uid,
                "running",
                {"status": "cancelling"},
            )
            return await self.get(uid, actor_user_id=actor_user_id)
        return record

    async def run_job(self, uid: str) -> TransferRecord:
        record = await self._transfers.get(uid)
        if record is None:
            raise MediaFileNotFoundError(uid)
        start_result = await self._start_job(uid)
        if start_result is not None:
            return start_result
        try:
            await self._run_sources(uid, record)
        except Exception as error:
            logger.exception("transfer job failed: %s", uid)
            if await self._cancel_requested(uid):
                return await self._finish_cancelled(uid)
            failed = await self._transfers.update(
                uid,
                {
                    "status": "failed",
                    "error": _humanize_transfer_error(_exception_message(error)),
                    "finished_at": datetime.now(),
                    "current_name": None,
                },
            )
            await self._notify_failure(failed)
            return failed
        return await self._finalize_job(uid)

    async def _start_job(self, uid: str) -> TransferRecord | None:
        started = await self._transfers.update_if_status(
            uid,
            "queued",
            {
                "status": "running",
                "started_at": datetime.now(),
                "current_name": None,
                "error": None,
            },
        )
        if not started:
            record = await self._transfers.get(uid)
            if record is None:
                raise MediaFileNotFoundError(uid)
            if record.status == "cancelling":
                return await self._finish_cancelled(uid)
            return record
        return None

    async def _run_sources(self, uid: str, record: TransferRecord) -> None:
        for source_id in record.source_ids:
            if await self._cancel_requested(uid):
                return
            try:
                source = await self._media.get(
                    source_id,
                    actor_user_id=record.owner_id,
                )
            except MediaFileNotFoundError as error:
                await self._mark_item_failed(
                    uid,
                    _exception_message(error),
                    source_uid=source_id,
                )
                continue
            try:
                await self._process_source(uid, source, record)
            except Exception as error:
                logger.exception("transfer item failed: %s", source_id)
                await self._mark_item_failed(
                    uid,
                    _exception_message(error),
                    name=source.name,
                    source_uid=source_id,
                )

    async def _process_source(
        self,
        uid: str,
        source: MediaFileRecord,
        record: TransferRecord,
    ) -> None:
        if record.operation == "move":
            await self._process_move(
                uid,
                source,
                record,
                actor_user_id=record.owner_id,
                is_admin=record.is_admin,
            )
            return
        await self._process_copy(
            uid,
            source,
            record,
            actor_user_id=record.owner_id,
            is_admin=record.is_admin,
        )

    async def _finalize_job(self, uid: str) -> TransferRecord:
        final = await self._transfers.get(uid)
        assert final is not None  # noqa: S101
        if final.status == "cancelling":
            return await self._finish_cancelled(uid)
        if final.failed_items == 0:
            status = "completed"
        elif final.failed_items == final.done_items:
            status = "failed"
        else:
            status = "partial"
        finished = await self._transfers.update_if_status(
            uid,
            "running",
            {
                "status": status,
                "finished_at": datetime.now(),
                "current_name": None,
            },
        )
        if finished:
            result = await self._transfers.get(uid)
            assert result is not None  # noqa: S101
            return result
        final = await self._transfers.get(uid)
        assert final is not None  # noqa: S101
        if final.status == "cancelling":
            return await self._finish_cancelled(uid)
        return final

    async def _notify_failure(self, record: TransferRecord) -> None:
        if self._notifications is None or not record.error:
            return
        await self._notifications.record_failure(
            owner_id=record.owner_id,
            operation=record.operation,
            item_name=record.current_name or f"{record.operation} job",
            error=record.error,
            source_type="transfer",
            source_uid=record.uid,
        )

    async def _cancel_requested(self, uid: str) -> bool:
        record = await self._transfers.get(uid)
        return record is None or record.status in {"cancelling", "cancelled"}

    async def _finish_cancelled(self, uid: str) -> TransferRecord:
        try:
            await self._rollback_transfer(uid)
        except Exception as error:
            logger.exception("transfer cancellation cleanup failed: %s", uid)
            cleanup_error = _exception_message(error)
            failed = await self._transfers.update(
                uid,
                {
                    "status": "failed",
                    "error": f"Cancellation cleanup failed: {cleanup_error}",
                    "finished_at": datetime.now(),
                    "current_name": None,
                },
            )
            await self._notify_failure(failed)
            return failed
        await self._transfers.update(
            uid,
            {
                "status": "cancelled",
                "finished_at": datetime.now(),
                "current_name": None,
            },
        )
        record = await self._transfers.get(uid)
        assert record is not None  # noqa: S101
        return record

    async def _rollback_transfer(self, uid: str) -> None:
        record = await self._transfers.get(uid)
        if record is None:
            raise MediaFileNotFoundError(uid)

        for moved in reversed(record.moved_sources):
            try:
                source = await self._media._get_visible(
                    moved["uid"],
                    actor_user_id=record.owner_id,
                    include_deleted=True,
                )
            except MediaFileNotFoundError:
                continue
            if source.is_deleted:
                await self._media.restore(
                    source.uid,
                    actor_user_id=record.owner_id,
                )
            await self._media.update(
                source.uid,
                actor_user_id=record.owner_id,
                name=moved["name"] if moved["name"] != source.name else None,
                parent_id=moved["parent_id"],
            )

        for created_id in reversed(record.created_ids):
            try:
                created = await self._media._get_visible(
                    created_id,
                    actor_user_id=record.owner_id,
                    include_deleted=True,
                )
            except MediaFileNotFoundError:
                continue
            if not created.is_deleted:
                await self._media.soft_delete(
                    created_id,
                    actor_user_id=record.owner_id,
                )
            await self._media.hard_delete(
                created_id,
                actor_user_id=record.owner_id,
            )

    async def _record_created(self, job_uid: str, created_id: str) -> None:
        record = await self._transfers.get(job_uid)
        assert record is not None  # noqa: S101
        if created_id in record.created_ids:
            return
        metadata = {
            "is_admin": record.is_admin,
            "created_ids": [*record.created_ids, created_id],
            "moved_sources": record.moved_sources,
        }
        await self._transfers.update(job_uid, {"meta_data": metadata})

    async def _record_move_undo(
        self,
        job_uid: str,
        source: MediaFileRecord,
    ) -> None:
        record = await self._transfers.get(job_uid)
        assert record is not None  # noqa: S101
        if any(item["uid"] == source.uid for item in record.moved_sources):
            return
        metadata = {
            "is_admin": record.is_admin,
            "created_ids": record.created_ids,
            "moved_sources": [
                *record.moved_sources,
                {
                    "uid": source.uid,
                    "parent_id": source.parent_id,
                    "name": source.name,
                },
            ],
        }
        await self._transfers.update(job_uid, {"meta_data": metadata})

    # ------------------------------------------------------------------
    # Move / copy
    # ------------------------------------------------------------------

    async def _process_move(
        self,
        job_uid: str,
        source: MediaFileRecord,
        record: TransferRecord,
        *,
        actor_user_id: str,
        is_admin: bool = False,
    ) -> None:
        if await self._is_cross_storage(source, record.dest_parent_id):
            failed_before = (await self._transfers.get(job_uid)).failed_items
            created = await self._copy_tree(
                job_uid,
                source,
                dest_parent_id=record.dest_parent_id,
                conflict=record.conflict,
                actor_user_id=actor_user_id,
                byte_copy=True,
                is_admin=is_admin,
            )
            if created is not None:
                await self._record_created(job_uid, created.uid)
            if await self._cancel_requested(job_uid):
                return
            failed_after = (await self._transfers.get(job_uid)).failed_items
            if failed_after == failed_before:
                await self._record_move_undo(job_uid, source)
                await self._media.soft_delete(
                    source.uid,
                    actor_user_id=actor_user_id,
                )
            return

        name = await self._resolve_dest_name(
            source.name,
            dest_parent_id=record.dest_parent_id,
            conflict=record.conflict,
            actor_user_id=actor_user_id,
        )
        if name is None:
            await self._bump_done(job_uid, current_name=source.name)
            return
        await self._transfers.update(job_uid, {"current_name": name})
        await self._record_move_undo(job_uid, source)
        await self._media.update(
            source.uid,
            actor_user_id=actor_user_id,
            name=name if name != source.name else None,
            parent_id=record.dest_parent_id,
        )
        await self._bump_done(job_uid, current_name=name)

    async def _process_copy(
        self,
        job_uid: str,
        source: MediaFileRecord,
        record: TransferRecord,
        *,
        actor_user_id: str,
        is_admin: bool = False,
    ) -> None:
        byte_copy = await self._is_cross_storage(source, record.dest_parent_id)
        created = await self._copy_tree(
            job_uid,
            source,
            dest_parent_id=record.dest_parent_id,
            conflict=record.conflict,
            actor_user_id=actor_user_id,
            byte_copy=byte_copy,
            is_admin=is_admin,
        )
        if created is not None:
            await self._record_created(job_uid, created.uid)

    async def _copy_tree(
        self,
        job_uid: str,
        source: MediaFileRecord,
        *,
        dest_parent_id: str | None,
        conflict: str,
        actor_user_id: str,
        byte_copy: bool,
        is_admin: bool = False,
    ) -> MediaFileRecord | None:
        if await self._cancel_requested(job_uid):
            return None
        name = await self._resolve_dest_name(
            source.name,
            dest_parent_id=dest_parent_id,
            conflict=conflict,
            actor_user_id=actor_user_id,
        )
        if name is None:
            await self._bump_done(job_uid, current_name=source.name)
            return None

        await self._transfers.update(job_uid, {"current_name": name})
        if source.type == "folder":
            created = await self._media.create_folder(
                name=name,
                parent_id=dest_parent_id,
                owner_id=actor_user_id,
                is_admin=is_admin,
            )
            await self._bump_done(job_uid, current_name=name)
            for child in await self._media._files.list(
                parent_id=source.uid,
            ):
                if await self._cancel_requested(job_uid):
                    break
                try:
                    await self._copy_tree(
                        job_uid,
                        child,
                        dest_parent_id=created.uid,
                        conflict=conflict,
                        actor_user_id=actor_user_id,
                        byte_copy=byte_copy,
                        is_admin=is_admin,
                    )
                except Exception as error:
                    logger.exception("copy child failed: %s", child.uid)
                    await self._mark_item_failed(
                        job_uid,
                        _exception_message(error),
                        name=child.name,
                    )
            return created

        created = await self._media.copy_file(
            source,
            dest_parent_id=dest_parent_id,
            name=name,
            actor_user_id=actor_user_id,
            byte_copy=byte_copy,
            is_admin=is_admin,
        )
        await self._bump_done(job_uid, current_name=name)
        return created

    # ------------------------------------------------------------------
    # Counting / naming / progress
    # ------------------------------------------------------------------

    async def _count_items(
        self,
        sources: Sequence[MediaFileRecord],
        *,
        operation: str,
        dest_parent_id: str | None,
    ) -> int:
        total = 0
        for source in sources:
            cross = await self._is_cross_storage(source, dest_parent_id)
            if operation == "move" and not cross:
                total += 1
            else:
                total += await self._count_tree(source)
        return total

    async def _count_tree(self, root: MediaFileRecord) -> int:
        count = 1
        if root.type == "folder":
            for child in await self._media._files.list(
                parent_id=root.uid,
            ):
                count += await self._count_tree(child)
        return count

    async def _is_cross_storage(
        self,
        source: MediaFileRecord,
        dest_parent_id: str | None,
    ) -> bool:
        """True only when both ends name *different non-null* connections.

        Unbound destinations (pure library folders) and same-
        connection targets use share-link copy (`byte_copy=False`) — never
        stream-read through the plugin.
        """
        if dest_parent_id is None:
            return False
        item_conn = source.provider_connection_id
        if not item_conn and source.type == "folder":
            item_conn = await self._media._bound_connection(source.uid)
        dest_conn = await self._media._bound_connection(dest_parent_id)
        if item_conn is None or dest_conn is None:
            return False
        return item_conn != dest_conn

    async def _resolve_dest_name(
        self,
        name: str,
        *,
        dest_parent_id: str | None,
        conflict: str,
        actor_user_id: str,
    ) -> str | None:
        siblings = await self._media._files.list(
            parent_id=dest_parent_id,
        )
        # Only names the actor can see matter for conflict UX; owned siblings
        # are enough for v1 (dest is always a writable folder of the actor).
        claimed = {
            sibling.name for sibling in siblings if sibling.owner_id == actor_user_id
        }
        if name not in claimed:
            return name
        if conflict == "skip":
            return None
        return resolve_conflict_name(name, claimed)

    async def _bump_done(
        self,
        job_uid: str,
        *,
        current_name: str | None = None,
    ) -> None:
        current = await self._transfers.get(job_uid)
        assert current is not None  # noqa: S101
        await self._transfers.update(
            job_uid,
            {
                "done_items": current.done_items + 1,
                "current_name": current_name,
            },
        )

    async def _mark_item_failed(
        self,
        job_uid: str,
        message: str,
        *,
        name: str | None = None,
        source_uid: str | None = None,
    ) -> None:
        current = await self._transfers.get(job_uid)
        assert current is not None  # noqa: S101
        failed = await self._transfers.update(
            job_uid,
            {
                "failed_items": current.failed_items + 1,
                "done_items": current.done_items + 1,
                "current_name": name,
                "error": _humanize_transfer_error(message),
            },
        )
        if self._notifications is not None:
            await self._notifications.record_failure(
                owner_id=failed.owner_id,
                operation=failed.operation,
                item_name=name or f"{failed.operation} item",
                error=_humanize_transfer_error(message),
                source_type="transfer_item",
                source_uid=f"{job_uid}:{source_uid or failed.failed_items}",
            )
