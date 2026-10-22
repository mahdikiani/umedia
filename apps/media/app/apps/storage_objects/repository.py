"""StorageObject database access."""

import builtins
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select, update
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
        list_error=row.list_error,
        list_error_status=row.list_error_status,
        list_error_at=row.list_error_at,
        hash_error=row.hash_error,
        hash_error_at=row.hash_error_at,
    )


def _prepare(changes: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(changes)
    if "metadata" in prepared:
        prepared["object_metadata"] = prepared.pop("metadata")
    return prepared


def _content_changed(row: StorageObject, prepared: dict[str, Any]) -> bool:
    """Whether an upsert says the bytes changed at the provider: a new
    size or a new `mtime`. A recorded `hash_error` is then stale -- the
    file is worth reading again."""
    if "size" in prepared and (prepared["size"] or 0) != (row.size or 0):
        return True
    if "object_metadata" not in prepared:
        return False
    old_mtime = (row.object_metadata or {}).get("mtime")
    return (prepared["object_metadata"] or {}).get("mtime") != old_mtime


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
                # Decided on the row itself, not on a copy the caller read
                # earlier: the hash backfill may have recorded the error in
                # between, against the old size.
                hash_error_stale = (
                    prepared.get("content_hash") is not None
                    or _content_changed(row, prepared)
                )
                for key, value in prepared.items():
                    setattr(row, key, value)
                if hash_error_stale and "hash_error" not in prepared:
                    row.hash_error = None
                    row.hash_error_at = None
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

    async def list_missing_hashes(
        self,
        *,
        provider_connection_id: str,
        after_uid: str | None = None,
        limit: int = 100,
        hash_error_before: datetime | None = None,
    ) -> builtins.list[StorageObjectRecord]:
        """Return a bounded, stable page of active files without hashes.

        Files with a recorded `hash_error` are left out, unless the error
        is older than `hash_error_before` (worth one more try).
        """
        unreadable_skipped = (
            StorageObject.hash_error_at.is_(None)
            if hash_error_before is None
            else or_(
                StorageObject.hash_error_at.is_(None),
                StorageObject.hash_error_at < hash_error_before,
            )
        )
        async with self._session_factory() as session:
            query = select(StorageObject).where(
                StorageObject.provider_connection_id == provider_connection_id,
                StorageObject.type == "file",
                StorageObject.content_hash.is_(None),
                StorageObject.status == "active",
                StorageObject.is_deleted.is_(False),
                unreadable_skipped,
            )
            if after_uid is not None:
                query = query.where(StorageObject.uid > after_uid)
            result = await session.execute(
                query.order_by(StorageObject.uid).limit(limit),
            )
            return [_to_record(row) for row in result.scalars()]

    async def set_content_hash_if_unchanged(
        self,
        *,
        uid: str,
        expected_size: int,
        expected_metadata: dict[str, Any],
        content_hash: str,
    ) -> bool:
        """Set a pending hash only if sync has not refreshed the object."""
        async with self._session_factory() as session:
            result = await session.execute(
                update(StorageObject)
                .where(
                    StorageObject.uid == uid,
                    StorageObject.content_hash.is_(None),
                    StorageObject.status == "active",
                    StorageObject.is_deleted.is_(False),
                    StorageObject.size == expected_size,
                    StorageObject.object_metadata == expected_metadata,
                )
                .values(content_hash=content_hash, hash_error=None, hash_error_at=None),
            )
            await session.commit()
            return result.rowcount == 1

    async def set_hash_error_if_unchanged(
        self,
        *,
        uid: str,
        expected_size: int,
        expected_metadata: dict[str, Any],
        error: str,
    ) -> bool:
        """Record why a file's content cannot be read -- only if sync has
        not refreshed the object meanwhile (same guard as the hash)."""
        async with self._session_factory() as session:
            result = await session.execute(
                update(StorageObject)
                .where(
                    StorageObject.uid == uid,
                    StorageObject.content_hash.is_(None),
                    StorageObject.size == expected_size,
                    StorageObject.object_metadata == expected_metadata,
                )
                .values(hash_error=error[:2000], hash_error_at=datetime.now()),
            )
            await session.commit()
            return result.rowcount == 1

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
