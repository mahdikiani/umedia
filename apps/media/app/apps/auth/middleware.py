"""Central auth enforcement for the versioned API.

Delegates token/session verification entirely to usso.lite's own
`resolve_current_user` (see `docs/02-architecture.md` "Tooling reuse, not
reinvention") -- this module only decides which paths are public, wires
the result into `request.state.user`, and provides the `require_admin`
route dependency for admin-only endpoints.
"""

import re
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from fastapi_mongo_base.core.exceptions import BaseHTTPException
from usso.exceptions import USSOException
from usso.lite.dependency import resolve_current_user
from usso.lite.models import LocalUser

# `/f/{uid}` and `/f/{uid}/{filename}` share links: readable without a
# session so an anonymous recipient can open one -- the route itself
# enforces the actual gate (the resource's own `public_permission`, see
# docs/04-data-model.md), resolving the caller optionally via
# `resolve_optional_user` below so an already-logged-in user can still
# open a *non*-public resource's link. The trailing `{filename}` is
# cosmetic (browsers / download UX); only `{uid}` is authoritative.
# Only safe (read) methods qualify; anything else on this prefix 404s
# anyway since no such route exists.
_PUBLIC_LINK_RE = re.compile(r"^/api/v1/f/[^/]+(?:/[^/]+)?$")

# The S3-compatible gateway carries its *own* authentication -- every
# request is SigV4-verified against a per-user access key inside the
# routes' `s3_auth` dependency (apps/s3/auth.py), so the session guard
# must stay out of the way for every method: an S3 client never has a
# session cookie, and presigned GETs are anonymous by design.
_S3_GATEWAY_PREFIX = "/api/v1/s3"

PUBLIC_EXACT_PATHS = {
    "/api/v1/health",
    "/api/v1/auth/state",
    "/api/v1/auth/setup",
    "/api/v1/auth/sessions",
    # The docs page/schema, not just the auth bootstrap routes: an admin
    # has no session the first time they open the app, so if these aren't
    # public the docs page can't even fetch its own schema to render --
    # found live (https://umedia.uln.me/api/v1/docs 401ing on load). The
    # schema itself isn't sensitive, it's just the API's shape.
    "/api/v1/docs",
    "/api/v1/openapi.json",
    "/api/v1/redoc",
}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _is_public(request: Request) -> bool:
    if request.method == "OPTIONS":
        return True
    if request.url.path in PUBLIC_EXACT_PATHS:
        return True
    path = request.url.path
    if path == _S3_GATEWAY_PREFIX or path.startswith(_S3_GATEWAY_PREFIX + "/"):
        return True
    return (
        request.method in SAFE_METHODS
        and bool(_PUBLIC_LINK_RE.match(request.url.path))
    )


async def resolve_optional_user(request: Request) -> LocalUser | None:
    """The current user, or `None` if there isn't a valid session.

    For routes reachable both with and without auth (currently only
    `/f/{uid}`, see `_PUBLIC_LINK_RE` above) -- `jwt_guard` skips session
    resolution entirely for public paths, so such a route must resolve it
    itself, same as `/auth/state` already does for the same reason.
    """
    service = request.app.state.auth_service
    async with service.database.session_maker() as session:
        try:
            return await resolve_current_user(request, session, service.lite_auth)
        except USSOException:
            return None


#: Kept as an alias from the single-administrator era; new code should
#: use `resolve_optional_user`.
resolve_optional_admin = resolve_optional_user


def require_admin(request: Request) -> LocalUser:
    """Route dependency: the current user, required to hold "admin".

    `jwt_guard` has already authenticated every private route, so this
    only checks the role -- 403, not 401, is the correct answer for a
    valid session lacking the privilege.
    """
    user = getattr(request.state, "user", None)
    if user is None or "admin" not in (user.roles or []):
        raise BaseHTTPException(
            status_code=403,
            error_code="admin_required",
            detail="Administrator role is required",
            message="Administrator role is required",
        )
    return user


def _origin_is_valid(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    return urlsplit(origin).hostname == request.url.hostname


async def jwt_guard(request: Request, call_next: object) -> Response:
    """Require a current usso.lite session for every private API route."""
    if request.method not in SAFE_METHODS and not _origin_is_valid(request):
        return JSONResponse(
            status_code=403,
            content={
                "code": "invalid_origin",
                "message": "Request origin is not allowed",
                "details": {},
            },
        )

    if not request.url.path.startswith("/api/v1") or _is_public(request):
        return await call_next(request)

    service = request.app.state.auth_service
    async with service.database.session_maker() as session:
        try:
            user = await resolve_current_user(request, session, service.lite_auth)
        except USSOException:
            return _unauthorized()
        request.state.user = user
        # Compatibility alias from the single-administrator era; new code
        # should read `request.state.user`.
        request.state.admin = user
        return await call_next(request)


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={
            "code": "authentication_required",
            "message": "A valid administrator session is required",
            "details": {},
        },
        headers={"WWW-Authenticate": "Bearer"},
    )
