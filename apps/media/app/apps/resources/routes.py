"""Resource REST routes (docs/05-api-design.md): browse/create/update/
delete resources through the connection-routed plugin gateway, plus the
`/f/{uid}` public-link alias.

`/resources/*` is always behind `jwt_guard` (any authenticated user;
visibility is then enforced by Resource ACL — owner / share / public).
`/f/{uid}` is readable without a session when `public_permission` allows
it, or with a session when the caller has ACL READ (see
`apps.auth.middleware._PUBLIC_LINK_RE` and `resolve_optional_user`).
"""

import re
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

from apps.auth.middleware import resolve_optional_user

from .api_schemas import PermissionIn, PermissionOut, ResourceOut, VolumeStatsOut
from .errors import ResourceValidationError
from .factory import build_resource_service
from .schemas import ResourceRecord
from .services import ResourceService

router = APIRouter(prefix="/resources", tags=["Resources"])
public_router = APIRouter(prefix="/f", tags=["Public links"])

_RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)")


class _InvalidRangeError(Exception):
    """The `Range` header didn't parse as `bytes=start-end`."""


class _RangeNotSatisfiableError(Exception):
    """The `Range` header parsed fine but doesn't fit the resource's size."""


def _service(request: Request) -> ResourceService:
    return build_resource_service(request)


def _actor(request: Request) -> str:
    """The authenticated caller's uid -- every `/resources/*` route is
    behind `jwt_guard`, which set `request.state.user`. Routes only
    *identify* the actor; all permission decisions live in
    `ResourceService`/`apps.resources.permissions`."""
    return request.state.user.uid


def _parse_range(range_header: str | None, size: int) -> tuple[int, int] | None:
    """`None` (no range requested), or the inclusive `(start, end)` bytes
    requested -- raises `_InvalidRangeError`/`_RangeNotSatisfiableError` otherwise."""
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


def _metadata_headers(record: ResourceRecord) -> dict[str, str]:
    headers = {
        "Content-Type": record.content_type,
        "Content-Length": str(record.size),
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(record.name)}",
        "Accept-Ranges": "bytes",
        "Last-Modified": record.updated_at.strftime("%a, %d %b %Y %H:%M:%S GMT"),
    }
    if record.content_hash:
        headers["ETag"] = f'"{record.content_hash}"'
    return headers


async def _content_response(
    service: ResourceService,
    uid: str,
    request: Request,
    *,
    actor_user_id: str | None,
    via_public_link: bool = False,
) -> Response:
    """Shared by `GET /resources/{id}/content` and `GET /f/{uid}` -- same
    streaming/`Range` behavior either way; the visibility rule (`ACL read`
    vs `ACL read or public`) is the service's, selected by
    `via_public_link`."""
    if via_public_link:
        record = await service.get_via_public_link(uid, actor_user_id=actor_user_id)
    else:
        record = await service.get(uid, actor_user_id=actor_user_id)
    if record.type == "folder":
        raise ResourceValidationError("Cannot stream content of a folder")

    range_header = request.headers.get("Range")
    try:
        bounds = _parse_range(range_header, record.size)
    except _InvalidRangeError:
        raise HTTPException(400, "Invalid Range header") from None
    except _RangeNotSatisfiableError:
        return Response(
            status_code=416, headers={"Content-Range": f"bytes */{record.size}"},
        )

    if via_public_link:
        record, stream = await service.read_content_via_public_link(
            uid, actor_user_id=actor_user_id, range_header=range_header,
        )
    else:
        record, stream = await service.read_content(
            uid, actor_user_id=actor_user_id, range_header=range_header,
        )
    headers = _metadata_headers(record)
    if bounds is None:
        return StreamingResponse(stream, status_code=200, headers=headers)

    start, end = bounds
    headers["Content-Range"] = f"bytes {start}-{end}/{record.size}"
    headers["Content-Length"] = str(end - start + 1)
    return StreamingResponse(stream, status_code=206, headers=headers)


# ----------------------------------------------------------------------
# Private routes
# ----------------------------------------------------------------------


@router.get("/volume-stats", response_model=VolumeStatsOut)
async def volume_stats(request: Request) -> VolumeStatsOut:
    """Registered before `/{uid}` so it isn't shadowed as a resource id."""
    return VolumeStatsOut(**await _service(request).volume_stats())


@router.get("", response_model=list[ResourceOut])
async def list_resources(
    request: Request,
    parent_id: str | None = None,
    scope: Annotated[str, Query()] = "owned",
    include_deleted: Annotated[bool, Query()] = False,
) -> list[ResourceOut]:
    """Browse resources. `scope=owned` (default) is the actor's own
    children of `parent_id`; `shared_with_me`/`shared_by_me` are flat
    share lists; `all_visible` combines owned + shared for a parent.
    `include_deleted=true` (trash) is valid only with `owned`."""
    records = await _service(request).list_children(
        parent_id,
        actor_user_id=_actor(request),
        scope=scope,
        include_deleted=include_deleted,
    )
    return [ResourceOut.from_record(record) for record in records]


@router.post("", response_model=ResourceOut, status_code=201)
async def create_resource(
    request: Request,
    provider_connection_id: Annotated[str, Form()],
    name: Annotated[str, Form()],
    type: Annotated[str, Form()] = "file",  # noqa: A002 -- matches the data model's own field name
    parent_id: Annotated[str | None, Form()] = None,
    file: Annotated[UploadFile | None, File()] = None,
) -> ResourceOut:
    """Upload a file, or make a folder (`type=folder`, no `file` part)."""
    content = await file.read() if file is not None else b""
    record = await _service(request).create(
        provider_connection_id=provider_connection_id,
        parent_id=parent_id,
        name=name,
        type_=type,
        content=content,
        content_type=file.content_type if file is not None else None,
        owner_id=_actor(request),
    )
    return ResourceOut.from_record(record)


