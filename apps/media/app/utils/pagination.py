"""Offset/limit page envelope shared by every list-returning service.

One shape for both layers (MediaFile listings/search and the
StorageObject provider-index browse/search): `total` always counts the
full filtered/ranked result set, never just the returned slice, so a
client can render honest progress ("50 of 1,203") and `has_more` follows
from it without a second round trip.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Page[T]:
    items: list[T]
    total: int
    limit: int
    offset: int
    has_more: bool

    @classmethod
    def build(
        cls, items: list[T], *, total: int, limit: int, offset: int,
    ) -> "Page[T]":
        return cls(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + len(items) < total,
        )
