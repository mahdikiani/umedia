"""StorageObject database access."""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import StorageObject
from .schemas import StorageObjectRecord


def _to_record(row: StorageObject) -> StorageObjectRecord:
    return StorageObjectRecord(
        uid=row.uid,
        provider_connection_id=row.provider_connection_id,
        content_reference=row.content_reference,
        provider_parent_ref=row.provider_parent_ref,
        type=row.type,
        name=row.name,
        content_hash=row.content_hash,
        content_type=row.content_type,
        size=row.size,
        metadata=dict(row.object_metadata or {}),
        status=row.status,
        last_seen_at=row.last_seen_at,
        deleted_at=row.deleted_at,
        is_deleted=row.is_deleted,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _prepare(changes: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(changes)
    if "metadata" in prepared:
        prepared["object_metadata"] = prepared.pop("metadata")
    return prepared


class StorageObjectRepository:
    """Persist `StorageObject` rows; returns plain `StorageObjectRecord`s."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, data: dict[str, Any]) -> StorageObjectRecord:
        async with self._session_factory() as session:
            row = StorageObject(last_seen_at=datetime.now(), **_prepare(data))
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _to_record(row)

    async def get(self, uid: str) -> StorageObjectRecord | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(StorageObject).where(StorageObject.uid == uid),
            )
            row = result.scalar_one_or_none()
            return None if row is None else _to_record(row)

    async def get_by_reference(
        self, *, provider_connection_id: str, content_reference: str,
    ) -> StorageObjectRecord | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(StorageObject).where(
                    StorageObject.provider_connection_id == provider_connection_id,
                    StorageObject.content_reference == content_reference,
                ),
            )
            row = result.scalar_one_or_none()
            return None if row is None else _to_record(row)

    async def upsert(self, data: dict[str, Any]) -> StorageObjectRecord:
        """Create-or-refresh by the provider-side identity
        `(provider_connection_id, content_reference)` -- what every
        sync/import pass and every plugin-confirmed write goes through, so
        one remote object can never index twice."""
        async with self._session_factory() as session:
            prepared = _prepare(data)
            result = await session.execute(
                select(StorageObject).where(
                    StorageObject.provider_connection_id
                    == prepared["provider_connection_id"],
                    StorageObject.content_reference
                    == prepared["content_reference"],
                ),
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = StorageObject(last_seen_at=datetime.now(), **prepared)
                session.add(row)
            else:
                for key, value in prepared.items():
                    setattr(row, key, value)
                row.last_seen_at = datetime.now()
                # A re-seen object is alive again even if a previous sweep
                # had soft-deleted it.
                row.is_deleted = False
                row.deleted_at = None
            await session.commit()
            await session.refresh(row)
            return _to_record(row)

    @staticmethod
    def _list_conditions(
        *,
        provider_connection_id: str,
        parent_ref: str | None,
        filter_by_parent: bool,
        include_deleted: bool,
    ) -> list[Any]:
        conditions: list[Any] = [
            StorageObject.provider_connection_id == provider_connection_id,
        ]
        if filter_by_parent:
            conditions.append(StorageObject.provider_parent_ref == parent_ref)
        if not include_deleted:
            conditions.append(StorageObject.is_deleted.is_(False))
        return conditions

    async def list(
        self,
        *,
        provider_connection_id: str,
        parent_ref: str | None = None,
        filter_by_parent: bool = False,
        include_deleted: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[StorageObjectRecord]:
        """All of a connection's objects, optionally narrowed to one
        `provider_parent_ref` level (`filter_by_parent=True` -- a separate
        flag because `parent_ref=None` legitimately means "the provider
        root", not "don't filter"). `limit=None` (the default) keeps the
        full unpaginated list every internal caller relies on."""
        async with self._session_factory() as session:
            query = (
                select(StorageObject)
                .where(*self._list_conditions(
                    provider_connection_id=provider_connection_id,
                    parent_ref=parent_ref,
                    filter_by_parent=filter_by_parent,
                    include_deleted=include_deleted,
                ))
                .order_by(StorageObject.name)
            )
            if limit is not None:
                query = query.limit(limit).offset(offset)
            result = await session.execute(query)
            return [_to_record(row) for row in result.scalars()]

    async def count(
        self,
        *,
        provider_connection_id: str,
        parent_ref: str | None = None,
        filter_by_parent: bool = False,
        include_deleted: bool = False,
    ) -> int:
        """Row count under the exact same filters as `list` -- the
        `total` a paginated browse reports."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(func.count())
                .select_from(StorageObject)
                .where(*self._list_conditions(
                    provider_connection_id=provider_connection_id,
                    parent_ref=parent_ref,
                    filter_by_parent=filter_by_parent,
                    include_deleted=include_deleted,
                )),
            )
            return result.scalar_one()

    async def update(
        self, uid: str, changes: dict[str, Any],
    ) -> StorageObjectRecord:
        async with self._session_factory() as session:
            result = await session.execute(
                select(StorageObject).where(StorageObject.uid == uid),
            )
            row = result.scalar_one()
            for key, value in _prepare(changes).items():
                setattr(row, key, value)
            await session.commit()
            await session.refresh(row)
            return _to_record(row)

    async def soft_delete(self, uid: str) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(StorageObject).where(StorageObject.uid == uid),
            )
            row = result.scalar_one()
            row.is_deleted = True
            row.deleted_at = datetime.now()
            await session.commit()
