"""Provider connection REST resources.

Every authenticated user lists/creates/updates/deletes **their own**
connections. Provider type `local` is admin-only (create and use).
"""

import asyncio
import logging

from fastapi import APIRouter, Request, Response, status
from fastapi_mongo_base.errors import NotFoundError
from usso.lite.models import LocalUser

from .oauth import GoogleOAuthCredentials, OAuthStateStore
from .oauth_service import ProviderOAuthService
from .repository import ProviderConnectionRepository
from .schemas import (
    OAuthCompleteRequest,
    OAuthStartRequest,
    OAuthStartResponse,
    ProviderConnectionCreate,
    ProviderConnectionResponse,
    ProviderConnectionUpdate,
    ProviderTypeResponse,
)
from .services import (
    LOCAL_PROVIDER_TYPE,
    ProviderConnectionService,
    build_plugin_connector,
)

router = APIRouter(tags=["Storage providers"])


def _user(request: Request) -> LocalUser:
    return request.state.user


def _is_admin(user: LocalUser) -> bool:
    return "admin" in (user.roles or [])


def _repository(request: Request) -> ProviderConnectionRepository:
    return ProviderConnectionRepository(request.app.state.session_factory)


def _service(request: Request) -> ProviderConnectionService:
    return ProviderConnectionService(
        _repository(request),
        request.app.state.credential_cipher,
        request.app.state.plugin_registry,
        build_plugin_connector(request.app.state.plugin_process_manager),
    )


def _oauth_credentials(request: Request) -> GoogleOAuthCredentials:
    settings = request.app.state.settings
    return GoogleOAuthCredentials(
        client_id=settings.google_oauth_client_id,
        client_secret=settings.google_oauth_client_secret,
        redirect_uri=settings.google_oauth_redirect_uri,
    )


def _oauth_credentials_for(request: Request) -> dict[str, GoogleOAuthCredentials]:
    settings = request.app.state.settings
    return {
        "google_drive": _oauth_credentials(request),
        "onedrive": GoogleOAuthCredentials(
            client_id=settings.onedrive_oauth_client_id,
            client_secret=settings.onedrive_oauth_client_secret,
            redirect_uri=settings.onedrive_oauth_redirect_uri,
            provider_type="onedrive",
        ),
        "dropbox": GoogleOAuthCredentials(
            client_id=settings.dropbox_oauth_client_id,
            client_secret=settings.dropbox_oauth_client_secret,
            redirect_uri=settings.dropbox_oauth_redirect_uri,
            provider_type="dropbox",
        ),
    }


def _oauth_service(request: Request) -> ProviderOAuthService:
    store: OAuthStateStore = request.app.state.oauth_states
    return ProviderOAuthService(
        credentials=_oauth_credentials(request),
        provider_credentials=_oauth_credentials_for(request),
        state_store=store,
        connections=_service(request),
    )


def _response(item: object) -> ProviderConnectionResponse:
    return ProviderConnectionResponse(
        uid=item.uid,
        owner_id=getattr(item, "owner_id", None),
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
                connection_uid,
                actor_user_id=actor_uid,
            )
            from apps.media_files.worker import schedule_content_hash_backfill

            schedule_content_hash_backfill(
                request.app.state,
                connection_uid,
            )
        except Exception:
            logging.exception(
                "Import for provider connection '%s' failed",
                connection_uid,
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
    Non-admins do not see `local` (admin-only storage).
    """
    registry = request.app.state.plugin_registry
    user = _user(request)
    include_local = _is_admin(user)
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
        if include_local or item.id != LOCAL_PROVIDER_TYPE
    ]


@router.get(
    "/providers",
    response_model=list[ProviderConnectionResponse],
)
async def list_connections(request: Request) -> list[ProviderConnectionResponse]:
    """List the caller's storage connections without credentials."""
    user = _user(request)
    return [
        _response(item) for item in await _repository(request).list(owner_id=user.uid)
    ]


@router.post(
    "/providers",
    response_model=ProviderConnectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_connection(
    data: ProviderConnectionCreate,
    request: Request,
) -> ProviderConnectionResponse:
    """Validate, encrypt and persist a storage connection.

    "Validate" means a real `connect()` round-trip to the provider's
    plugin process over its Unix socket -- see
    `apps.provider_connections.services.build_plugin_connector`.

    With `import_existing`, the import job (remote objects ->
    StorageObjects + library MediaFiles owned by the creating user) is
    scheduled in the background right after -- the response never waits
    on it. `local` requires the administrator role.
    """
    user = _user(request)
    connection = await _service(request).create(
        provider_type=data.provider_type,
        name=data.name,
        config=data.config,
        owner_id=user.uid,
        is_admin=_is_admin(user),
        import_existing=data.import_existing,
        mirror_structure=data.mirror_structure,
    )
    _schedule_import(request, connection.uid, user.uid)
    return _response(connection)


@router.post(
    "/providers/oauth/start",
    response_model=OAuthStartResponse,
)
async def oauth_start(
    data: OAuthStartRequest,
    request: Request,
) -> OAuthStartResponse:
    """Begin Google Drive OAuth (localhost-redirect paste flow).

    Returns an authorization URL the UI shows / opens; the server does
    **not** receive Google's redirect. Pending `state` is kept in memory
    for CSRF checks on `/providers/oauth/complete` (single-container).
    """
    _user(request)  # authenticated — same gate as POST /providers
    started = _oauth_service(request).start(provider_type=data.provider_type)
    return OAuthStartResponse(**started)


@router.post(
    "/providers/oauth/complete",
    response_model=ProviderConnectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def oauth_complete(
    data: OAuthCompleteRequest,
    request: Request,
) -> ProviderConnectionResponse:
    """Exchange a pasted redirect/code/token and create the connection."""
    user = _user(request)
    connection = await _oauth_service(request).complete(
        provider_type=data.provider_type,
        name=data.name,
        callback=data.callback,
        state=data.state,
        root_folder_id=data.root_folder_id,
        import_existing=data.import_existing,
        mirror_structure=data.mirror_structure,
        owner_id=user.uid,
        is_admin=_is_admin(user),
    )
    _schedule_import(request, connection.uid, user.uid)
    return _response(connection)


@router.patch(
    "/providers/{uid}",
    response_model=ProviderConnectionResponse,
)
async def update_connection(
    uid: str,
    data: ProviderConnectionUpdate,
    request: Request,
) -> ProviderConnectionResponse:
    """Rename, enable/disable and/or toggle the dual-layer flags -- not
    its config, see `ProviderConnectionService.update`. Owner only;
    missing or unowned returns 404."""
    user = _user(request)
    updated = await _service(request).update(
        uid,
        owner_id=user.uid,
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
)
async def delete_connection(uid: str, request: Request) -> Response:
    """Soft-delete a provider connection the caller owns.

    Bound/synced MediaFiles go to trash; StorageObjects and remote bytes
    are kept. Placement settings that referenced this connection are cleared.
    """
    from apps.media_files.factory import build_media_file_service
    from apps.media_files.settings_repository import InstanceSettingsRepository

    user = _user(request)
    deleted = await _service(request).delete(
        uid,
        owner_id=user.uid,
        library=build_media_file_service(request),
        placement=InstanceSettingsRepository(request.app.state.session_factory),
    )
    if not deleted:
        raise _not_found()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