@router.get("/{uid}", response_model=ResourceOut)
async def get_resource(uid: str, request: Request) -> ResourceOut:
    record = await _service(request).get(uid, actor_user_id=_actor(request))
    return ResourceOut.from_record(record)


@router.head("/{uid}")
async def head_resource(uid: str, request: Request) -> Response:
    record = await _service(request).get(uid, actor_user_id=_actor(request))
    return Response(headers=_metadata_headers(record))


@router.get("/{uid}/content")
async def read_resource_content(uid: str, request: Request) -> Response:
    return await _content_response(
        _service(request), uid, request, actor_user_id=_actor(request),
    )


@router.get("/{uid}/permissions", response_model=list[PermissionOut])
async def list_resource_permissions(
    uid: str, request: Request,
) -> list[PermissionOut]:
    entries = await _service(request).list_permissions(
        uid, actor_user_id=_actor(request),
    )
    return [PermissionOut(**entry) for entry in entries]


@router.put("/{uid}/permissions", response_model=ResourceOut)
async def set_resource_permission(
    uid: str, request: Request, body: PermissionIn,
) -> ResourceOut:
    """Grant/replace one user's permission level; `permission: 0` (or
    `"none"`) revokes their entry."""
    record = await _service(request).set_user_permission(
        uid,
        actor_user_id=_actor(request),
        target_user_id=body.user_id,
        permission=body.permission,
    )
    return ResourceOut.from_record(record)


@router.put("/{uid}", response_model=ResourceOut)
async def update_resource(
    uid: str,
    request: Request,
    name: Annotated[str | None, Form()] = None,
    parent_id: Annotated[str | None, Form()] = None,
    public_permission: Annotated[str | None, Form()] = None,
    file: Annotated[UploadFile | None, File()] = None,
) -> ResourceOut:
    """Rename/move/overwrite content, and/or change `public_permission`.

    Individual `Form()` scalars, not a nested Pydantic Form-model -- the
    latter (`Annotated[SomeModel, Form()]`) only parses when the request
    is actually `multipart/form-data`, and a rename-only PUT with no file
    part is otherwise sent `application/x-www-form-urlencoded` by every
    normal HTTP client, which then 422s with a spurious "field required"
    on the whole model. Scalar `Form()` params (see `create_resource`
    above) don't have that restriction.
    """
    service = _service(request)
    actor_user_id = _actor(request)
    content = await file.read() if file is not None else None

    record: ResourceRecord
    if name is not None or parent_id is not None or content is not None:
        record = await service.update(
            uid,
            name=name,
            parent_id=parent_id,
            content=content,
            actor_user_id=actor_user_id,
        )
    else:
        record = await service.get(uid, actor_user_id=actor_user_id)

    if public_permission is not None:
        record = await service.set_public_permission(
            uid, public_permission, actor_user_id=actor_user_id,
        )
    return ResourceOut.from_record(record)


@router.post("/{uid}/restore", response_model=ResourceOut)
async def restore_resource(uid: str, request: Request) -> ResourceOut:
    service = _service(request)
    actor_user_id = _actor(request)
    await service.restore(uid, actor_user_id=actor_user_id)
    return ResourceOut.from_record(
        await service.get(uid, actor_user_id=actor_user_id),
    )


@router.delete("/{uid}", status_code=204)
async def delete_resource(
    uid: str,
    request: Request,
    permanent: Annotated[bool, Query()] = False,
) -> Response:
    """Two-step delete (docs/04-data-model.md): soft by default, hard
    (which actually removes the provider's bytes) with `?permanent=true`
    -- requires the resource to already be soft-deleted, see
    `ResourceService.hard_delete`."""
    service = _service(request)
    actor_user_id = _actor(request)
    if permanent:
        await service.hard_delete(uid, actor_user_id=actor_user_id)
    else:
        await service.soft_delete(uid, actor_user_id=actor_user_id)
    return Response(status_code=204)


# ----------------------------------------------------------------------
# Public link alias
# ----------------------------------------------------------------------


async def _optional_actor(request: Request) -> str | None:
    """`/f/{uid}` is reachable without a session (`jwt_guard` skips it),
    so the route resolves the caller itself -- `None` for anonymous.
    Visibility (`public_permission == "read"` OR an ACL grant) is the
    service's decision (`get_via_public_link`), not this route's."""
    user = await resolve_optional_user(request)
    return None if user is None else user.uid


@public_router.get("/{uid}")
async def get_public_link(
    uid: str, request: Request, details: bool = False,
) -> Response:
    service = _service(request)
    actor_user_id = await _optional_actor(request)
    if details:
        record = await service.get_via_public_link(
            uid, actor_user_id=actor_user_id,
        )
        return JSONResponse(ResourceOut.from_record(record).model_dump(mode="json"))
    return await _content_response(
        service, uid, request, actor_user_id=actor_user_id, via_public_link=True,
    )


@public_router.head("/{uid}")
async def head_public_link(uid: str, request: Request) -> Response:
    service = _service(request)
    record = await service.get_via_public_link(
        uid, actor_user_id=await _optional_actor(request),
    )
    return Response(headers=_metadata_headers(record))
