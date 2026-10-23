"""Constants and helpers shared by the MediaFile service modules."""

import asyncio
import dataclasses
import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

PUBLIC_PERMISSIONS = {"none", "read"}

#: Bounds for a temporary presigned link's lifetime (seconds): at least a
#: minute, at most 7 days -- the API schema enforces the same window, the
#: service re-checks so no other caller can mint an eternal link.
TEMPORARY_LINK_MIN_SECONDS = 60
TEMPORARY_LINK_MAX_SECONDS = 7 * 24 * 60 * 60

#: `list_children` scopes -- the Resource layer's set plus `trash`
#: (the recycle bin: the actor's own soft-deleted roots).
LIST_SCOPES = {
    "owned",
    "shared_with_me",
    "shared_by_me",
    "all_visible",
    "starred",
    "trash",
}

#: How long soft-deleted items sit in the trash before the nightly
#: purge job removes them permanently.
TRASH_RETENTION_DAYS = 30

#: The manifest capability that means "this provider has structure worth
#: mirroring into" -- flat providers (e.g. Telegram) don't declare it.
MIRROR_CAPABILITY = "move"

#: "Not passed" sentinel for `update(parent_id=...)` -- `None` is a real
#: value there (move to the library root), so absence needs its own marker.
UNSET: Any = object()


async def _hash(content: bytes) -> str:
    """SHA-256 off the event loop -- same rationale as the Resource
    layer's `_hash`: this single shared loop serves everyone."""
    return await asyncio.to_thread(lambda: hashlib.sha256(content).hexdigest())


async def _hash_stream(chunks: AsyncIterator[bytes]) -> str:
    digest = hashlib.sha256()
    async for chunk in chunks:
        if chunk:
            await asyncio.to_thread(digest.update, chunk)
    return digest.hexdigest()


@dataclasses.dataclass(frozen=True)
class TemporaryLink:
    """A minted SigV4-presigned GET URL for the projected library path.
    `url` is path-absolute; `key_id` is the public id of the access key
    that signed it (never the secret)."""

    url: str
    key_id: str
    expires: int

    @property
    def expires_at(self) -> datetime:
        return datetime.fromtimestamp(self.expires, tz=UTC)
