from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import OperationNotification


@dataclass(frozen=True)
class NotificationRecord:
    uid: str
    owner_id: str
    operation: str
    item_name: str
    error: str
    source_type: str
    source_uid: str
    read_at: datetime | None
    created_at: datetime


def _record(row: OperationNotification) -> NotificationRecord:
    return NotificationRecord(
        uid=row.uid,
        owner_id=row.owner_id,
        operation=row.operation,
        item_name=row.item_name,
        error=row.error,
        source_type=row.source_type,
        source_uid=row.source_uid,
        read_at=row.read_at,
        created_at=row.created_at,
    )


class NotificationRepository:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def create_once(self, data: dict[str, Any]) -> NotificationRecord:
        async with self._session_factory() as session:
            existing = await session.scalar(
                select(OperationNotification).where(
                    OperationNotification.owner_id == data["owner_id"],
                    OperationNotification.source_type == data["source_type"],
                    OperationNotification.source_uid == data["source_uid"],
                    OperationNotification.is_deleted.is_(False),
                ),
            )
            if existing is not None:
                return _record(existing)
            row = OperationNotification(**data)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _record(row)

    async def list_for_owner(self, owner_id: str) -> list[NotificationRecord]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(OperationNotification)
                .where(
                    OperationNotification.owner_id == owner_id,
                    OperationNotification.is_deleted.is_(False),
                )
                .order_by(OperationNotification.created_at.desc())
                .limit(100),
            )
            return [_record(row) for row in rows.all()]

    async def mark_read(self, uid: str, owner_id: str) -> NotificationRecord | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(OperationNotification).where(
                    OperationNotification.uid == uid,
                    OperationNotification.owner_id == owner_id,
                    OperationNotification.is_deleted.is_(False),
                ),
            )
            if row is None:
                return None
            row.read_at = row.read_at or datetime.now()
            await session.commit()
            await session.refresh(row)
            return _record(row)


class NotificationService:
    def __init__(self, repository: NotificationRepository) -> None:
        self._repository = repository

    async def record_failure(
        self,
        *,
        owner_id: str,
        operation: str,
        item_name: str,
        error: str,
        source_type: str,
        source_uid: str,
    ) -> NotificationRecord:
        return await self._repository.create_once({
            "owner_id": owner_id,
            "operation": operation,
            "item_name": item_name,
            "error": error,
            "source_type": source_type,
            "source_uid": source_uid,
            "read_at": None,
        })

    async def list_for_user(self, owner_id: str) -> list[NotificationRecord]:
        return await self._repository.list_for_owner(owner_id)

    async def mark_read(self, uid: str, owner_id: str) -> NotificationRecord | None:
        return await self._repository.mark_read(uid, owner_id)
