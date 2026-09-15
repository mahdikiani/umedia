"""Scheduled maintenance for the MediaFile library.

`purge_expired_trash` is the nightly APScheduler job body (wired into
`server/server.py`'s lifespan, cron 00:00 Asia/Tehran): permanently
remove trash roots soft-deleted more than
`services.TRASH_RETENTION_DAYS` days ago.

`poll_inbound_syncs` is the interval job body: walk every enabled
provider connection and run the same reconcile as
`POST /providers/{uid}/sync`. Sequential on purpose (SQLite; one plugin
walk at a time). A failed provider is logged and skipped so one outage
cannot abort the tick. Incomplete walks never mark objects missing --
`import_from_provider` raises before that step.

Purging is library-only (doc 11's v1 rule -- StorageObjects and the
provider's bytes always survive), so the purge service is assembled from
nothing but the session factory; the plugin gateway and connection
lookup are stubbed with loud placeholders because a purge reaching for
either would be a bug, not a missing dependency.
"""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.provider_connections.repository import ProviderConnectionRepository

from .factory import run_inbound_sync
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


def _state(app_or_state: Any) -> Any:  # noqa: ANN401
    return getattr(app_or_state, "state", app_or_state)


async def _poll_actor(
    state: Any,  # noqa: ANN401
    files: MediaFileRepository,
    connection_uid: str,
) -> str | None:
    """Import-root owner (any owner) so polling cannot fork a second
    connection folder; otherwise the first active admin."""
    root = await files.find_import_root(provider_connection_id=connection_uid)
    if root is not None:
        return root.owner_id
    auth = getattr(state, "auth_service", None)
    if auth is None:
        return None
    for user in await auth.list_users():
        roles = getattr(user, "roles", None) or []
        if getattr(user, "is_active", False) and "admin" in roles:
            return user.uid
    return None


async def poll_inbound_syncs(app_or_state: Any) -> int:  # noqa: ANN401
    """Reconcile every enabled connection that is not already walking.

    Returns the count of connections whose reconcile finished without
    raising. Shares `app.state.active_syncs` with the manual POST so
    `GET /providers/{uid}/sync` stays truthful and a 409 still fires
    if a click overlaps a poll tick.
    """
    state = _state(app_or_state)
    active_syncs = getattr(state, "active_syncs", None)
    if active_syncs is None:
        active_syncs = set()
        state.active_syncs = active_syncs

    connections = ProviderConnectionRepository(state.session_factory)
    files = MediaFileRepository(state.session_factory)
    reconciled = 0
    for connection in await connections.list():
        if not connection.enabled or connection.uid in active_syncs:
            continue
        # Claim the lock before any await so POST 409 / GET running stay
        # truthful even while we resolve the actor.
        active_syncs.add(connection.uid)
        try:
            actor_id = await _poll_actor(state, files, connection.uid)
            if actor_id is None:
                logger.warning(
                    "Skipping inbound sync for '%s': no import-root owner "
                    "or active admin",
                    connection.uid,
                )
                continue
            result = await run_inbound_sync(state, connection.uid, actor_id)
            logger.info(
                "Inbound poll sync for '%s' finished: %s",
                connection.uid,
                result,
            )
            reconciled += 1
        except Exception:
            logger.exception(
                "Inbound poll sync for '%s' failed",
                connection.uid,
            )
        finally:
            active_syncs.discard(connection.uid)
    return reconciled
