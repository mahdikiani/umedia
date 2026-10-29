"""One catch-all route: WebDAV verbs are dispatched by method."""

import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response

from apps.media_files.errors import (
    MediaFileNotFoundError,
    MediaFilePermissionError,
    MediaFileValidationError,
    MediaFileWriteFailedError,
)
from apps.media_files.factory import build_media_file_service_from_state
from server.config import Settings

from .auth import CHALLENGE, UnauthorizedError, authenticate
from .locks import LockTable
from .paths import BadPathError, mount_relative, split_path
from .service import ALLOWED, DavError, DavService

router = APIRouter(include_in_schema=False)

METHODS = [
    "OPTIONS", "GET", "HEAD", "PUT", "DELETE", "MKCOL", "COPY", "MOVE",
    "PROPFIND", "PROPPATCH", "LOCK", "UNLOCK",
]
_SAFE_PREFIX = re.compile(r"^/[A-Za-z0-9._~\-/]*$")
#: One process-wide table; see apps/webdav/locks.py for the scope.
_LOCKS = LockTable()


def _public_base(request: Request) -> str:
    """Client-visible mount URL path. A proxy that strips a route prefix
    (shared domain) tells us the original one via `X-Forwarded-Prefix`."""
    forwarded = request.headers.get("x-forwarded-prefix", "").strip().rstrip("/")
    outer = forwarded if _SAFE_PREFIX.match(forwarded) else ""
    outer = outer or request.scope.get("root_path", "").rstrip("/")
    return outer + Settings.WEBDAV_PREFIX


def _local_path(request: Request) -> str:
    path = request.scope["path"]
    root = request.scope.get("root_path", "").rstrip("/")
    return path.removeprefix(root) if root else path


def _options() -> Response:
    return Response(
        status_code=200,
        headers={
            "DAV": "1, 2",
            "Allow": ALLOWED,
            "MS-Author-Via": "DAV",
            "Content-Length": "0",
        },
    )


def _unauthorized() -> Response:
    return Response(
        b"Authentication required",
        status_code=401,
        media_type="text/plain",
        headers={"WWW-Authenticate": CHALLENGE},
    )


def _service(request: Request, identity: object, base: str) -> DavService:
    state = request.app.state
    return DavService(
        user_id=identity.user_id,
        is_admin=identity.is_admin,
        media=build_media_file_service_from_state(state),
        locks=_LOCKS,
        base=base,
        host=request.headers.get("host"),
        staging_dir=Path(state.settings.tus_upload_dir),
        max_upload=Settings.WEBDAV_MAX_UPLOAD_BYTES,
        lock_timeout=Settings.WEBDAV_LOCK_TIMEOUT_SECONDS,
    )


async def _dispatch(
    service: DavService, request: Request, segments: list[str],
) -> Response:
    method = request.method
    if method in {"GET", "HEAD"}:
        return await service.get(request, segments, head=method == "HEAD")
    handlers = {
        "PROPFIND": service.propfind,
        "PROPPATCH": service.proppatch,
        "PUT": service.put,
        "MKCOL": service.mkcol,
        "DELETE": service.delete,
        "MOVE": service.move,
        "COPY": service.copy,
        "LOCK": service.lock,
    }
    if method == "UNLOCK":
        return await service.unlock(request)
    return await handlers[method](request, segments)


@router.api_route("", methods=METHODS)
@router.api_route("/{path:path}", methods=METHODS)
async def webdav(request: Request, path: str = "") -> Response:
    if not Settings.WEBDAV_ENABLED:
        return Response(status_code=404)
    if request.method == "OPTIONS":
        return _options()
    try:
        identity = await authenticate(request)
    except UnauthorizedError:
        return _unauthorized()
    base = _public_base(request)
    relative = mount_relative(_local_path(request), Settings.WEBDAV_PREFIX)
    try:
        segments = split_path(relative or "")
    except BadPathError:
        return DavError(400, "Invalid path").response()
    try:
        return await _dispatch(_service(request, identity, base), request, segments)
    except DavError as error:
        return error.response()
    except MediaFileNotFoundError:
        return DavError(404, "Not found").response()
    except MediaFilePermissionError as error:
        return DavError(403, str(error.detail)).response()
    except MediaFileValidationError as error:
        return DavError(409, str(error.detail)).response()
    except MediaFileWriteFailedError as error:
        return DavError(502, str(error.detail)).response()
