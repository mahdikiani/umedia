from typing import Protocol

from utils.fuzzy import fuzzy_score
from utils.pagination import Page

from .schemas import MediaFileRecord

DIRECT_CHILD_BOOST = 48
DESCENDANT_BOOST = 24
DEFAULT_SEARCH_LIMIT = 50


class SearchRepositoryProtocol(Protocol):
    async def list_visible(
        self, *, actor_user_id: str,
    ) -> list[MediaFileRecord]: ...


class MediaFileSearchMixin:
    _files: SearchRepositoryProtocol

    async def search(
        self,
        query: str,
        *,
        actor_user_id: str,
        under_parent_id: str | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
        offset: int = 0,
    ) -> Page[MediaFileRecord]:
        normalized_query = query.strip()
        if not normalized_query or limit <= 0:
            return Page.build([], total=0, limit=limit, offset=offset)

        candidates = await self._files.list_visible(actor_user_id=actor_user_id)
        parent_by_uid = {record.uid: record.parent_id for record in candidates}
        ranked: list[tuple[int, int, str, str, MediaFileRecord]] = []

        for record in candidates:
            score = fuzzy_score(normalized_query, record.name)
            if score is None:
                continue

            context_boost = 0
            if under_parent_id is not None:
                if record.parent_id == under_parent_id:
                    context_boost = DIRECT_CHILD_BOOST
                else:
                    cursor = record.parent_id
                    visited: set[str] = set()
                    while cursor is not None and cursor not in visited:
                        if cursor == under_parent_id:
                            context_boost = DESCENDANT_BOOST
                            break
                        visited.add(cursor)
                        cursor = parent_by_uid.get(cursor)

            ranked.append((
                score + context_boost,
                score,
                record.name.casefold(),
                record.uid,
                record,
            ))

        ranked.sort(key=lambda entry: (-entry[0], -entry[1], entry[2], entry[3]))
        # Page over the *full* ranked match set -- `total` reports every
        # match, not just this slice.
        matches = [entry[4] for entry in ranked]
        return Page.build(
            matches[offset:offset + limit],
            total=len(matches),
            limit=limit,
            offset=offset,
        )
