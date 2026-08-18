"""Resource database access."""

import dataclasses
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import Resource
from .permissions import can_read
from .schemas import HistoryEntry, ResourceRecord


def _to_record(row: Resource) -> ResourceRecord:
    return ResourceRecord(
        uid=row.uid,
        owner_id=row.owner_id,
        provider_connection_id=row.provider_connection_id,
        type=row.type,
        name=row.name,
        parent_id=row.parent_id,
        metadata=dict(row.resource_metadata or {}),
        content_reference=row.content_reference,
        content_hash=row.content_hash,
        content_type=row.content_type,
        size=row.size,
        status=row.status,
        error=row.error,
        public_permission=row.public_permission,
        access_at=row.access_at,
        deleted_at=row.deleted_at,
        is_deleted=row.is_deleted,
        created_at=row.created_at,
        updated_at=row.updated_at,
        history=[HistoryEntry(**entry) for entry in (row.history or [])],
        permissions=[dict(entry) for entry in (row.permissions or [])],
        workspace_id=row.workspace_id,
    )


def _prepare(changes: dict[str, Any]) -> dict[str, Any]:
    """Translate `ResourceRecord`-shaped changes into `Resource` column
    assignments -- `metadata` -> `resource_metadata`, `HistoryEntry`
    dataclasses -> plain dicts for JSON storage."""
    prepared = dict(changes)
    if "metadata" in prepared:
        prepared["resource_metadata"] = prepared.pop("metadata")
    if "history" in prepared:
        prepared["history"] = [
            dataclasses.asdict(entry) if dataclasses.is_dataclass(entry) else entry
            for entry in prepared["history"]
        ]
    return prepared


