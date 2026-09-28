"""MediaFile database access.

Every read resolves the primary-StorageObject join described in
`schemas.py` in the same local SQL query -- one round trip, SQLite only,
which is what keeps `GET /files` free of provider I/O
(docs/11-dual-layer-library.md).
"""

import dataclasses
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from apps.storage_objects.models import StorageObject
from utils.pagination import Page

from .models import (
    MediaFile,
    MediaFileObject,
    MediaFileStar,
    MediaFileTemporaryItem,
)
from .permissions import can_read
from .schemas import (
    DEFAULT_CONTENT_TYPE,
    DIRECTORY_CONTENT_TYPE,
    HistoryEntry,
    MediaFileRecord,
)

PRIMARY_ROLE = "primary"

#: The `select(...)` entities every joined read uses.
_JOINED = (MediaFile, StorageObject)


def _joined_select():  # noqa: ANN202 -- SQLAlchemy Select typing adds nothing here
    return (
        select(*_JOINED)
        .outerjoin(
            MediaFileObject,
            and_(
                MediaFileObject.media_file_id == MediaFile.uid,
                MediaFileObject.role == PRIMARY_ROLE,
                MediaFileObject.is_deleted.is_(False),
            ),
        )
        .outerjoin(
            StorageObject,
            StorageObject.uid == MediaFileObject.storage_object_id,
        )
    )


def _browse_order(sort: str, order: str) -> tuple[Any, ...]:
    folders_first = case((MediaFile.type == "folder", 0), else_=1).asc()
    if sort == "name":
        fields = (func.lower(MediaFile.name), MediaFile.uid)
    elif sort == "updated_at":
        fields = (
            MediaFile.updated_at,
            func.lower(MediaFile.name),
            MediaFile.uid,
        )
    elif sort == "type":
        fields = (
            func.coalesce(StorageObject.content_type, ""),
            func.lower(MediaFile.name),
            MediaFile.uid,
        )
    elif sort == "size":
        fields = (
            func.coalesce(StorageObject.size, 0),
            func.lower(MediaFile.name),
            MediaFile.uid,
        )
    else:
        raise ValueError(f"Unsupported file sort: {sort}")
    direction = "asc" if order == "asc" else "desc"
    return (
        folders_first,
        *(getattr(field, direction)() for field in fields),
    )


def _sort_records(
    records: list[MediaFileRecord],
    *,
    sort: str,
    order: str,
) -> list[MediaFileRecord]:
    def key(record: MediaFileRecord) -> tuple[Any, ...]:
        name = record.name.lower()
        if sort == "name":
            return name, record.uid
        if sort == "updated_at":
            return record.updated_at, name, record.uid
        if sort == "type":
            return (
                (name, record.uid)
                if record.type == "folder"
                else (
                    record.content_type or "",
                    name,
                    record.uid,
                )
            )
        if sort == "size":
            return record.size, name, record.uid
        raise ValueError(f"Unsupported file sort: {sort}")

    reverse = order == "desc"
    folders = sorted(
        (record for record in records if record.type == "folder"),
        key=key,
        reverse=reverse,
    )
    files = sorted(
        (record for record in records if record.type != "folder"),
        key=key,
        reverse=reverse,
    )
    return [*folders, *files]


def _to_record(
    row: MediaFile,
    primary: StorageObject | None,
) -> MediaFileRecord:
    if primary is not None:
        derived = {
            "storage_object_uid": primary.uid,
            "provider_connection_id": primary.provider_connection_id,
            "content_reference": primary.content_reference,
            "content_hash": primary.content_hash,
            "content_type": primary.content_type,
            "size": primary.size,
        }
    else:
        derived = {
            "provider_connection_id": row.provider_connection_id,
            "content_type": DIRECTORY_CONTENT_TYPE
            if row.type == "folder"
            else DEFAULT_CONTENT_TYPE,
        }
    return MediaFileRecord(
        uid=row.uid,
        owner_id=row.owner_id,
        type=row.type,
        name=row.name,
        parent_id=row.parent_id,
        metadata=dict(row.file_metadata or {}),
        status=row.status,
        error=row.error,
        public_permission=row.public_permission,
        permissions=[dict(entry) for entry in (row.permissions or [])],
        workspace_id=row.workspace_id,
        access_at=row.access_at,
        deleted_at=row.deleted_at,
        is_deleted=row.is_deleted,
        created_at=row.created_at,
        updated_at=row.updated_at,
        history=[HistoryEntry(**entry) for entry in (row.history or [])],
        **derived,
    )


