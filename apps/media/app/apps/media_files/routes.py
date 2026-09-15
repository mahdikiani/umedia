"""User-library REST routes (docs/11-dual-layer-library.md "API"):

- `/files/*` -- browse/create/update/delete MediaFiles. Listing reads
  SQLite only; content streams through the primary StorageObject's
  plugin. Always behind `jwt_guard`; visibility is the MediaFile ACL.
- `/f/{uid}` -- the public-link alias over a media file's content
  (reachable without a session when `public_permission` allows).
- `/providers/{uid}/objects` + `/providers/{uid}/sync` -- the physical
  provider index: browse what's indexed, and run the import job.

Routes only parse HTTP and identify the actor; every decision lives in
`MediaFileService` / `StorageObjectService`.
"""

import asyncio
import logging
import re
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse, Response, StreamingResponse

from apps.auth.middleware import resolve_optional_user

from .api_schemas import (
    LinkIn,
    MediaFileOut,
    MediaFileUpdateIn,
    PageOut,
    PermissionIn,
    PermissionOut,
    StorageObjectOut,
    SyncAcceptedOut,
    SyncStatusOut,
    TemporaryAddIn,
    TemporaryLinkIn,
    TemporaryLinkOut,
    TransferCreateIn,
    TransferOut,
    VolumeStatsOut,
)
from .content_type import serve_content_type
from .errors import MediaFileNotFoundError, MediaFileValidationError
from .factory import (
    build_media_file_service,
    build_storage_object_service,
    build_transfer_service,
    run_inbound_sync,
)
from .schemas import MediaFileRecord
from .services import UNSET, MediaFileService
from .transfer_service import TransferCreate

router = APIRouter(prefix="/files", tags=["Files"])
public_router = APIRouter(prefix="/f", tags=["Public links"])
provider_index_router = APIRouter(tags=["Provider objects"])

_RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)")


class _InvalidRangeError(Exception):
    """The `Range` header didn't parse as `bytes=start-end`."""


class _RangeNotSatisfiableError(Exception):
    """The `Range` header parsed fine but doesn't fit the file's size."""


def _service(request: Request) -> MediaFileService:
    return build_media_file_service(request)


def _actor(request: Request) -> str:
    """The authenticated caller's uid -- every `/files/*` route is behind
    `jwt_guard`, which set `request.state.user`."""
    return request.state.user.uid


def _is_admin(request: Request) -> bool:
    return "admin" in (request.state.user.roles or [])


async def _require_owned_connection(request: Request, uid: str) -> object:
    """Owner + usable gate for `/providers/{uid}/objects` and sync.

    404 when missing, unowned, or not usable (e.g. non-admin with a
    leftover `local` connection after role demotion) -- do not leak
    existence.
    """
    from apps.provider_connections.repository import ProviderConnectionRepository
    from apps.provider_connections.services import connection_usable_by

    user = request.state.user
    connection = await ProviderConnectionRepository(
        request.app.state.session_factory,
    ).get(uid, owner_id=user.uid)
    if connection is None or not connection_usable_by(
        connection,
        actor_user_id=user.uid,
        is_admin=_is_admin(request),
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider connection not found",
        )
    return connection


def _parse_range(range_header: str | None, size: int) -> tuple[int, int] | None:
    if not range_header:
        return None
    match = _RANGE_RE.match(range_header)
    if not match:
        raise _InvalidRangeError
    start_str, end_str = match.groups()
    start = int(start_str)
    end = int(end_str) if end_str else size - 1
    if start >= size or end >= size or start > end:
        raise _RangeNotSatisfiableError
    return start, end


# Private library bytes must never sit in a shared cache (Cloudflare sits
# in front of umedia.uln.me). Without this, an authenticated `<img src=
# /files/{uid}/content/...>` response can later be replayed to an
# anonymous/incognito client keyed only by URL. Public `/f/{uid}` may be
# shared-cached; max-age is the revoke latency vs origin-hit tradeoff.
_PRIVATE_CACHE_CONTROL = "private, no-store"
_PUBLIC_LINK_CACHE_CONTROL = "public, max-age=300"


