"""REST authentication routes, backed by usso.lite."""

from fastapi import APIRouter, Request, Response, status
from usso.exceptions import USSOException
from usso.lite.dependency import resolve_current_user

from .schemas import (
    AuthStateResponse,
    AuthStateUser,
    CredentialsRequest,
    CurrentSessionResponse,
    PasswordChangeRequest,
    SessionResponse,
)
from .services import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])

# Named "usso-access-token" so usso.lite's own `resolve_current_user` (used
# unchanged by middleware.py) finds it without any custom extraction code.
ACCESS_COOKIE_NAME = "usso-access-token"
REFRESH_COOKIE_NAME = "umedia_refresh"


def _service(request: Request) -> AuthService:
    return request.app.state.auth_service


def _client_info(request: Request) -> tuple[str | None, str | None]:
    return request.headers.get("user-agent"), (
        request.client.host if request.client else None
    )


def _set_session_cookies(response: Response, pair: object) -> None:
    response.set_cookie(
        ACCESS_COOKIE_NAME,
        pair.access_token,
        max_age=pair.expires_in,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        pair.refresh_token,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE_NAME, path="/")
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/")


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
async def get_auth_state(request: Request) -> AuthStateResponse:
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
    return AuthStateResponse(
        configured=configured,
        authenticated=user is not None,
        user=user,
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
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
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
