"""Persistence for library transfer jobs."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select

from .models import LibraryTransfer


@dataclass
class TransferRecord:
    uid: str
    owner_id: str
    operation: str
    status: str
    source_ids: list[str]
    dest_parent_id: str | None
    conflict: str
    total_items: int
    done_items: int
    failed_items: int
    current_name: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    is_admin: bool = False

    @property
    def progress_pct(self) -> int:
        if self.total_items <= 0:
            return 100 if self.status in {"completed", "partial", "failed"} else 0
        return min(100, (100 * self.done_items) // self.total_items)


def _to_record(row: LibraryTransfer) -> TransferRecord:
    meta = row.meta_data if isinstance(row.meta_data, dict) else {}
    return TransferRecord(
        uid=row.uid,
        owner_id=row.owner_id,
        operation=row.operation,
        status=row.status,
        source_ids=list(row.source_ids or []),
        dest_parent_id=row.dest_parent_id,
        conflict=row.conflict,
        total_items=row.total_items,
        done_items=row.done_items,
        failed_items=row.failed_items,
        current_name=row.current_name,
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        is_admin=bool(meta.get("is_admin", False)),
    )


class TransferRepository:
    def __init__(self, session_factory: object) -> None:
        self._session_factory = session_factory

    async def create(self, data: dict[str, Any]) -> TransferRecord:
        async with self._session_factory() as session:
            row = LibraryTransfer(
                owner_id=data["owner_id"],
                operation=data["operation"],
                status=data.get("status", "queued"),
                source_ids=list(data["source_ids"]),
                dest_parent_id=data.get("dest_parent_id"),
                conflict=data.get("conflict", "rename"),
                total_items=int(data.get("total_items", 0)),
                done_items=int(data.get("done_items", 0)),
                failed_items=int(data.get("failed_items", 0)),
                current_name=data.get("current_name"),
                error=data.get("error"),
                started_at=data.get("started_at"),
                finished_at=data.get("finished_at"),
                meta_data=data.get("meta_data"),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _to_record(row)

    async def get(self, uid: str) -> TransferRecord | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(LibraryTransfer).where(
                    LibraryTransfer.uid == uid,
                    LibraryTransfer.is_deleted.is_(False),
                ),
            )
            row = result.scalar_one_or_none()
        return None if row is None else _to_record(row)

    async def list_for_owner(
        self, *, owner_id: str, limit: int = 50,
    ) -> list[TransferRecord]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(LibraryTransfer)
                .where(
                    LibraryTransfer.owner_id == owner_id,
                    LibraryTransfer.is_deleted.is_(False),
                )
                .order_by(LibraryTransfer.created_at.desc())
                .limit(limit),
            )
            return [_to_record(row) for row in result.scalars().all()]

    async def update(self, uid: str, changes: dict[str, Any]) -> TransferRecord:
        async with self._session_factory() as session:
            result = await session.execute(
                select(LibraryTransfer).where(LibraryTransfer.uid == uid),
            )
            row = result.scalar_one()
            for key, value in changes.items():
                setattr(row, key, value)
            await session.commit()
            await session.refresh(row)
            return _to_record(row)
