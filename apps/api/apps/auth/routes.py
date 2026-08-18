"""REST authentication routes."""

import time

from fastapi import APIRouter, Request, Response, status

from .repository import AuthRepository
from .schemas import (
    AuthStateResponse,
    CredentialsRequest,
    PasswordChangeRequest,
    SessionResponse,
)
from .security import InvalidSessionError, JwtManager, PasswordHasher
from .services import AuthenticationError, AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])
COOKIE_NAME = "umedia_session"
SESSION_TTL_SECONDS = 60 * 60 * 24


def _components(request: Request) -> tuple[AuthService, JwtManager]:
    repository = AuthRepository(request.app.state.session_factory)
    service = AuthService(repository, PasswordHasher())
    signer = JwtManager(
        request.app.state.credential_cipher.key,
        ttl_seconds=SESSION_TTL_SECONDS,
    )
    return service, signer


def _set_session(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )


async def _require_authentication(request: Request) -> tuple[AuthService, int]:
    service, signer = _components(request)
    state = await service.get_state()
    token = request.cookies.get(COOKIE_NAME)
    if state is None or token is None:
        raise AuthenticationError
    _, _, password_version = state
    try:
        claims = signer.verify(
            token,
            password_version=password_version,
            now=int(time.time()),
        )
    except InvalidSessionError as error:
        raise AuthenticationError from error
    request.state.admin = claims
    return service, password_version


@router.get("/state", response_model=AuthStateResponse)
async def get_auth_state(request: Request) -> AuthStateResponse:
    """Return setup and current-session state."""
    service, signer = _components(request)
    state = await service.get_state()
    if state is None:
        return AuthStateResponse(configured=False, authenticated=False)
    _, _, password_version = state
    token = request.cookies.get(COOKIE_NAME)
    authenticated = False
    if token:
        try:
            signer.verify(
                token,
                password_version=password_version,
                now=int(time.time()),
            )
            authenticated = True
        except InvalidSessionError:
            pass
    return AuthStateResponse(configured=True, authenticated=authenticated)


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
    """Configure the first administrator password."""
    service, signer = _components(request)
    email, _, version = await service.setup(data.email, data.password)
    token = signer.create(
        admin_id="installation",
        email=email,
        password_version=version,
        now=int(time.time()),
    )
    _set_session(response, token)
    return SessionResponse(
        configured=True,
        authenticated=True,
        access_token=token,
        expires_in=SESSION_TTL_SECONDS,
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
    service, signer = _components(request)
    version = await service.authenticate(data.email, data.password)
    token = signer.create(
        admin_id="installation",
        email=str(data.email).casefold(),
        password_version=version,
        now=int(time.time()),
    )
    _set_session(response, token)
    return SessionResponse(
        configured=True,
        authenticated=True,
        access_token=token,
        expires_in=SESSION_TTL_SECONDS,
    )


@router.delete("/sessions/current", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(response: Response) -> None:
    """End the current browser session."""
    response.delete_cookie(COOKIE_NAME, path="/")


@router.patch("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    data: PasswordChangeRequest,
    request: Request,
    response: Response,
) -> None:
    """Change the administrator password and rotate the session."""
    service, _ = await _require_authentication(request)
    _, signer = _components(request)
    version = await service.change_password(
        current_password=data.current_password,
        new_password=data.new_password,
    )
    _set_session(
        response,
        signer.create(
            admin_id="installation",
            email=(await service.get_state() or ("", "", 0))[0],
            password_version=version,
            now=int(time.time()),
        ),
    )
