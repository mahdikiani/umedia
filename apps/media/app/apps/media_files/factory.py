"""Builds a `MediaFileService` (and the StorageObject read side) from
`app.state` -- mirrors `apps/resources/factory.py`'s pattern: nothing here
is a singleton, repositories only hold a session factory."""

import hashlib
import logging
import re
from typing import Any, Protocol
from uuid import uuid4

from fastapi import Request

from apps.provider_connections.repository import ProviderConnectionRepository
from apps.storage_objects.repository import StorageObjectRepository
from apps.storage_objects.services import StorageObjectService
from apps.user_access_keys.factory import (
    build_user_access_key_service_from_state,
)

from .jobs import job_run
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
    *,
    trigger: str = "manual",
    run_id: str | None = None,
    confirm_missing: int | None = None,
) -> dict[str, Any]:
    """One reconcile pass for one connection.

    Shared by `POST /providers/{uid}/sync` and the interval poller.
    Callers own `active_syncs` add/discard so GET sync status stays
    accurate for both triggers.

    The outcome goes where the user sees it, for both triggers: the
    connection's `last_error` (cleared by a clean pass) and, for a failed
    or incomplete pass, an operation notification for the actor -- one per
    manual run, one per distinct problem for the poller (which repeats the
    same problem every cycle).
    """
    run_id = run_id or uuid4().hex
    options: dict[str, Any] = {"actor_user_id": actor_user_id}
    if confirm_missing is not None:
        options["confirm_missing"] = confirm_missing
    # Its own trace, run id and `job_runs` record (apps/media_files/jobs.py).
    async with job_run(
        state,
        kind="sync",
        connection_id=connection_uid,
        trigger=trigger,
        run_id=run_id,
    ) as run:
        service = build_media_file_service_from_state(state)
        try:
            result = await service.import_from_provider(connection_uid, **options)
        except Exception as error:
            await _record_sync_outcome(
                state,
                connection_uid,
                actor_user_id,
                trigger=trigger,
                run_id=run_id,
                problem=_sync_failure_text(error),
            )
            raise
        problem = _sync_incomplete_text(connection_uid, result)
        run.result = result
        run.partial = sync_is_incomplete(result)
        await _record_sync_outcome(
            state,
            connection_uid,
            actor_user_id,
            trigger=trigger,
            run_id=run_id,
            problem=problem,
        )
        return result


def sync_is_incomplete(result: dict[str, Any]) -> bool:
    """Whether a sync pass finished but left something out: folders it
    could not list (e.g. OneDrive's Personal Vault) or missing objects it
    held back from trash. Its job run is then `partial`, not `ok`; the
    poll/manual sync and the connect-time import both use this rule."""
    return any(
        result.get(key) for key in ("failed_folders", "skipped_folders", "missing_held")
    )


def _sync_failure_text(error: Exception) -> str:
    return f"Sync failed: {type(error).__name__}: {error}"[:2000]


def _sync_incomplete_text(connection_uid: str, result: dict[str, Any]) -> str | None:
    """`last_error` text for a pass that finished but left something out,
    or None for a clean pass."""
    parts: list[str] = []
    unlisted = result.get("failed_folders", 0) + result.get("skipped_folders", 0)
    if unlisted:
        names = [repr(item["folder"]) for item in result.get("failures", [])[:3]]
        more = ", ..." if unlisted > len(names) else ""
        parts.append(
            f"{unlisted} folder(s) could not be listed ({', '.join(names)}{more}); "
            "nothing under them was checked",
        )
    held = result.get("missing_held", 0)
    if held:
        parts.append(
            f"{held} indexed object(s) are no longer at the provider and were "
            "kept: too many to move to trash without confirmation (POST "
            f"/providers/{connection_uid}/sync?confirm_missing={held})",
        )
    return f"Sync incomplete: {'; '.join(parts)}" if parts else None


async def _record_sync_outcome(
    state: _AppStateProtocol,
    connection_uid: str,
    actor_user_id: str,
    *,
    trigger: str,
    run_id: str,
    problem: str | None,
) -> None:
    """Best effort: a bookkeeping failure must not hide the sync's own
    result or error."""
    session_factory = getattr(state, "session_factory", None)
    if session_factory is None:
        return
    try:
        connections = ProviderConnectionRepository(session_factory)
        connection = await connections.get(connection_uid)
        if connection is not None and connection.last_error != problem:
            await connections.update(connection_uid, {"last_error": problem})
        if problem is None:
            return
        # The poller hits the same problem every cycle: notify once per
        # distinct problem (counts ignored), not once per cycle.
        source_uid = (
            run_id
            if trigger == "manual"
            else "poll:{}:{}".format(
                connection_uid,
                hashlib.sha256(re.sub(r"\d+", "#", problem).encode()).hexdigest()[:16],
            )
        )
        await build_notification_service_from_state(state).record_failure(
            owner_id=actor_user_id,
            operation="sync",
            item_name=getattr(connection, "name", None) or connection_uid,
            error=problem,
            source_type="provider_sync",
            source_uid=source_uid,
        )
    except Exception:
        logging.exception(
            "Could not record the sync outcome of connection '%s'",
            connection_uid,
        )


def build_storage_object_service(request: Request) -> StorageObjectService:
    return StorageObjectService(
        StorageObjectRepository(request.app.state.session_factory),
    )
