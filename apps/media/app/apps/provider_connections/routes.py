"""Provider connection REST resources.

Reads are for every authenticated user; writes (create/update/delete)
are administrator-only via the `require_admin` dependency.
"""

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi_mongo_base.errors import NotFoundError

from apps.auth.middleware import require_admin

from .repository import ProviderConnectionRepository
from .schemas import (
    ProviderConnectionCreate,
    ProviderConnectionResponse,
    ProviderConnectionUpdate,
    ProviderTypeResponse,
)
from .services import ProviderConnectionService, build_plugin_connector

router = APIRouter(tags=["Storage providers"])


def _repository(request: Request) -> ProviderConnectionRepository:
    return ProviderConnectionRepository(request.app.state.session_factory)


def _service(request: Request) -> ProviderConnectionService:
    return ProviderConnectionService(
        _repository(request),
        request.app.state.credential_cipher,
        request.app.state.plugin_registry,
        build_plugin_connector(request.app.state.plugin_process_manager),
    )


def _response(item: object) -> ProviderConnectionResponse:
    return ProviderConnectionResponse(
        uid=item.uid,
        provider_type=item.provider_type,
        name=item.name,
        status=item.status,
        enabled=item.enabled,
        import_existing=item.import_existing,
        mirror_structure=item.mirror_structure,
        created_at=item.created_at,
        last_tested_at=item.last_tested_at,
        last_error=item.last_error,
    )


def _schedule_import(request: Request, connection_uid: str, actor_uid: str) -> None:
    """Kick off the import job as a detached background task after a
    connection was created with `import_existing` -- the connect response
    must not block on walking the whole remote (doc 11: "`GET /files`
    never waits on sync"). `import_if_enabled` re-reads the flag itself,
    so a connection created without it is a cheap no-op here.

    Keeping the task referenced in `app.state.import_tasks` is required
    (asyncio only holds a weak reference); shutdown also waits on the set
    so an in-flight import isn't killed mid-write.
    """
    from apps.media_files.factory import build_media_file_service_from_state

    async def _run() -> None:
        try:
            service = build_media_file_service_from_state(request.app.state)
            await service.import_if_enabled(
                connection_uid, actor_user_id=actor_uid,
            )
        except Exception:
            logging.exception(
                "Import for provider connection '%s' failed", connection_uid,
            )

    task = asyncio.create_task(_run())
    request.app.state.import_tasks.add(task)
    task.add_done_callback(request.app.state.import_tasks.discard)


def _not_found() -> NotFoundError:
    return NotFoundError(
        error_code="provider_connection_not_found",
        detail="Provider connection not found",
        message="Provider connection not found",
    )


@router.get("/provider-types", response_model=list[ProviderTypeResponse])
async def list_provider_types(request: Request) -> list[ProviderTypeResponse]:
    """List supported connection types and their configuration contract.

    Sourced from the plugin registry's loaded manifests
    (docs/03-provider-system.md) -- one plugin process can register
    several entries here (e.g. `rclone`'s `s3`/`google_drive`).
    """
    registry = request.app.state.plugin_registry
    return [
        ProviderTypeResponse(
            id=item.id,
            name=item.name,
            description=item.description,
            adapter=item.process_key,
            status=item.status,
            capabilities=list(item.capabilities),
            connect_flow=item.connect_flow,
            fields=[
                {
                    "key": field.key,
                    "label": field.label,
                    "input_type": field.input_type,
                    "required": field.required,
                    "secret": field.secret,
                    "placeholder": field.placeholder,
                }
                for field in item.config_fields
            ],
        )
        for item in registry.provider_types()
    ]


@router.get(
    "/providers",
    response_model=list[ProviderConnectionResponse],
)
async def list_connections(request: Request) -> list[ProviderConnectionResponse]:
    """List configured storage connections without credentials."""
    return [_response(item) for item in await _repository(request).list()]


@router.post(
    "/providers",
    response_model=ProviderConnectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_connection(
    data: ProviderConnectionCreate,
    request: Request,
    admin: Annotated[object, Depends(require_admin)],
) -> ProviderConnectionResponse:
    """Validate, encrypt and persist a storage connection.

    "Validate" means a real `connect()` round-trip to the provider's
    plugin process over its Unix socket -- see
    `apps.provider_connections.services.build_plugin_connector`.

    With `import_existing`, the import job (remote objects ->
    StorageObjects + library MediaFiles owned by the creating admin) is
    scheduled in the background right after -- the response never waits
    on it.
    """
    connection = await _service(request).create(
        provider_type=data.provider_type,
        name=data.name,
        config=data.config,
        import_existing=data.import_existing,
        mirror_structure=data.mirror_structure,
    )
    _schedule_import(request, connection.uid, admin.uid)
    return _response(connection)


@router.patch(
    "/providers/{uid}",
    response_model=ProviderConnectionResponse,
    dependencies=[Depends(require_admin)],
)
async def update_connection(
    uid: str,
    data: ProviderConnectionUpdate,
    request: Request,
) -> ProviderConnectionResponse:
    """Rename, enable/disable and/or toggle the dual-layer flags -- not
    its config, see `ProviderConnectionService.update`."""
    updated = await _service(request).update(
        uid,
        name=data.name,
        enabled=data.enabled,
        import_existing=data.import_existing,
        mirror_structure=data.mirror_structure,
    )
    if updated is None:
        raise _not_found()
    return _response(updated)


@router.delete(
    "/providers/{uid}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def delete_connection(uid: str, request: Request) -> Response:
    """Soft-delete a provider connection."""
    if not await _repository(request).delete(uid):
        raise _not_found()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
