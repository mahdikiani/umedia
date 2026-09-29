"""Builds a `MediaFileService` (and the StorageObject read side) from
`app.state` -- mirrors `apps/resources/factory.py`'s pattern: nothing here
is a singleton, repositories only hold a session factory."""

from typing import Any, Protocol

from fastapi import Request

from apps.provider_connections.repository import ProviderConnectionRepository
from apps.storage_objects.repository import StorageObjectRepository
from apps.storage_objects.services import StorageObjectService
from apps.user_access_keys.factory import (
    build_user_access_key_service_from_state,
)

from .notifications import NotificationRepository, NotificationService
from .plugin_gateway import MediaPluginGateway
from .repository import MediaFileRepository
from .services import MediaFileService
from .settings_repository import InstanceSettingsRepository
from .transfer_repository import TransferRepository
from .transfer_service import TransferService


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
) -> dict[str, int]:
    """One reconcile pass for one connection.

    Shared by `POST /providers/{uid}/sync` and the interval poller.
    Callers own `active_syncs` add/discard so GET sync status stays
    accurate for both triggers.
    """
    service = build_media_file_service_from_state(state)
    return await service.import_from_provider(
        connection_uid,
        actor_user_id=actor_user_id,
    )


def build_storage_object_service(request: Request) -> StorageObjectService:
    return StorageObjectService(
        StorageObjectRepository(request.app.state.session_factory),
    )