def _metadata_headers(
    record: MediaFileRecord,
    *,
    attachment: bool = False,
    shared_cacheable: bool = False,
) -> dict[str, str]:
    disposition = "attachment" if attachment else "inline"
    headers = {
        "Content-Type": serve_content_type(record.name, record.content_type),
        "Content-Length": str(record.size),
        "Content-Disposition": (
            f"{disposition}; filename*=UTF-8''{quote(record.name)}"
        ),
        "Accept-Ranges": "bytes",
        "Last-Modified": record.updated_at.strftime("%a, %d %b %Y %H:%M:%S GMT"),
        "Cache-Control": (
            _PUBLIC_LINK_CACHE_CONTROL
            if shared_cacheable
            else _PRIVATE_CACHE_CONTROL
        ),
    }
    if record.content_hash:
        headers["ETag"] = f'"{record.content_hash}"'
    return headers


async def _content_response(
    service: MediaFileService,
    uid: str,
    request: Request,
    *,
    actor_user_id: str | None,
    via_public_link: bool = False,
) -> Response:
    """Shared by `GET /files/{id}/content` and `GET /f/{uid}` -- same
    streaming/`Range` behavior; the visibility rule is the service's,
    selected by `via_public_link`."""
    if via_public_link:
        record = await service.get_via_public_link(
            uid,
            actor_user_id=actor_user_id,
        )
    else:
        record = await service.get(uid, actor_user_id=actor_user_id)
    if record.type == "folder":
        raise MediaFileValidationError("Cannot stream content of a folder")

    range_header = request.headers.get("Range")
    try:
        bounds = _parse_range(range_header, record.size)
    except _InvalidRangeError:
        raise HTTPException(400, "Invalid Range header") from None
    except _RangeNotSatisfiableError:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{record.size}"},
        )

    if via_public_link:
        record, stream = await service.read_content_via_public_link(
            uid,
            actor_user_id=actor_user_id,
            range_header=range_header,
        )
    else:
        record, stream = await service.read_content(
            uid,
            actor_user_id=actor_user_id,
            range_header=range_header,
        )
    download = request.query_params.get("download", "").lower() in {
        "1",
        "true",
        "yes",
    }
    # Only truly public `/f/` responses may be edge-cached. `/files/...`
    # stays private even when the same file is also public via `/f/`, and
    # ACL-only `/f/` opens (logged-in recipient, `public_permission=none`)
    # must not poison a shared cache either.
    headers = _metadata_headers(
        record,
        attachment=download,
        shared_cacheable=(
            via_public_link and record.public_permission == "read"
        ),
    )
    if bounds is None:
        return StreamingResponse(stream, status_code=200, headers=headers)

    start, end = bounds
    headers["Content-Range"] = f"bytes {start}-{end}/{record.size}"
    headers["Content-Length"] = str(end - start + 1)
    return StreamingResponse(stream, status_code=206, headers=headers)


# ----------------------------------------------------------------------
# /files
# ----------------------------------------------------------------------