class ResourceRepository:
    """Persist `Resource` rows. Returns/accepts the plain `ResourceRecord`
    shape `ResourceService` depends on -- see `apps/resources/services.py`.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, data: dict[str, Any]) -> ResourceRecord:
        async with self._session_factory() as session:
            row = Resource(**_prepare(data))
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _to_record(row)

    async def get(self, uid: str) -> ResourceRecord | None:
        """No `is_deleted` filter -- callers that need "not deleted" apply
        it themselves (`ResourceService.get()`); `hard_delete`/`restore`
        specifically need to fetch an already-deleted row."""
        async with self._session_factory() as session:
            result = await session.execute(select(Resource).where(Resource.uid == uid))
            row = result.scalar_one_or_none()
            return None if row is None else _to_record(row)

    async def list(
        self, *, parent_id: str | None, include_deleted: bool = False,
    ) -> list[ResourceRecord]:
        async with self._session_factory() as session:
            conditions = [Resource.parent_id == parent_id]
            if not include_deleted:
                conditions.append(Resource.is_deleted.is_(False))
            result = await session.execute(
                select(Resource).where(*conditions).order_by(Resource.name),
            )
            return [_to_record(row) for row in result.scalars()]

    async def list_for_actor(
        self,
        *,
        actor_user_id: str,
        parent_id: str | None = None,
        scope: str = "owned",
        include_deleted: bool = False,
        # Quoted: in this class body the name `list` is the `list()`
        # *method* above, not the builtin.
    ) -> "list[ResourceRecord]":
        """Actor-visible listing, one scope at a time (docs/05-api-design.md
        `GET /resources?scope=`):

        - `owned`: the actor's own children of `parent_id` (the only scope
          where `include_deleted` -- trash -- applies; enforced upstream by
          `ResourceService.list_children`).
        - `shared_with_me`: flat, parent-agnostic list of other users'
          resources carrying an ACL entry for the actor.
        - `shared_by_me`: flat list of the actor's resources that have
          outgoing ACL entries.
        - `all_visible`: children of `parent_id` the actor may READ
          (owned + shared), for unified folder browsing.

        Owner/parent narrowing happens in SQL; the ACL-entry check runs in
        Python via `permissions.can_read` -- SQLite JSON queries would
        duplicate (and eventually contradict) the one authoritative
        implementation of the permission ladder for no practical gain at
        this row count.
        """
        async with self._session_factory() as session:
            conditions = []
            if scope == "owned":
                conditions.append(Resource.owner_id == actor_user_id)
                conditions.append(Resource.parent_id == parent_id)
                if not include_deleted:
                    conditions.append(Resource.is_deleted.is_(False))
            elif scope == "shared_with_me":
                conditions.append(Resource.owner_id != actor_user_id)
                conditions.append(Resource.is_deleted.is_(False))
            elif scope == "shared_by_me":
                conditions.append(Resource.owner_id == actor_user_id)
                conditions.append(Resource.is_deleted.is_(False))
            else:  # all_visible
                conditions.append(Resource.parent_id == parent_id)
                conditions.append(Resource.is_deleted.is_(False))
            result = await session.execute(
                select(Resource).where(*conditions).order_by(Resource.name),
            )
            records = [_to_record(row) for row in result.scalars()]
        if scope in {"owned", "shared_by_me"}:
            # Already owner-filtered in SQL; `shared_by_me` additionally
            # means "has at least one outgoing grant".
            if scope == "shared_by_me":
                return [record for record in records if record.permissions]
            return records
        return [
            record for record in records if can_read(record, actor_user_id)
        ]

    async def find_by_content_hash(
        self,
        *,
        provider_connection_id: str,
        parent_id: str | None,
        content_hash: str,
        owner_id: str,
    ) -> ResourceRecord | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(Resource).where(
                    Resource.provider_connection_id == provider_connection_id,
                    Resource.parent_id == parent_id,
                    Resource.content_hash == content_hash,
                    Resource.owner_id == owner_id,
                    Resource.is_deleted.is_(False),
                ),
            )
            row = result.scalar_one_or_none()
            return None if row is None else _to_record(row)

    async def update(self, uid: str, changes: dict[str, Any]) -> ResourceRecord:
        async with self._session_factory() as session:
            result = await session.execute(select(Resource).where(Resource.uid == uid))
            row = result.scalar_one()
            for key, value in _prepare(changes).items():
                setattr(row, key, value)
            await session.commit()
            await session.refresh(row)
            return _to_record(row)

    async def touch_access(self, uid: str) -> None:
        async with self._session_factory() as session:
            result = await session.execute(select(Resource).where(Resource.uid == uid))
            row = result.scalar_one()
            row.access_at = datetime.now()
            await session.commit()

    async def soft_delete(self, uid: str) -> None:
        async with self._session_factory() as session:
            result = await session.execute(select(Resource).where(Resource.uid == uid))
            row = result.scalar_one()
            row.is_deleted = True
            row.deleted_at = datetime.now()
            await session.commit()

    async def hard_delete(self, uid: str) -> None:
        async with self._session_factory() as session:
            result = await session.execute(select(Resource).where(Resource.uid == uid))
            row = result.scalar_one_or_none()
            if row is not None:
                await session.delete(row)
                await session.commit()

    async def restore(self, uid: str) -> None:
        async with self._session_factory() as session:
            result = await session.execute(select(Resource).where(Resource.uid == uid))
            row = result.scalar_one()
            row.is_deleted = False
            row.deleted_at = None
            await session.commit()

    async def volume_stats(self) -> dict[str, int]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(
                    Resource.is_deleted,
                    func.coalesce(func.sum(Resource.size), 0),
                    func.count(),
                ).group_by(Resource.is_deleted),
            )
            stats = {
                "active_size": 0, "active_count": 0,
                "deleted_size": 0, "deleted_count": 0,
            }
            for is_deleted, total_size, count in result.all():
                prefix = "deleted" if is_deleted else "active"
                stats[f"{prefix}_size"] = int(total_size)
                stats[f"{prefix}_count"] = int(count)
            return stats
