"""StorageObject read-side business rules.

Deliberately thin: the physical index is written exclusively by
`MediaFileService` (uploads and import/sync -- see apps/media_files),
which owns the cross-layer invariants. This service exists so the
provider-index browse route (`GET /providers/{uid}/objects`) has a
service to call instead of a repository -- no business logic in routes.
"""

from typing import Any, Protocol

from utils.fuzzy import fuzzy_score
from utils.pagination import Page

from .schemas import StorageObjectRecord


class StorageObjectRepositoryProtocol(Protocol):
    async def list(
        self,
        *,
        provider_connection_id: str,
        parent_ref: str | None = None,
        filter_by_parent: bool = False,
        include_deleted: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[StorageObjectRecord]: ...
    async def count(
        self,
        *,
        provider_connection_id: str,
        parent_ref: str | None = None,
        filter_by_parent: bool = False,
        include_deleted: bool = False,
    ) -> int: ...
    async def get(self, uid: str) -> StorageObjectRecord | None: ...
    async def upsert(self, data: dict[str, Any]) -> StorageObjectRecord: ...


class StorageObjectService:
    def __init__(self, repository: StorageObjectRepositoryProtocol) -> None:
        self._repository = repository

    async def list_objects(
        self,
        provider_connection_id: str,
        *,
        parent_ref: str | None = None,
        filter_by_parent: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[StorageObjectRecord]:
        """Browse one connection's index -- from SQLite, never a live
        provider call (that's what `POST /providers/{uid}/sync` is for).
        The slice and its `total` both come from SQL, same filters."""
        items = await self._repository.list(
            provider_connection_id=provider_connection_id,
            parent_ref=parent_ref,
            filter_by_parent=filter_by_parent,
            limit=limit,
            offset=offset,
        )
        total = await self._repository.count(
            provider_connection_id=provider_connection_id,
            parent_ref=parent_ref,
            filter_by_parent=filter_by_parent,
        )
        return Page.build(items, total=total, limit=limit, offset=offset)

    async def search(
        self,
        provider_connection_id: str,
        *,
        query: str,
        parent_ref: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[StorageObjectRecord]:
        normalized_query = query.strip()
        if not normalized_query:
            return Page.build([], total=0, limit=limit, offset=offset)

        candidates = await self._repository.list(
            provider_connection_id=provider_connection_id,
        )
        if parent_ref is not None:
            prefix = f"{parent_ref}/"
            candidates = [
                record
                for record in candidates
                if record.provider_parent_ref == parent_ref
                or record.content_reference.startswith(prefix)
            ]

        ranked = [
            (score, record.name.casefold(), record.content_reference,
             record.uid, record)
            for record in candidates
            if (score := fuzzy_score(normalized_query, record.name)) is not None
        ]
        ranked.sort(key=lambda entry: (-entry[0], entry[1], entry[2], entry[3]))
        # `total` covers the full ranked match set, not only this slice.
        matches = [entry[4] for entry in ranked]
        return Page.build(
            matches[offset:offset + limit],
            total=len(matches),
            limit=limit,
            offset=offset,
        )