@router.get("", response_model=PageOut[MediaFileOut])
async def list_files(
    request: Request,
    parent_id: str | None = None,
    q: str | None = None,
    scope: Annotated[str, Query()] = "owned",
    include_deleted: Annotated[bool, Query()] = False,
    sort: Annotated[Literal["name", "updated_at", "type", "size"], Query()] = "name",
    order: Annotated[Literal["asc", "desc"] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PageOut[MediaFileOut]:
    """Browse the library -- SQLite only, never live provider I/O.
    `scope=owned` (default) is the actor's own children of `parent_id`;
    `shared_with_me`/`shared_by_me` are flat share lists; `all_visible`
    combines owned + shared for a parent; `scope=trash` is the actor's
    recycle bin (soft-deleted roots, flat, `parent_id` ignored --
    `deleted_at` on each item says when; the nightly job purges items
    older than 30 days). `include_deleted=true` is valid only with
    `owned`. Paginated: `limit`/`offset` slice the result; the
    envelope's `total` counts the full set."""
    service = _service(request)
    actor_user_id = _actor(request)
    if q is not None and q.strip():
        page = await service.search(
            q,
            actor_user_id=actor_user_id,
            under_parent_id=parent_id,
            limit=limit,
            offset=offset,
        )
    else:
        effective_order = order or (
            "desc" if sort in {"updated_at", "size"} else "asc"
        )
        page = await service.list_children(
            parent_id,
            actor_user_id=actor_user_id,
            scope=scope,
            include_deleted=include_deleted,
            sort=sort,
            order=effective_order,
            limit=limit,
            offset=offset,
        )
    return PageOut.from_page(
        page,
        [MediaFileOut.from_record(record) for record in page.items],
    )


@router.get("/stats", response_model=VolumeStatsOut)
async def library_stats(request: Request) -> VolumeStatsOut:
    """Owned library usage for the sidebar -- SQLite only."""
    stats = await _service(request).volume_stats(actor_user_id=_actor(request))
    return VolumeStatsOut(**stats)


@router.post(
    "/transfers",
    response_model=TransferOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_transfer(
    request: Request, body: TransferCreateIn,
) -> TransferOut:
    """Enqueue a library move/copy job (always 202, even for one item)."""
    record = await build_transfer_service(request).create_and_enqueue(
        _actor(request),
        TransferCreate(
            operation=body.operation,
            source_ids=body.source_ids,
            dest_parent_id=body.dest_parent_id,
            conflict=body.conflict,
        ),
        is_admin=_is_admin(request),
    )
    return TransferOut.from_record(record)


@router.get("/transfers", response_model=list[TransferOut])
async def list_transfers(request: Request) -> list[TransferOut]:
    """Recent transfer jobs for the caller (newest first, max 50)."""
    records = await build_transfer_service(request).list_for_user(
        actor_user_id=_actor(request),
    )
    return [TransferOut.from_record(record) for record in records]


@router.get("/transfers/{uid}", response_model=TransferOut)
async def get_transfer(uid: str, request: Request) -> TransferOut:
    record = await build_transfer_service(request).get(
        uid, actor_user_id=_actor(request),
    )
    return TransferOut.from_record(record)


@router.post("/temporary", status_code=status.HTTP_204_NO_CONTENT)
async def add_temporary_items(
    request: Request,
    body: TemporaryAddIn,
) -> Response:
    await _service(request).add_temporary_items(
        body.media_file_ids,
        actor_user_id=_actor(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/temporary", response_model=list[MediaFileOut])
async def list_temporary_items(request: Request) -> list[MediaFileOut]:
    records = await _service(request).list_temporary_items(
        actor_user_id=_actor(request),
    )
    return [MediaFileOut.from_record(record) for record in records]


@router.delete(
    "/temporary/{media_file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_temporary_item(
    media_file_id: str,
    request: Request,
) -> Response:
    await _service(request).remove_temporary_item(
        media_file_id,
        actor_user_id=_actor(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/temporary", status_code=status.HTTP_204_NO_CONTENT)
async def clear_temporary_items(request: Request) -> Response:
    await _service(request).clear_temporary_items(
        actor_user_id=_actor(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("", response_model=MediaFileOut, status_code=201)
async def create_file(
    request: Request,
    name: Annotated[str, Form()],
    type: Annotated[str, Form()] = "file",  # noqa: A002 -- matches the data model's field name
    parent_id: Annotated[str | None, Form()] = None,
    provider_connection_id: Annotated[str | None, Form()] = None,
    file: Annotated[UploadFile | None, File()] = None,
) -> MediaFileOut:
    """Upload a file or make a library folder (`type=folder`). Placement
    is resolved by the service: a bound parent folder wins, otherwise
    the instance policy."""
    service = _service(request)
    if type == "folder":
        record = await service.create_folder(
            name=name,
            parent_id=parent_id,
            owner_id=_actor(request),
            is_admin=_is_admin(request),
        )
        return MediaFileOut.from_record(record)

    content = await file.read() if file is not None else b""
    record = await service.upload(
        provider_connection_id=provider_connection_id,
        parent_id=parent_id,
        name=name,
        content=content,
        content_type=file.content_type if file is not None else None,
        owner_id=_actor(request),
        is_admin=_is_admin(request),
    )
    return MediaFileOut.from_record(record)


@router.get("/{uid}", response_model=MediaFileOut)
async def get_file(uid: str, request: Request) -> MediaFileOut:
    record = await _service(request).get(uid, actor_user_id=_actor(request))
    return MediaFileOut.from_record(record)


@router.head("/{uid}")
async def head_file(uid: str, request: Request) -> Response:
    record = await _service(request).get(uid, actor_user_id=_actor(request))
    return Response(headers=_metadata_headers(record))


@router.patch("/{uid}", response_model=MediaFileOut)
async def update_file(
    uid: str,
    request: Request,
    body: MediaFileUpdateIn,
) -> MediaFileOut:
    """Rename/move within the library, and/or change `public_permission`.
    Whether a move also reaches the provider is the service's mirror rule
    (connection `mirror_structure` + plugin capability)."""
    service = _service(request)
    actor_user_id = _actor(request)

    record: MediaFileRecord
    if body.name is not None or "parent_id" in body.model_fields_set:
        record = await service.update(
            uid,
            actor_user_id=actor_user_id,
            name=body.name,
            # `parent_id: null` means "move to root", absent means "keep".
            parent_id=body.parent_id if "parent_id" in body.model_fields_set else UNSET,
        )
    else:
        record = await service.get(uid, actor_user_id=actor_user_id)

    if body.public_permission is not None:
        record = await service.set_public_permission(
            uid,
            body.public_permission,
            actor_user_id=actor_user_id,
        )
    if body.starred is not None:
        record = await service.set_starred(
            uid,
            actor_user_id=actor_user_id,
            starred=body.starred,
        )
    return MediaFileOut.from_record(record)


@router.get("/{uid}/content")
@router.get("/{uid}/content/{filename}")
async def read_file_content(
    uid: str,
    request: Request,
    filename: str | None = None,
) -> Response:
    """Stream file bytes under the content open matrix.

    Optional trailing `{filename}` is a single cosmetic name segment; only
    `{uid}` is looked up. Session is optional: owner / ACL share /
    workspace / permanent public may open; everyone else gets 404 (not
    401 -- existence must stay private). Short-lived links use `/s3/...`.
    """
    user = await resolve_optional_user(request)
    try:
        return await _content_response(
            _service(request),
            uid,
            request,
            actor_user_id=None if user is None else user.uid,
            via_public_link=True,
        )
    except MediaFileNotFoundError as exc:
        # Deny must not be edge-cached: a later public-share flip would
        # otherwise keep serving this 404 from Cloudflare.
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.error_code,
                "message": exc.message,
                "details": {},
            },
            headers={"Cache-Control": _PRIVATE_CACHE_CONTROL},
        )


@router.get("/{uid}/permissions", response_model=list[PermissionOut])
async def list_file_permissions(uid: str, request: Request) -> list[PermissionOut]:
    entries = await _service(request).list_permissions(
        uid,
        actor_user_id=_actor(request),
    )
    return [PermissionOut(**entry) for entry in entries]


@router.put("/{uid}/permissions", response_model=MediaFileOut)
async def set_file_permission(
    uid: str,
    request: Request,
    body: PermissionIn,
) -> MediaFileOut:
    """Grant/replace one user's permission level; `permission: 0` (or
    `"none"`) revokes their entry."""
    record = await _service(request).set_user_permission(
        uid,
        actor_user_id=_actor(request),
        target_user_id=body.user_id,
        permission=body.permission,
    )
    return MediaFileOut.from_record(record)


@router.post("/{uid}/temporary-link", response_model=TemporaryLinkOut)
async def create_temporary_link(
    uid: str,
    request: Request,
    body: TemporaryLinkIn,
) -> TemporaryLinkOut:
    """Mint a SigV4-presigned GET URL for this file on the S3 gateway
    (`/s3/{bucket}/{library-path}?X-Amz-...`). No `public_permission` change
    and no share row -- the signature is the whole grant. Deactivating
    the signing access key revokes every link it signed."""
    from apps.s3.auth import public_base_url

    link = await _service(request).create_temporary_link(
        uid,
        actor_user_id=_actor(request),
        expires_in=body.expires_in,
        base_url=public_base_url(request),
    )
    return TemporaryLinkOut(
        url=link.url,
        key_id=link.key_id,
        expires=link.expires,
        expires_at=link.expires_at,
    )


@router.post("/{uid}/link", response_model=MediaFileOut)
async def link_storage_object(
    uid: str,
    request: Request,
    body: LinkIn,
) -> MediaFileOut:
    """Attach an indexed-but-unlinked StorageObject as this file's
    `primary` content (docs/11-dual-layer-library.md)."""
    record = await _service(request).link_object(
        uid,
        body.storage_object_id,
        actor_user_id=_actor(request),
    )
    return MediaFileOut.from_record(record)


@router.post("/{uid}/restore", response_model=MediaFileOut)
async def restore_file(uid: str, request: Request) -> MediaFileOut:
    service = _service(request)
    actor_user_id = _actor(request)
    await service.restore(uid, actor_user_id=actor_user_id)
    return MediaFileOut.from_record(
        await service.get(uid, actor_user_id=actor_user_id),
    )


@router.delete("/{uid}", status_code=204)
async def delete_file(
    uid: str,
    request: Request,
    permanent: Annotated[bool, Query()] = False,
) -> Response:
    """Two-step delete: soft by default; `?permanent=true` removes the
    library row (requires it to already be soft-deleted). Neither touches
    the StorageObject or the provider's bytes -- doc 11's v1 rule."""
    service = _service(request)
    actor_user_id = _actor(request)
    if permanent:
        await service.hard_delete(uid, actor_user_id=actor_user_id)
    else:
        await service.soft_delete(uid, actor_user_id=actor_user_id)
    return Response(status_code=204)


# ----------------------------------------------------------------------
# Provider index (separate menu): /providers/{uid}/objects + sync
# ----------------------------------------------------------------------


@provider_index_router.get(
    "/providers/{uid}/objects",
    response_model=PageOut[StorageObjectOut],
)
async def list_provider_objects(
    uid: str,
    request: Request,
    parent_ref: str | None = None,
    q: str | None = None,
    only_parent: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PageOut[StorageObjectOut]:
    """Browse one connection's physical index -- from SQLite (what sync
    has indexed), never a live provider call. Owner-only: StorageObjects
    carry no per-user ACL; the physical layer belongs to the connection
    owner. Paginated with the same `limit`/`offset` envelope as
    `GET /files`."""
    await _require_owned_connection(request, uid)
    service = build_storage_object_service(request)
    if q is not None and q.strip():
        page = await service.search(
            uid,
            query=q,
            parent_ref=parent_ref,
            limit=limit,
            offset=offset,
        )
    else:
        page = await service.list_objects(
            uid,
            parent_ref=parent_ref,
            filter_by_parent=only_parent,
            limit=limit,
            offset=offset,
        )
    return PageOut.from_page(
        page,
        [StorageObjectOut.from_record(record) for record in page.items],
    )


def _schedule_sync(request: Request, connection_uid: str, actor_uid: str) -> None:
    """Detached sync job — same keep-alive pattern as connect-time import.
    The reconcile itself lives in `run_inbound_sync` so the interval
    poller walks the same path.
    """

    async def _run() -> None:
        try:
            result = await run_inbound_sync(
                request.app.state,
                connection_uid,
                actor_uid,
            )
            logging.info(
                "Sync for provider '%s' finished: %s",
                connection_uid,
                result,
            )
        except Exception:
            logging.exception(
                "Sync for provider connection '%s' failed", connection_uid
            )
        finally:
            request.app.state.active_syncs.discard(connection_uid)

    task = asyncio.create_task(_run())
    request.app.state.import_tasks.add(task)
    task.add_done_callback(request.app.state.import_tasks.discard)


@provider_index_router.get(
    "/providers/{uid}/sync",
    response_model=SyncStatusOut,
)
async def get_sync_status(uid: str, request: Request) -> SyncStatusOut:
    """Whether a background sync is currently walking this connection
    (manual POST or the interval poller -- both use `active_syncs`).
    The Storage UI polls this so the Sync button can stay spinning/
    disabled for the whole job, not just the 202 accept."""
    await _require_owned_connection(request, uid)
    running = uid in request.app.state.active_syncs
    return SyncStatusOut(
        connection_id=uid,
        status="running" if running else "idle",
    )


@provider_index_router.post(
    "/providers/{uid}/sync",
    response_model=SyncAcceptedOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def sync_provider(
    uid: str,
    request: Request,
) -> SyncAcceptedOut:
    """Accept a sync job immediately; the provider walk + reconcile runs
    in the background so the UI never blocks on a large tree. Owner-only."""
    await _require_owned_connection(request, uid)
    if uid in request.app.state.active_syncs:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A sync is already running for this provider",
        )

    request.app.state.active_syncs.add(uid)
    _schedule_sync(request, uid, request.state.user.uid)
    return SyncAcceptedOut(connection_id=uid)


# ----------------------------------------------------------------------
# Public link alias
# ----------------------------------------------------------------------


async def _optional_actor(request: Request) -> str | None:
    """`/f/{uid}` is reachable without a session (`jwt_guard` skips it),
    so the route resolves the caller itself -- `None` for anonymous."""
    user = await resolve_optional_user(request)
    return None if user is None else user.uid


@public_router.get("/{uid}")
@public_router.get("/{uid}/{filename}")
async def get_public_link(
    uid: str,
    request: Request,
    details: bool = False,
    filename: str | None = None,
) -> Response:
    """Permanent public alias gated by `public_permission` (or the
    caller's ACL). Optional trailing `{filename}` is cosmetic -- only
    `{uid}` is looked up. Temporary shares use SigV4-presigned URLs on
    `/s3/{bucket}/{library-path}` instead."""
    service = _service(request)
    actor_user_id = await _optional_actor(request)
    if details:
        record = await service.get_via_public_link(
            uid,
            actor_user_id=actor_user_id,
        )
        return JSONResponse(MediaFileOut.from_record(record).model_dump(mode="json"))
    try:
        return await _content_response(
            service,
            uid,
            request,
            actor_user_id=actor_user_id,
            via_public_link=True,
        )
    except MediaFileNotFoundError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.error_code,
                "message": exc.message,
                "details": {},
            },
            headers={"Cache-Control": _PRIVATE_CACHE_CONTROL},
        )


@public_router.head("/{uid}")
@public_router.head("/{uid}/{filename}")
async def head_public_link(
    uid: str,
    request: Request,
    filename: str | None = None,
) -> Response:
    record = await _service(request).get_via_public_link(
        uid,
        actor_user_id=await _optional_actor(request),
    )
    return Response(
        headers=_metadata_headers(
            record,
            shared_cacheable=record.public_permission == "read",
        ),
    )
