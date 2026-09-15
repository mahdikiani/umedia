"""REST authentication routes, backed by usso.lite."""

from urllib.parse import quote

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from usso.exceptions import USSOException
from usso.lite.dependency import resolve_current_user
from usso.lite.router import REFRESH_COOKIE_NAME
from usso.lite.schemas import TokenPair

from .config import REFRESH_TOKEN_DAYS
from .schemas import (
    AuthStateResponse,
    AuthStateUser,
    CredentialsRequest,
    CurrentSessionResponse,
    OidcCompleteRequest,
    OidcStartRequest,
    OidcStartResponse,
    PasswordChangeRequest,
    RefreshRequest,
    RefreshResponse,
    SessionResponse,
)
from .services import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])

# Named "usso-access-token" so usso.lite's own `resolve_current_user` (used
# unchanged by middleware.py) finds it without any custom extraction code.
ACCESS_COOKIE_NAME = "usso-access-token"
# Pre-0.32.9 installs issued this name; keep reading/clearing it until
# every browser session has rotated through login or refresh once.
LEGACY_REFRESH_COOKIE_NAME = "umedia_refresh"
REFRESH_COOKIE_MAX_AGE = REFRESH_TOKEN_DAYS * 24 * 60 * 60


def _service(request: Request) -> AuthService:
    return request.app.state.auth_service


def _client_info(request: Request) -> tuple[str | None, str | None]:
    return request.headers.get("user-agent"), (
        request.client.host if request.client else None
    )


