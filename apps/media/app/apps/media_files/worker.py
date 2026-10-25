"""Scheduled maintenance for the MediaFile library.

`purge_expired_trash` is the nightly APScheduler job body (wired into
`server/server.py`'s lifespan, cron 00:00 Asia/Tehran): permanently
remove trash roots soft-deleted more than
`services.TRASH_RETENTION_DAYS` days ago.

`poll_inbound_syncs` is the interval job body: walk every enabled
provider connection and run the same reconcile as
`POST /providers/{uid}/sync`. Sequential on purpose (SQLite; one plugin
walk at a time). A failed provider is logged and skipped so one outage
cannot abort the tick, then backed off (30 min .. 24 h) so a broken
connection reports one error event instead of one per tick. Incomplete
walks never mark objects missing -- `import_from_provider` raises before
that step (a single unlistable sub-folder is skipped, not fatal).

Purging is library-only (doc 11's v1 rule -- StorageObjects and the
provider's bytes always survive), so the purge service is assembled from
nothing but the session factory; the plugin gateway and connection
lookup are stubbed with loud placeholders because a purge reaching for
either would be a bug, not a missing dependency.
"""

import asyncio
import logging
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.provider_connections.repository import ProviderConnectionRepository
from plugins.client import PluginRPCError

from .factory import build_media_file_service_from_state, run_inbound_sync
from .repository import MediaFileRepository
from .services import MediaFileService
from .shared import TRASH_RETENTION_DAYS

logger = logging.getLogger(__name__)


def schedule_content_hash_backfill(
    app_or_state: Any,  # noqa: ANN401
    connection_uid: str,
) -> None:
    """Start one retained hash job per provider connection."""
    state = _state(app_or_state)
    hash_tasks = getattr(state, "hash_tasks", None)
    if hash_tasks is None:
        hash_tasks = {}
        state.hash_tasks = hash_tasks
    current = hash_tasks.get(connection_uid)
    if current is not None and not current.done():
        return

    async def _run() -> None:
        try:
            service = build_media_file_service_from_state(state)
            count = await service.hash_missing_content(connection_uid)
            logger.info(
                "Content hash backfill for '%s' finished: %d object(s)",
                connection_uid,
                count,
            )
        except Exception:
            logger.exception(
                "Content hash backfill for '%s' failed",
                connection_uid,
            )
        finally:
            hash_tasks.pop(connection_uid, None)

    task = asyncio.create_task(_run())
    hash_tasks[connection_uid] = task


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


# A connection whose provider keeps failing (revoked token, a listing that
# always times out) was retried on every 15-minute tick, one GlitchTip event
# each (UMEDIA-4/6: 100+ events). Back off per connection in this process:
# 30 min, doubling, up to 24 h. "Sync now" ignores the backoff, and any
# successful sync clears it (see `factory.record_sync_health`).
SYNC_RETRY_BASE_SECONDS = 1800.0
SYNC_RETRY_MAX_SECONDS = 24 * 3600.0
# A transport failure (timeout, connect error) is often a blip: report it as
# an error only once it has failed this many polls in a row.
TRANSIENT_REPORT_AFTER = 3


class SyncFailure:
    """Backoff bookkeeping for one connection: consecutive failed polls,
    when to try again, and the last error message already reported."""

    def __init__(self) -> None:
        self.attempts = 0
        self.retry_at = 0.0
        self.reported: str | None = None


def _sync_failures(state: Any) -> dict[str, SyncFailure]:  # noqa: ANN401
    failures = getattr(state, "sync_failures", None)
    if failures is None:
        failures = {}
        state.sync_failures = failures
    return failures


def _note_sync_failure(
    failures: dict[str, SyncFailure],
    connection_uid: str,
    error: Exception,
    now: float,
) -> tuple[SyncFailure, bool]:
    """Record one failed poll; return it and whether to report an error
    event (first time this message is worth reporting) or only warn."""
    failure = failures.setdefault(connection_uid, SyncFailure())
    failure.attempts += 1
    delay = min(
        SYNC_RETRY_BASE_SECONDS * 2 ** (failure.attempts - 1),
        SYNC_RETRY_MAX_SECONDS,
    )
    failure.retry_at = now + delay
    message = str(error)
    transient = isinstance(error, PluginRPCError) and error.status_code is None
    due = not transient or failure.attempts >= TRANSIENT_REPORT_AFTER
    report = due and failure.reported != message
    if report:
        failure.reported = message
    return failure, report


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

    failures = _sync_failures(state)
    connections = ProviderConnectionRepository(state.session_factory)
    files = MediaFileRepository(state.session_factory)
    reconciled = 0
    for connection in await connections.list():
        if not connection.enabled or connection.uid in active_syncs:
            continue
        failure = failures.get(connection.uid)
        if failure is not None and time.monotonic() < failure.retry_at:
            logger.debug(
                "Skipping inbound sync for '%s': backing off after %d "
                "failed poll(s)",
                connection.uid,
                failure.attempts,
            )
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
            schedule_content_hash_backfill(state, connection.uid)
            logger.info(
                "Inbound poll sync for '%s' finished: %s",
                connection.uid,
                result,
            )
            failures.pop(connection.uid, None)
            reconciled += 1
        except Exception as error:
            failure, report = _note_sync_failure(
                failures, connection.uid, error, time.monotonic(),
            )
            retry_in = int(failure.retry_at - time.monotonic())
            if report:
                logger.exception(
                    "Inbound poll sync for '%s' failed (attempt %d, next "
                    "try in %ds)",
                    connection.uid,
                    failure.attempts,
                    retry_in,
                )
            else:
                logger.warning(
                    "Inbound poll sync for '%s' failed again (attempt %d, "
                    "next try in %ds): %s",
                    connection.uid,
                    failure.attempts,
                    retry_in,
                    error,
                )
        finally:
            active_syncs.discard(connection.uid)
    return reconciled