def _prepare(changes: dict[str, Any]) -> dict[str, Any]:
    """Translate `MediaFileRecord`-shaped changes into `MediaFile` column
    assignments -- derived (join-side) fields are silently dropped, they
    are not columns of this table."""
    prepared = dict(changes)
    if "metadata" in prepared:
        prepared["file_metadata"] = prepared.pop("metadata")
    if "history" in prepared:
        prepared["history"] = [
            dataclasses.asdict(entry) if dataclasses.is_dataclass(entry) else entry
            for entry in prepared["history"]
        ]
    for derived in (
        "storage_object_uid",
        "content_reference",
        "content_hash",
        "content_type",
        "size",
    ):
        prepared.pop(derived, None)
    return prepared


class MediaFileRepository:
    """Persist `MediaFile` rows and their StorageObject links. Returns/
    accepts the plain `MediaFileRecord` shape `MediaFileService` uses."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, data: dict[str, Any]) -> MediaFileRecord:
        async with self._session_factory() as session:
            row = MediaFile(**_prepare(data))
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _to_record(row, None)

    async def get(self, uid: str) -> MediaFileRecord | None:
        """No `is_deleted` filter -- callers that need "not deleted" apply
        it themselves; restore/hard-delete need already-deleted rows."""
        async with self._session_factory() as session:
            result = await session.execute(
                _joined_select().where(MediaFile.uid == uid),
            )
            row = result.first()
            return None if row is None else _to_record(row[0], row[1])

    async def list(
        self,
        *,
        parent_id: str | None,
        include_deleted: bool = False,
        sort: str = "name",
        order: str = "asc",
    ) -> list[MediaFileRecord]:
        async with self._session_factory() as session:
            conditions = [MediaFile.parent_id == parent_id]
            if not include_deleted:
                conditions.append(MediaFile.is_deleted.is_(False))
            result = await session.execute(
                _joined_select()
                .where(*conditions)
                .order_by(*_browse_order(sort, order)),
            )
            return [_to_record(file, obj) for file, obj in result.all()]

    async def list_for_actor(
        self,
        *,
        actor_user_id: str,
        parent_id: str | None = None,
        scope: str = "owned",
        include_deleted: bool = False,
        sort: str = "name",
        order: str = "asc",
        limit: int = 50,
        offset: int = 0,
    ) -> Page[MediaFileRecord]:
        """Actor-visible listing, one scope at a time -- same scopes the
        Resource layer had (`owned`, `shared_with_me`, `shared_by_me`,
        `all_visible`); owner/parent narrowing in SQL, the ACL-entry check
        in Python via the one authoritative `permissions.can_read`.

        Pagination: `owned` needs no Python filter, so its slice and
        `total` come straight from SQL LIMIT/OFFSET + COUNT; every other
        scope filters in Python, so the page is sliced *after* that
        filter -- `total` always counts the fully filtered set.

        `trash` is the recycle bin: the actor's own soft-deleted *roots*
        (see `_trash_roots_query`), flat -- `parent_id` is ignored."""
        if scope == "trash":
            return await self._list_trash(
                actor_user_id=actor_user_id,
                limit=limit,
                offset=offset,
            )
        async with self._session_factory() as session:
            conditions = []
            if scope == "owned":
                conditions.append(MediaFile.owner_id == actor_user_id)
                conditions.append(MediaFile.parent_id == parent_id)
                if not include_deleted:
                    conditions.append(MediaFile.is_deleted.is_(False))
            elif scope == "shared_with_me":
                conditions.append(MediaFile.owner_id != actor_user_id)
                conditions.append(MediaFile.is_deleted.is_(False))
            elif scope == "shared_by_me":
                conditions.append(MediaFile.owner_id == actor_user_id)
                conditions.append(MediaFile.is_deleted.is_(False))
            elif scope == "starred":
                conditions.append(MediaFileStar.user_id == actor_user_id)
                conditions.append(MediaFile.is_deleted.is_(False))
            else:  # all_visible
                conditions.append(MediaFile.parent_id == parent_id)
                conditions.append(MediaFile.is_deleted.is_(False))

            conditions.append(
                or_(
                    MediaFile.parent_id.is_not(None),
                    MediaFile.file_metadata["staging"]
                    .as_boolean()
                    .is_not(True),
                ),
            )

            if scope == "owned":
                total = (
                    await session.execute(
                        select(func.count()).select_from(MediaFile).where(*conditions),
                    )
                ).scalar_one()
                result = await session.execute(
                    _joined_select()
                    .where(*conditions)
                    .order_by(*_browse_order(sort, order))
                    .limit(limit)
                    .offset(offset),
                )
                records = [_to_record(file, obj) for file, obj in result.all()]
                return Page.build(
                    records,
                    total=total,
                    limit=limit,
                    offset=offset,
                )

            query = _joined_select()
            if scope == "starred":
                query = query.join(
                    MediaFileStar,
                    MediaFileStar.media_file_id == MediaFile.uid,
                )
            result = await session.execute(
                query.where(*conditions),
            )
            records = [_to_record(file, obj) for file, obj in result.all()]
        if scope == "shared_by_me":
            visible = [record for record in records if record.permissions]
        else:
            visible = [record for record in records if can_read(record, actor_user_id)]
        visible = _sort_records(visible, sort=sort, order=order)
        return Page.build(
            visible[offset : offset + limit],
            total=len(visible),
            limit=limit,
            offset=offset,
        )

    # ------------------------------------------------------------------
    # Trash (recycle bin)
    # ------------------------------------------------------------------
    # A "trash root" is a deleted row whose deletion isn't just the
    # cascade of a deleted parent: its parent_id is null, or the parent
    # row is gone, or the parent is alive. Cascaded children stay hidden
    # behind their root (and come back with it on restore).

    @staticmethod
    def _trash_roots_query(conditions: "list[Any]") -> tuple[Any, Any]:
        """(joined select, count select) over deleted trash roots that
        also match `conditions`. Both carry the parent self-join the
        root test needs."""
        parent = aliased(MediaFile)
        where = [
            MediaFile.is_deleted.is_(True),
            or_(
                MediaFile.parent_id.is_(None),
                parent.uid.is_(None),
                parent.is_deleted.is_(False),
            ),
            *conditions,
        ]
        rows = (
            _joined_select()
            .outerjoin(parent, parent.uid == MediaFile.parent_id)
            .where(*where)
        )
        count = (
            select(func.count())
            .select_from(MediaFile)
            .outerjoin(parent, parent.uid == MediaFile.parent_id)
            .where(*where)
        )
        return rows, count

    async def _list_trash(
        self,
        *,
        actor_user_id: str,
        limit: int,
        offset: int,
    ) -> Page[MediaFileRecord]:
        """The actor's recycle bin: their own trash roots, newest
        deletion first. Pure SQL filter, so the slice and `total` come
        from LIMIT/OFFSET + COUNT like `owned`."""
        rows, count = self._trash_roots_query(
            [MediaFile.owner_id == actor_user_id],
        )
        async with self._session_factory() as session:
            total = (await session.execute(count)).scalar_one()
            result = await session.execute(
                rows.order_by(MediaFile.deleted_at.desc(), MediaFile.name)
                .limit(limit)
                .offset(offset),
            )
            records = [_to_record(file, obj) for file, obj in result.all()]
        return Page.build(records, total=total, limit=limit, offset=offset)

    async def list_expired_trash(
        self,
        *,
        cutoff: datetime,
    ) -> "list[MediaFileRecord]":
        """Trash roots (every owner's -- this feeds the system purge job,
        not a user view) soft-deleted strictly before `cutoff`."""
        rows, _ = self._trash_roots_query([MediaFile.deleted_at < cutoff])
        async with self._session_factory() as session:
            result = await session.execute(
                rows.order_by(MediaFile.deleted_at),
            )
            return [_to_record(file, obj) for file, obj in result.all()]

    async def set_starred(
        self,
        *,
        actor_user_id: str,
        media_file_uid: str,
        starred: bool,
    ) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(MediaFileStar).where(
                    MediaFileStar.user_id == actor_user_id,
                    MediaFileStar.media_file_id == media_file_uid,
                ),
            )
            row = result.scalar_one_or_none()
            if starred and row is None:
                session.add(
                    MediaFileStar(
                        user_id=actor_user_id,
                        media_file_id=media_file_uid,
                    )
                )
            elif not starred and row is not None:
                await session.delete(row)
            await session.commit()

    async def starred_ids(
        self,
        *,
        actor_user_id: str,
        media_file_uids: "list[str]",
    ) -> set[str]:
        if not media_file_uids:
            return set()
        async with self._session_factory() as session:
            result = await session.execute(
                select(MediaFileStar.media_file_id).where(
                    MediaFileStar.user_id == actor_user_id,
                    MediaFileStar.media_file_id.in_(media_file_uids),
                ),
            )
            return set(result.scalars().all())

    async def add_temporary_item(
        self,
        *,
        actor_user_id: str,
        media_file_uid: str,
    ) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                select(MediaFileTemporaryItem).where(
                    MediaFileTemporaryItem.user_id == actor_user_id,
                    MediaFileTemporaryItem.media_file_id == media_file_uid,
                ),
            )
            if result.scalar_one_or_none() is not None:
                return False
            session.add(
                MediaFileTemporaryItem(
                    user_id=actor_user_id,
                    media_file_id=media_file_uid,
                ),
            )
            await session.commit()
            return True

    async def list_temporary_items(
        self,
        *,
        actor_user_id: str,
    ) -> "list[MediaFileRecord]":
        async with self._session_factory() as session:
            result = await session.execute(
                _joined_select()
                .join(
                    MediaFileTemporaryItem,
                    MediaFileTemporaryItem.media_file_id == MediaFile.uid,
                )
                .where(
                    MediaFileTemporaryItem.user_id == actor_user_id,
                    MediaFileTemporaryItem.is_deleted.is_(False),
                    MediaFile.is_deleted.is_(False),
                )
                .order_by(
                    MediaFileTemporaryItem.created_at,
                    MediaFileTemporaryItem.uid,
                ),
            )
            return [_to_record(file, obj) for file, obj in result.all()]

    async def remove_temporary_item(
        self,
        *,
        actor_user_id: str,
        media_file_uid: str,
    ) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(MediaFileTemporaryItem).where(
                    MediaFileTemporaryItem.user_id == actor_user_id,
                    MediaFileTemporaryItem.media_file_id == media_file_uid,
                ),
            )
            row = result.scalar_one_or_none()
            if row is not None:
                await session.delete(row)
                await session.commit()

    async def clear_temporary_items(self, *, actor_user_id: str) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(MediaFileTemporaryItem).where(
                    MediaFileTemporaryItem.user_id == actor_user_id,
                ),
            )
            for row in result.scalars():
                await session.delete(row)
            await session.commit()

    async def list_visible(
        self,
        *,
        actor_user_id: str,
    ) -> "list[MediaFileRecord]":
        async with self._session_factory() as session:
            result = await session.execute(
                _joined_select().where(MediaFile.is_deleted.is_(False)),
            )
            records = [_to_record(file, obj) for file, obj in result.all()]
        return [record for record in records if can_read(record, actor_user_id)]

    async def update(self, uid: str, changes: dict[str, Any]) -> MediaFileRecord:
        async with self._session_factory() as session:
            result = await session.execute(
                select(MediaFile).where(MediaFile.uid == uid),
            )
            row = result.scalar_one()
            for key, value in _prepare(changes).items():
                setattr(row, key, value)
            await session.commit()
        record = await self.get(uid)
        assert record is not None  # noqa: S101 -- just updated it
        return record

    async def touch_access(self, uid: str) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(MediaFile).where(MediaFile.uid == uid),
            )
            row = result.scalar_one()
            row.access_at = datetime.now()
            await session.commit()

    async def volume_stats(self, *, actor_user_id: str) -> dict[str, int]:
        """Owned, non-deleted library usage -- bytes from the primary
        StorageObject, counts from MediaFile rows."""
        owned_live = and_(
            MediaFile.owner_id == actor_user_id,
            MediaFile.is_deleted.is_(False),
        )
        async with self._session_factory() as session:
            used_bytes = await session.scalar(
                select(func.coalesce(func.sum(StorageObject.size), 0))
                .select_from(MediaFile)
                .outerjoin(
                    MediaFileObject,
                    and_(
                        MediaFileObject.media_file_id == MediaFile.uid,
                        MediaFileObject.role == PRIMARY_ROLE,
                        MediaFileObject.is_deleted.is_(False),
                    ),
                )
                .outerjoin(
                    StorageObject,
                    StorageObject.uid == MediaFileObject.storage_object_id,
                )
                .where(owned_live, MediaFile.type == "file"),
            )
            file_count = await session.scalar(
                select(func.count())
                .select_from(MediaFile)
                .where(owned_live, MediaFile.type == "file"),
            )
            folder_count = await session.scalar(
                select(func.count())
                .select_from(MediaFile)
                .where(owned_live, MediaFile.type == "folder"),
            )
        return {
            "used_bytes": int(used_bytes or 0),
            "file_count": int(file_count or 0),
            "folder_count": int(folder_count or 0),
        }

    async def soft_delete(self, uid: str) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(MediaFile).where(MediaFile.uid == uid),
            )
            row = result.scalar_one()
            row.is_deleted = True
            row.deleted_at = datetime.now()
            await session.commit()

    async def soft_delete_many(self, uids: Sequence[str]) -> None:
        if not uids:
            return
        async with self._session_factory() as session:
            await session.execute(
                update(MediaFile)
                .where(MediaFile.uid.in_(uids), MediaFile.is_deleted.is_(False))
                .values(is_deleted=True, deleted_at=datetime.now()),
            )
            await session.commit()

    async def restore(self, uid: str) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(MediaFile).where(MediaFile.uid == uid),
            )
            row = result.scalar_one()
            row.is_deleted = False
            row.deleted_at = None
            await session.commit()

    async def hard_delete(self, uid: str) -> None:
        async with self._session_factory() as session:
            links = await session.execute(
                select(MediaFileObject).where(
                    MediaFileObject.media_file_id == uid,
                ),
            )
            for link in links.scalars():
                await session.delete(link)
            result = await session.execute(
                select(MediaFile).where(MediaFile.uid == uid),
            )
            row = result.scalar_one_or_none()
            if row is not None:
                await session.delete(row)
            await session.commit()

    # ------------------------------------------------------------------
    # Links
    # ------------------------------------------------------------------

    async def link_object(
        self,
        *,
        media_file_uid: str,
        storage_object_uid: str,
        role: str = PRIMARY_ROLE,
    ) -> None:
        async with self._session_factory() as session:
            session.add(
                MediaFileObject(
                    media_file_id=media_file_uid,
                    storage_object_id=storage_object_uid,
                    role=role,
                )
            )
            await session.commit()

    async def get_by_storage_object(
        self,
        storage_object_uid: str,
    ) -> MediaFileRecord | None:
        """The first MediaFile linked to this object, if any -- how import
        runs stay idempotent (an already-imported object is never
        re-created). Same-storage copies may share one StorageObject
        across multiple MediaFiles; any link is enough to skip re-import.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(MediaFileObject)
                .where(
                    MediaFileObject.storage_object_id == storage_object_uid,
                    MediaFileObject.is_deleted.is_(False),
                )
                .order_by(MediaFileObject.created_at.asc()),
            )
            link = result.scalars().first()
        if link is None:
            return None
        return await self.get(link.media_file_id)

    async def list_uids_by_storage_object(
        self,
        storage_object_uid: str,
    ) -> "list[str]":
        async with self._session_factory() as session:
            result = await session.execute(
                select(MediaFileObject.media_file_id).where(
                    MediaFileObject.storage_object_id == storage_object_uid,
                    MediaFileObject.is_deleted.is_(False),
                ),
            )
            return list(result.scalars())

    async def list_for_connection(
        self,
        provider_connection_id: str,
    ) -> "list[MediaFileRecord]":
        """Live (not soft-deleted) MediaFiles bound to this connection via
        the denormalized `provider_connection_id` column -- folders always,
        files as a placement hint. Callers that also need StorageObject-
        linked rows union that set themselves (see
        `MediaFileService.soft_delete_for_connection`)."""
        async with self._session_factory() as session:
            result = await session.execute(
                _joined_select().where(
                    MediaFile.provider_connection_id == provider_connection_id,
                    MediaFile.is_deleted.is_(False),
                ),
            )
            return [_to_record(file, obj) for file, obj in result.all()]

    async def find_import_root(
        self,
        *,
        provider_connection_id: str,
        owner_id: str | None = None,
    ) -> MediaFileRecord | None:
        """The connection's library root folder, if one exists -- marked
        with `metadata.import_root` at creation. `owner_id` narrows the
        search; omit it to find the root regardless of owner (the inbound
        poller must not create a second root under a different admin).
        The metadata check runs in Python over root folders (SQLite JSON
        querying buys nothing at this row count)."""
        async with self._session_factory() as session:
            conditions = [
                MediaFile.parent_id.is_(None),
                MediaFile.type == "folder",
                MediaFile.is_deleted.is_(False),
            ]
            if owner_id is not None:
                conditions.append(MediaFile.owner_id == owner_id)
            result = await session.execute(
                _joined_select().where(*conditions),
            )
            for file, obj in result.all():
                record = _to_record(file, obj)
                if record.metadata.get("import_root") == provider_connection_id:
                    return record
        return None
