"""Scheduled maintenance for the MediaFile library.

`purge_expired_trash` is the nightly APScheduler job body (wired into
`server/server.py`'s lifespan, cron 00:00 Asia/Tehran): permanently
remove trash roots soft-deleted more than
`services.TRASH_RETENTION_DAYS` days ago.

Purging is library-only (doc 11's v1 rule -- StorageObjects and the
provider's bytes always survive), so the service is assembled from
nothing but the session factory; the plugin gateway and connection
lookup are stubbed with loud placeholders because a purge reaching for
either would be a bug, not a missing dependency.
"""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .repository import MediaFileRepository
from .services import TRASH_RETENTION_DAYS, MediaFileService

logger = logging.getLogger(__name__)


class _Unused:
    """A `MediaFileService` dependency the purge job must never touch."""

    def __init__(self, label: str) -> None:
        self._label = label

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        msg = f"the trash purge job must not use {self._label} ({name})"
        raise RuntimeError(msg)


async def purge_expired_trash(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Run one purge pass; returns the number of trash roots removed."""
    service = MediaFileService(
        MediaFileRepository(session_factory),
        _Unused("the storage-object repository"),
        _Unused("the plugin gateway"),
        _Unused("the provider-connection repository"),
    )
    removed = await service.purge_expired_trash(
        older_than_days=TRASH_RETENTION_DAYS,
    )
    logger.info(
        "Trash purge finished: %d expired item(s) permanently removed",
        removed,
    )
    return removed
