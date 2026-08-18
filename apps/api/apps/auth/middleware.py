"""Central JWT enforcement for the versioned API."""

import time
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from .repository import AuthRepository
from .routes import COOKIE_NAME, _components
from .security import InvalidSessionError

PUBLIC_EXACT_PATHS = {
    "/api/v1/health",
    "/api/v1/ready",
    "/api/v1/auth/state",
    "/api/v1/auth/setup",
    "/api/v1/auth/sessions",
}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _is_public(request: Request) -> bool:
    if request.method == "OPTIONS":
        return True
    if request.url.path in PUBLIC_EXACT_PATHS:
        return True
    return request.method in {"GET", "HEAD"} and request.url.path.startswith(
        "/api/v1/shares/public/",
    )


def _token_from_request(request: Request) -> tuple[str | None, bool]:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.casefold() == "bearer" and token:
        return token, False
    return request.cookies.get(COOKIE_NAME), True


def _origin_is_valid(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    return urlsplit(origin).hostname == request.url.hostname


async def jwt_guard(request: Request, call_next: object) -> Response:
    """Require a current JWT for every private API route."""
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

    token, from_cookie = _token_from_request(request)
    if token is None:
        return _unauthorized()

    repository = AuthRepository(request.app.state.session_factory)
    state = await repository.get()
    if state is None:
        return _unauthorized()
    email, _, password_version = state
    _, manager = _components(request)
    try:
        claims = manager.verify(
            token,
            password_version=password_version,
            now=int(time.time()),
        )
    except InvalidSessionError:
        return _unauthorized()
    if claims.get("email") != email:
        return _unauthorized()
    request.state.admin = claims
    request.state.authenticated_with_cookie = from_cookie
    return await call_next(request)


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={
            "code": "authentication_required",
            "message": "A valid administrator JWT is required",
            "details": {},
        },
        headers={"WWW-Authenticate": "Bearer"},
    )
