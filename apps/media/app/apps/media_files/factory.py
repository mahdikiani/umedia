"""Builds a `MediaFileService` (and the StorageObject read side) from
`app.state` -- mirrors `apps/resources/factory.py`'s pattern: nothing here
is a singleton, repositories only hold a session factory."""

import logging
from typing import Any, Protocol

from fastapi import Request

from apps.provider_connections.repository import ProviderConnectionRepository
from apps.storage_objects.repository import StorageObjectRepository
from apps.storage_objects.services import StorageObjectService
from apps.user_access_keys.factory import (
    build_user_access_key_service_from_state,
)
from plugins.client import PluginRPCError

from .import_sync import ImportWalk
from .notifications import NotificationRepository, NotificationService
from .plugin_gateway import MediaPluginGateway
from .repository import MediaFileRepository
from .services import MediaFileService
from .settings_repository import InstanceSettingsRepository
from .transfer_repository import TransferRepository
from .transfer_service import TransferService

logger = logging.getLogger(__name__)


class _AppStateProtocol(Protocol):
    """The subset of `app.state` this factory reads -- `Request.app.state`
    and a plain `FastAPI.state` (no request in scope, e.g. a background
    import task or the tus completion hook) both satisfy it."""

    session_factory: Any
    plugin_registry: Any
    plugin_process_manager: Any
    credential_cipher: Any
    transfer_tasks: set


def build_media_file_service_from_state(
    state: _AppStateProtocol,
) -> MediaFileService:
    connections = ProviderConnectionRepository(state.session_factory)
    settings = InstanceSettingsRepository(state.session_factory)
    gateway = MediaPluginGateway(
        connections,
        state.plugin_registry,
        state.plugin_process_manager,
        state.credential_cipher,
    )
    return MediaFileService(
        MediaFileRepository(state.session_factory),
        StorageObjectRepository(state.session_factory),
        gateway,
        connections,
        # Temporary share links are SigV4-presigned with the minting user's
        # own access-key secret (encrypted at rest under the master key).
        access_keys=build_user_access_key_service_from_state(state),
        settings=settings,
    )


def build_media_file_service(request: Request) -> MediaFileService:
    """The per-request form -- what every route uses."""
    return build_media_file_service_from_state(request.app.state)


def build_transfer_service_from_state(state: _AppStateProtocol) -> TransferService:
    tasks = getattr(state, "transfer_tasks", None)
    return TransferService(
        TransferRepository(state.session_factory),
        build_media_file_service_from_state(state),
        tasks=tasks if tasks is not None else set(),
        notifications=NotificationService(
            NotificationRepository(state.session_factory),
        ),
    )


def build_transfer_service(request: Request) -> TransferService:
    return build_transfer_service_from_state(request.app.state)


def build_notification_service_from_state(
    state: _AppStateProtocol,
) -> NotificationService:
    return NotificationService(NotificationRepository(state.session_factory))


def build_notification_service(request: Request) -> NotificationService:
    return build_notification_service_from_state(request.app.state)


async def run_inbound_sync(
    state: _AppStateProtocol,
    connection_uid: str,
    actor_user_id: str,
    *,
    walk: ImportWalk | None = None,
) -> dict[str, int]:
    """One reconcile pass for one connection.

    Shared by `POST /providers/{uid}/sync` and the interval poller.
    Callers own `active_syncs` add/discard so GET sync status stays
    accurate for both triggers. The poller passes `walk` to bound the
    pass in time and resume it later (see `ImportWalk`).
    """
    service = build_media_file_service_from_state(state)
    try:
        result = await service.import_from_provider(
            connection_uid,
            actor_user_id=actor_user_id,
            walk=walk,
        )
    except PluginRPCError as error:
        await record_sync_health(state, connection_uid, error)
        raise
    await record_sync_health(state, connection_uid, None)
    return result


SYNC_ERROR_STATUS = "sync_error"
_LAST_ERROR_MAX_CHARS = 500


async def record_sync_health(
    state: _AppStateProtocol,
    connection_uid: str,
    error: Exception | None,
) -> None:
    """Show a failing provider on the connection itself.

    A plugin error on the root listing sets `status="sync_error"` plus
    `last_error`, which the Storage settings page already renders as
    "Needs attention" -- the owner sees the connection must be checked
    or reconnected. The next successful sync (poll or "Sync now") puts
    it back to `configured` and clears the poller's backoff. Never
    raises: health bookkeeping must not mask the sync's own outcome.
    """
    try:
        connections = ProviderConnectionRepository(state.session_factory)
        current = await connections.get(connection_uid)
        if current is None:
            return
        if error is not None:
            await connections.update(
                connection_uid,
                {
                    "status": SYNC_ERROR_STATUS,
                    "last_error": str(error)[:_LAST_ERROR_MAX_CHARS],
                },
            )
            return
        failures = getattr(state, "sync_failures", None)
        if failures is not None:
            failures.pop(connection_uid, None)
        if current.status == SYNC_ERROR_STATUS:
            await connections.update(
                connection_uid,
                {"status": "configured", "last_error": None},
            )
    except Exception:
        logger.warning(
            "Could not record sync health for '%s'",
            connection_uid,
            exc_info=True,
        )


def build_storage_object_service(request: Request) -> StorageObjectService:
    return StorageObjectService(
        StorageObjectRepository(request.app.state.session_factory),
    )