def _set_session_cookies(response: Response, pair: TokenPair) -> None:
    response.set_cookie(
        ACCESS_COOKIE_NAME,
        pair.access_token,
        max_age=pair.expires_in,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        pair.refresh_token,
        max_age=REFRESH_COOKIE_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def _clear_session_cookies(response: Response) -> None:
    # Emit both Secure and non-Secure clears: login Set-Cookie uses
    # Secure=True, but tests (and some clients) seed the jar without it,
    # and httpx only drops entries whose flags match the delete header.
    for name in (
        ACCESS_COOKIE_NAME,
        REFRESH_COOKIE_NAME,
        LEGACY_REFRESH_COOKIE_NAME,
    ):
        response.delete_cookie(name, path="/")
        response.delete_cookie(
            name,
            path="/",
            secure=True,
            httponly=True,
            samesite="lax",
        )


def _usso_error_response(exc: USSOException) -> JSONResponse:
    """Build a USSO error body and clear session cookies on the same response.

    Re-raising into the global USSO handler would drop Set-Cookie headers
    written on the injected FastAPI Response.
    """
    json_response = JSONResponse(
        status_code=exc.status_code,
        content={
            "message": exc.message,
            "error_code": exc.error_code,
            "detail": exc.detail,
            **exc.data,
        },
    )
    _clear_session_cookies(json_response)
    return json_response


def _refresh_token_from_request(
    request: Request,
    data: RefreshRequest | None,
) -> str | None:
    if data is not None and data.refresh_token:
        return data.refresh_token
    return request.cookies.get(REFRESH_COOKIE_NAME) or request.cookies.get(
        LEGACY_REFRESH_COOKIE_NAME,
    )


async def _build_refresh_response(
    service: AuthService,
    *,
    refresh_token: str,
    include_tokens_in_json: bool,
) -> tuple[RefreshResponse, TokenPair]:
    pair, user = await service.refresh(refresh_token)
    state_user = await _state_user(service, user.uid)
    return (
        RefreshResponse(
            configured=True,
            authenticated=True,
            user=state_user,
            status="refreshed",
            access_token=pair.access_token if include_tokens_in_json else None,
            token_type="bearer" if include_tokens_in_json else None,
            expires_in=pair.expires_in if include_tokens_in_json else None,
        ),
        pair,
    )


@router.get(
    "/refresh",
    response_model=RefreshResponse,
    response_model_exclude_none=True,
)
async def refresh_from_cookie(
    request: Request,
    response: Response,
) -> JSONResponse:
    """Refresh access using the HttpOnly refresh cookie (browser sessions)."""
    refresh_token = _refresh_token_from_request(request, None)
    if not refresh_token:
        return _usso_error_response(
            USSOException(
                401,
                error_code="invalid_refresh_token",
                message={
                    "en": "Refresh token is required.",
                    "fa": "توکن تازه‌سازی الزامی است.",
                },
            ),
        )
    try:
        body, pair = await _build_refresh_response(
            _service(request),
            refresh_token=refresh_token,
            include_tokens_in_json=False,
        )
        json_response = JSONResponse(content=body.model_dump(exclude_none=True))
        _set_session_cookies(json_response, pair)
        return json_response
    except USSOException as exc:
        return _usso_error_response(exc)


@router.post(
    "/refresh",
    response_model=RefreshResponse,
    response_model_exclude_none=True,
)
async def refresh_from_cookie_or_body(
    request: Request,
    response: Response,
    data: RefreshRequest | None = None,
) -> JSONResponse:
    """Refresh access using a body token or the HttpOnly refresh cookie."""
    refresh_token = _refresh_token_from_request(request, data)
    if not refresh_token:
        return _usso_error_response(
            USSOException(
                401,
                error_code="invalid_refresh_token",
                message={
                    "en": "Refresh token is required.",
                    "fa": "توکن تازه‌سازی الزامی است.",
                },
            ),
        )
    include_tokens = data is not None and bool(data.refresh_token)
    try:
        body, pair = await _build_refresh_response(
            _service(request),
            refresh_token=refresh_token,
            include_tokens_in_json=include_tokens,
        )
        json_response = JSONResponse(content=body.model_dump(exclude_none=True))
        _set_session_cookies(json_response, pair)
        return json_response
    except USSOException as exc:
        return _usso_error_response(exc)


async def _state_user(service: AuthService, uid: str) -> AuthStateUser | None:
    """Build the `/auth/state` user block (usso.lite keeps the email on
    identifier rows, not the user row, so this is a summary lookup)."""
    summary = await service.get_user_summary(uid)
    if summary is None:
        return None
    return AuthStateUser(
        uid=summary.uid,
        email=summary.email,
        roles=summary.roles,
    )


@router.get("/state", response_model=AuthStateResponse)
async def get_auth_state(
    request: Request,
    response: Response,
) -> AuthStateResponse:
    """Return setup and current-session state.

    `/auth/state` is a public route (jwt_guard lets unauthenticated callers
    through so they can discover whether setup/login is needed), so it
    can't rely on the middleware having already populated
    `request.state.user` the way private routes do -- it verifies the
    cookie itself, the same way the middleware would.
    """
    service = _service(request)
    configured = await service.is_configured()
    user = None
    if configured:
        async with service.database.session_maker() as session:
            try:
                resolved = await resolve_current_user(
                    request, session, service.lite_auth,
                )
            except USSOException:
                resolved = None
        if resolved is not None:
            user = await _state_user(service, resolved.uid)
        refresh_token = _refresh_token_from_request(request, None)
        if user is None and refresh_token:
            try:
                pair, refreshed_user = await service.refresh(refresh_token)
            except Exception:
                user = None
            else:
                user = await _state_user(service, refreshed_user.uid)
                _set_session_cookies(response, pair)
    return AuthStateResponse(
        configured=configured,
        authenticated=user is not None,
        user=user,
        oidc_providers=service.oidc_providers(),
    )


@router.post("/oidc/start", response_model=OidcStartResponse)
async def oidc_start(
    data: OidcStartRequest,
    request: Request,
) -> OidcStartResponse:
    """Begin OIDC identity login; return the authorize URL."""
    result = _service(request).start_oidc(data.provider)
    return OidcStartResponse(**result)


@router.get("/oidc/callback")
async def oidc_callback(request: Request) -> RedirectResponse:
    """Complete OIDC login after Google redirects back to this installation."""
    error = request.query_params.get("error")
    if error:
        description = request.query_params.get("error_description") or error
        return RedirectResponse(
            f"/login?oidc_error={quote(description, safe='')}",
            status_code=status.HTTP_302_FOUND,
        )

    state = request.query_params.get("state")
    if not state:
        return RedirectResponse(
            "/login?oidc_error=missing_state",
            status_code=status.HTTP_302_FOUND,
        )

    service = _service(request)
    user_agent, ip = _client_info(request)
    try:
        pair, _user = await service.login_with_oidc(
            provider="google",
            callback=str(request.url),
            state=state,
            user_agent=user_agent,
            ip=ip,
        )
    except USSOException as exc:
        message = exc.message.get("en") or exc.error_code or "oidc_failed"
        return RedirectResponse(
            f"/login?oidc_error={quote(str(message), safe='')}",
            status_code=status.HTTP_302_FOUND,
        )

    response = RedirectResponse("/files", status_code=status.HTTP_302_FOUND)
    _set_session_cookies(response, pair)
    return response


@router.post(
    "/oidc/complete",
    status_code=status.HTTP_201_CREATED,
    response_model=SessionResponse,
)
async def oidc_complete(
    data: OidcCompleteRequest,
    request: Request,
    response: Response,
) -> SessionResponse:
    """Complete OIDC identity login from a pasted callback; set session cookies."""
    service = _service(request)
    user_agent, ip = _client_info(request)
    pair, user = await service.login_with_oidc(
        provider=data.provider,
        callback=data.callback,
        state=data.state,
        user_agent=user_agent,
        ip=ip,
    )
    _set_session_cookies(response, pair)
    return SessionResponse(
        configured=True,
        authenticated=True,
        user=await _state_user(service, user.uid),
        access_token=pair.access_token,
        expires_in=pair.expires_in,
        oidc_providers=service.oidc_providers(),
    )


@router.post(
    "/setup",
    status_code=status.HTTP_201_CREATED,
    response_model=SessionResponse,
)
async def setup(
    data: CredentialsRequest,
    request: Request,
    response: Response,
) -> SessionResponse:
    """Configure the first (and only) administrator password."""
    service = _service(request)
    user_agent, ip = _client_info(request)
    pair, user = await service.setup(
        str(data.email),
        data.password,
        user_agent=user_agent,
        ip=ip,
    )
    _set_session_cookies(response, pair)
    return SessionResponse(
        configured=True,
        authenticated=True,
        user=await _state_user(service, user.uid),
        access_token=pair.access_token,
        expires_in=pair.expires_in,
    )


@router.post(
    "/sessions",
    status_code=status.HTTP_201_CREATED,
    response_model=SessionResponse,
)
async def create_session(
    data: CredentialsRequest,
    request: Request,
    response: Response,
) -> SessionResponse:
    """Create an authenticated browser session."""
    service = _service(request)
    user_agent, ip = _client_info(request)
    pair, user = await service.login(
        str(data.email),
        data.password,
        user_agent=user_agent,
        ip=ip,
    )
    _set_session_cookies(response, pair)
    return SessionResponse(
        configured=True,
        authenticated=True,
        user=await _state_user(service, user.uid),
        access_token=pair.access_token,
        expires_in=pair.expires_in,
    )


@router.get("/sessions/current", response_model=CurrentSessionResponse)
async def get_current_session(request: Request) -> CurrentSessionResponse:
    """Return who the current session belongs to."""
    summary = await _service(request).get_user_summary(request.state.user.uid)
    return CurrentSessionResponse(
        uid=summary.uid,
        email=summary.email,
        roles=summary.roles,
        name=summary.name,
    )


@router.delete("/sessions/current", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(request: Request, response: Response) -> None:
    """End the current browser session."""
    service = _service(request)
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME) or request.cookies.get(
        LEGACY_REFRESH_COOKIE_NAME,
    )
    await service.logout(refresh_token)
    _clear_session_cookies(response)


@router.patch("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    data: PasswordChangeRequest,
    request: Request,
    response: Response,
) -> None:
    """Change the administrator password.

    Every session -- including this one -- is invalidated as part of this
    (usso.lite's behavior); cookies are cleared so the browser is
    consistent with that instead of holding a now-dead access token.
    """
    service = _service(request)
    user = request.state.user
    await service.change_password(
        user.uid,
        current_password=data.current_password,
        new_password=data.new_password,
    )
    _clear_session_cookies(response)
