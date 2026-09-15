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
    if (
        "403" in message
        or "forbidden" in lower
        or "accessdenied" in compact
    ):
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
    ) -> None:
        self._transfers = transfers
        self._media = media_files
        self._tasks = tasks if tasks is not None else set()

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
        sources = []
        for source_id in body.source_ids:
            sources.append(
                await self._media.get(source_id, actor_user_id=actor_user_id),
            )
        if body.dest_parent_id is not None:
            await self._media._writable_folder(  # noqa: SLF001 -- shared gate
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
        self, uid: str, *, actor_user_id: str,
    ) -> TransferRecord:
        record = await self._transfers.get(uid)
        if record is None or record.owner_id != actor_user_id:
            raise MediaFileNotFoundError(uid)
        return record

    async def list_for_user(
        self, *, actor_user_id: str, limit: int = 50,
    ) -> list[TransferRecord]:
        return await self._transfers.list_for_owner(
            owner_id=actor_user_id, limit=limit,
        )

    async def run_job(self, uid: str) -> TransferRecord:
        record = await self._transfers.get(uid)
        if record is None:
            raise MediaFileNotFoundError(uid)
        actor = record.owner_id
        await self._transfers.update(uid, {
            "status": "running",
            "started_at": datetime.now(),
            "current_name": None,
            "error": None,
        })
        try:
            for source_id in record.source_ids:
                try:
                    source = await self._media.get(
                        source_id, actor_user_id=actor,
                    )
                except MediaFileNotFoundError as error:
                    await self._mark_item_failed(uid, _exception_message(error))
                    continue
                try:
                    if record.operation == "move":
                        await self._process_move(
                            uid,
                            source,
                            record,
                            actor_user_id=actor,
                            is_admin=record.is_admin,
                        )
                    else:
                        await self._process_copy(
                            uid,
                            source,
                            record,
                            actor_user_id=actor,
                            is_admin=record.is_admin,
                        )
                except Exception as error:  # noqa: BLE001 -- per-item isolation
                    logger.exception("transfer item failed: %s", source_id)
                    await self._mark_item_failed(
                        uid, _exception_message(error), name=source.name,
                    )
        except Exception as error:  # noqa: BLE001 -- job-level failure
            logger.exception("transfer job failed: %s", uid)
            return await self._transfers.update(uid, {
                "status": "failed",
                "error": _humanize_transfer_error(_exception_message(error)),
                "finished_at": datetime.now(),
                "current_name": None,
            })

        final = await self._transfers.get(uid)
        assert final is not None  # noqa: S101
        if final.failed_items == 0:
            status = "completed"
        elif final.failed_items == final.done_items:
            status = "failed"
        else:
            status = "partial"
        return await self._transfers.update(uid, {
            "status": status,
            "finished_at": datetime.now(),
            "current_name": None,
        })

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
            await self._copy_tree(
                job_uid,
                source,
                dest_parent_id=record.dest_parent_id,
                conflict=record.conflict,
                actor_user_id=actor_user_id,
                byte_copy=True,
                is_admin=is_admin,
            )
            failed_after = (await self._transfers.get(job_uid)).failed_items
            if failed_after == failed_before:
                await self._media.soft_delete(
                    source.uid, actor_user_id=actor_user_id,
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
        await self._copy_tree(
            job_uid,
            source,
            dest_parent_id=record.dest_parent_id,
            conflict=record.conflict,
            actor_user_id=actor_user_id,
            byte_copy=byte_copy,
            is_admin=is_admin,
        )

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
            for child in await self._media._files.list(  # noqa: SLF001
                parent_id=source.uid,
            ):
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
                except Exception as error:  # noqa: BLE001
                    logger.exception("copy child failed: %s", child.uid)
                    await self._mark_item_failed(
                        job_uid, _exception_message(error), name=child.name,
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
            for child in await self._media._files.list(  # noqa: SLF001
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
            item_conn = await self._media._bound_connection(source.uid)  # noqa: SLF001
        dest_conn = await self._media._bound_connection(dest_parent_id)  # noqa: SLF001
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
        siblings = await self._media._files.list(  # noqa: SLF001
            parent_id=dest_parent_id,
        )
        # Only names the actor can see matter for conflict UX; owned siblings
        # are enough for v1 (dest is always a writable folder of the actor).
        claimed = {
            sibling.name
            for sibling in siblings
            if sibling.owner_id == actor_user_id
        }
        if name not in claimed:
            return name
        if conflict == "skip":
            return None
        return resolve_conflict_name(name, claimed)

    async def _bump_done(
        self, job_uid: str, *, current_name: str | None = None,
    ) -> None:
        current = await self._transfers.get(job_uid)
        assert current is not None  # noqa: S101
        await self._transfers.update(job_uid, {
            "done_items": current.done_items + 1,
            "current_name": current_name,
        })

    async def _mark_item_failed(
        self,
        job_uid: str,
        message: str,
        *,
        name: str | None = None,
    ) -> None:
        current = await self._transfers.get(job_uid)
        assert current is not None  # noqa: S101
        await self._transfers.update(job_uid, {
            "failed_items": current.failed_items + 1,
            "done_items": current.done_items + 1,
            "current_name": name,
            "error": _humanize_transfer_error(message),
        })
