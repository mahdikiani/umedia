"""OIDC identity login (usso.lite) — not Google Drive storage OAuth."""

from collections.abc import AsyncGenerator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from starlette.requests import Request
from usso.exceptions import USSOException
from usso.lite import LiteConfig, OidcProviderConfig

from apps.auth.config import build_lite_config, resolve_oidc_redirect_uri
from apps.auth.middleware import _is_public
from apps.auth.services import AuthService
from apps.user_access_keys.factory import build_user_access_key_service_from_state
from server.config import Settings


def _request(path: str, method: str = "POST") -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [],
            "query_string": b"",
            "server": ("test.uln.me", 443),
            "scheme": "https",
        },
    )


def _mock_settings(
    *,
    client_id: str = "",
    client_secret: str = "",
    redirect_uri: str = "http://localhost",
    database_uri: str = "sqlite+aiosqlite:///:memory:",
) -> SimpleNamespace:
    return SimpleNamespace(
        database_uri=database_uri,
        google_oauth_client_id=client_id,
        google_oauth_client_secret=client_secret,
        google_oauth_redirect_uri=redirect_uri,
        google_oidc_redirect_uri="",
    )


def _mock_http(*, userinfo: dict) -> MagicMock:
    token_resp = MagicMock()
    token_resp.raise_for_status = MagicMock()
    token_resp.json.return_value = {"access_token": "at", "token_type": "Bearer"}
    token_resp.is_success = True
    token_resp.status_code = 200
    info_resp = MagicMock()
    info_resp.raise_for_status = MagicMock()
    info_resp.json.return_value = userinfo
    info_resp.is_success = True
    info_resp.status_code = 200
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    client.post.return_value = token_resp
    client.get.return_value = info_resp
    return client


def test_build_lite_config_omits_oidc_without_google_credentials() -> None:
    config = build_lite_config(_mock_settings())
    assert config.oidc_providers == {}
    assert config.oidc_allow_signup is False
    assert config.allow_registration is False


def test_build_lite_config_adds_google_oidc_when_credentials_set() -> None:
    config = build_lite_config(
        _mock_settings(client_id="cid", client_secret="csecret"),
    )
    assert "google" in config.oidc_providers
    google = config.oidc_providers["google"]
    assert google.client_id == "cid"
    assert google.client_secret == "csecret"  # noqa: S105
    assert google.redirect_uri == "http://localhost"
    assert config.oidc_allow_signup is False
    assert config.oidc_default_roles == ["user"]


def test_oidc_start_and_complete_paths_are_public() -> None:
    assert _is_public(_request("/api/v1/auth/oidc/start"))
    assert _is_public(_request("/api/v1/auth/oidc/complete"))
    assert _is_public(_request("/api/v1/auth/oidc/callback", method="GET"))


def test_resolve_oidc_redirect_uri_prefers_oidc_specific_env() -> None:
    settings = _mock_settings(redirect_uri="http://localhost")
    settings.google_oidc_redirect_uri = (
        "https://umedia.uln.me/api/v1/auth/oidc/callback"
    )
    assert (
        resolve_oidc_redirect_uri(settings)
        == "https://umedia.uln.me/api/v1/auth/oidc/callback"
    )


def test_resolve_oidc_redirect_uri_falls_back_to_drive_redirect() -> None:
    settings = _mock_settings(redirect_uri="http://localhost")
    assert resolve_oidc_redirect_uri(settings) == "http://localhost"


@pytest_asyncio.fixture
async def oidc_auth_service(tmp_path: Path) -> AsyncGenerator[AuthService]:
    config = LiteConfig(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'oidc-auth.sqlite3'}",
        issuer="umedia-test",
        audience="umedia-test",
        access_token_minutes=60 * 24,
        refresh_token_days=30,
        allow_registration=False,
        oidc_providers={
            "google": OidcProviderConfig(
                client_id="cid",
                client_secret="csecret",
                redirect_uri="http://localhost",
            ),
        },
        oidc_allow_signup=False,
        oidc_default_roles=["user"],
    )
    service = AuthService(config)
    await service.ensure_initialized()
    yield service
    await service.dispose()


def test_oidc_available_and_provider_list(
    oidc_auth_service: AuthService,
) -> None:
    assert oidc_auth_service.oidc_available() is True
    assert oidc_auth_service.oidc_providers() == ["google"]


def test_start_oidc_returns_authorize_url(
    oidc_auth_service: AuthService,
) -> None:
    started = oidc_auth_service.start_oidc("google")
    assert started["provider"] == "google"
    assert started["state"]
    assert started["redirect_uri"] == "http://localhost"
    assert "accounts.google.com" in started["authorization_url"]
    assert "openid" in started["authorization_url"]
    assert "drive" not in started["authorization_url"].lower()


@pytest.mark.asyncio
async def test_login_with_oidc_existing_user(
    oidc_auth_service: AuthService,
) -> None:
    await oidc_auth_service.setup(
        "ali@example.com",
        "a secure first password",
        user_agent=None,
        ip=None,
    )
    started = oidc_auth_service.start_oidc("google")
    client = _mock_http(
        userinfo={
            "email": "ali@example.com",
            "email_verified": True,
            "name": "Ali",
        },
    )
    with patch("usso.lite.oidc.httpx.AsyncClient", return_value=client):
        result = await oidc_auth_service.login_with_oidc(
            provider="google",
            callback=f"http://localhost/?code=abc&state={started['state']}",
            state=started["state"],
            user_agent="pytest",
            ip="127.0.0.1",
        )
    assert result.pair.access_token
    assert result.user.is_active is True


@pytest.mark.asyncio
async def test_login_with_oidc_unknown_email_fails_when_signup_disabled(
    oidc_auth_service: AuthService,
) -> None:
    await oidc_auth_service.setup(
        "admin@example.com",
        "a secure first password",
        user_agent=None,
        ip=None,
    )
    started = oidc_auth_service.start_oidc("google")
    client = _mock_http(
        userinfo={
            "email": "unknown@example.com",
            "email_verified": True,
        },
    )
    with (
        patch("usso.lite.oidc.httpx.AsyncClient", return_value=client),
        pytest.raises(USSOException) as exc_info,
    ):
        await oidc_auth_service.login_with_oidc(
            provider="google",
            callback=f"http://localhost/?code=abc&state={started['state']}",
            state=started["state"],
            user_agent=None,
            ip=None,
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.error_code == "oidc_user_not_found"


@pytest_asyncio.fixture
async def oidc_http_client(
    client: httpx.AsyncClient,
) -> AsyncGenerator[httpx.AsyncClient]:
    """Reuse the shared ASGI client; swap AuthService for one with Google OIDC.

    Nested LifespanManager on the process-wide app times out (startup already
    ran for the module-scoped ``client`` fixture). Drive OAuth tests mutate
    ``app.state.settings`` the same way — OIDC providers are baked into
    LiteConfig at AuthService construction, so we rebuild that service.
    """
    app = client._transport.app  # type: ignore[attr-defined]
    settings = app.state.settings
    previous_service = app.state.auth_service
    previous_id = settings.google_oauth_client_id
    previous_secret = settings.google_oauth_client_secret
    previous_redirect = settings.google_oauth_redirect_uri

    settings.google_oauth_client_id = "cid"
    settings.google_oauth_client_secret = "csecret"  # noqa: S105
    settings.google_oauth_redirect_uri = "http://localhost"
    replacement = AuthService(
        build_lite_config(settings),
        access_keys=build_user_access_key_service_from_state(app.state),
    )
    await replacement.ensure_initialized()
    app.state.auth_service = replacement
    try:
        yield client
    finally:
        app.state.auth_service = previous_service
        settings.google_oauth_client_id = previous_id
        settings.google_oauth_client_secret = previous_secret
        settings.google_oauth_redirect_uri = previous_redirect
        await replacement.dispose()


async def _ensure_user(
    client: httpx.AsyncClient,
    email: str,
    password: str,
) -> None:
    """Create ``email`` if missing (setup when empty, else AuthService.create_user)."""
    app = client._transport.app  # type: ignore[attr-defined]
    service: AuthService = app.state.auth_service
    if not await service.is_configured():
        await service.setup(email, password, user_agent=None, ip=None)
        return
    existing = {user.email for user in await service.list_users()}
    if email not in existing:
        await service.create_user(
            email=email,
            password=password,
            role="user",
            name=None,
        )


@pytest.mark.asyncio
async def test_auth_state_lists_oidc_providers(
    oidc_http_client: httpx.AsyncClient,
) -> None:
    response = await oidc_http_client.get("/auth/state")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["oidc_providers"] == ["google"]


@pytest.mark.asyncio
async def test_oidc_start_route_returns_url(
    oidc_http_client: httpx.AsyncClient,
) -> None:
    response = await oidc_http_client.post(
        "/auth/oidc/start",
        json={"provider": "google"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "google"
    assert body["authorization_url"]
    assert body["state"]
    assert body["redirect_uri"] == "http://localhost"


@pytest.mark.asyncio
async def test_oidc_complete_route_logs_in_existing_user(
    oidc_http_client: httpx.AsyncClient,
) -> None:
    await _ensure_user(
        oidc_http_client,
        "ali@example.com",
        "a secure first password",
    )
    await oidc_http_client.delete("/auth/sessions/current")

    started = (
        await oidc_http_client.post(
            "/auth/oidc/start",
            json={"provider": "google"},
        )
    ).json()
    client = _mock_http(
        userinfo={
            "email": "ali@example.com",
            "email_verified": True,
            "name": "Ali",
        },
    )
    with patch("usso.lite.oidc.httpx.AsyncClient", return_value=client):
        response = await oidc_http_client.post(
            "/auth/oidc/complete",
            json={
                "provider": "google",
                "callback": (
                    f"http://localhost/?code=abc&state={started['state']}"
                ),
                "state": started["state"],
            },
        )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["authenticated"] is True
    assert body["user"]["email"] == "ali@example.com"
    assert "usso-access-token" in response.cookies


@pytest.mark.asyncio
async def test_oidc_complete_unknown_email_is_forbidden(
    oidc_http_client: httpx.AsyncClient,
) -> None:
    await _ensure_user(
        oidc_http_client,
        "admin@example.com",
        "a secure first password",
    )
    await oidc_http_client.delete("/auth/sessions/current")

    started = (
        await oidc_http_client.post(
            "/auth/oidc/start",
            json={"provider": "google"},
        )
    ).json()
    client = _mock_http(
        userinfo={
            "email": "stranger@example.com",
            "email_verified": True,
        },
    )
    with patch("usso.lite.oidc.httpx.AsyncClient", return_value=client):
        response = await oidc_http_client.post(
            "/auth/oidc/complete",
            json={
                "provider": "google",
                "callback": (
                    f"http://localhost/?code=abc&state={started['state']}"
                ),
                "state": started["state"],
            },
        )
    assert response.status_code == 403, response.text


async def _swap_oidc_redirect(
    client: httpx.AsyncClient,
    redirect_uri: str,
) -> AuthService:
    app = client._transport.app  # type: ignore[attr-defined]
    settings = app.state.settings
    previous_service = app.state.auth_service
    settings.google_oidc_redirect_uri = redirect_uri
    replacement = AuthService(
        build_lite_config(settings),
        access_keys=build_user_access_key_service_from_state(app.state),
    )
    await replacement.ensure_initialized()
    app.state.auth_service = replacement
    return previous_service


@pytest.mark.asyncio
async def test_oidc_callback_route_sets_session_and_redirects(
    oidc_http_client: httpx.AsyncClient,
) -> None:
    callback_uri = f"https://{Settings.root_url}{Settings.base_path}/auth/oidc/callback"
    previous_service = await _swap_oidc_redirect(oidc_http_client, callback_uri)
    try:
        await _ensure_user(
            oidc_http_client,
            "ali@example.com",
            "a secure first password",
        )
        await oidc_http_client.delete("/auth/sessions/current")

        started = (
            await oidc_http_client.post(
                "/auth/oidc/start",
                json={"provider": "google"},
            )
        ).json()
        assert started["redirect_uri"] == callback_uri

        client = _mock_http(
            userinfo={
                "email": "ali@example.com",
                "email_verified": True,
                "name": "Ali",
            },
        )
        with patch("usso.lite.oidc.httpx.AsyncClient", return_value=client):
            response = await oidc_http_client.get(
                "/auth/oidc/callback",
                params={"code": "abc", "state": started["state"]},
                follow_redirects=False,
            )
        assert response.status_code == 302, response.text
        assert response.headers["location"] == "/files"
        assert "usso-access-token" in response.cookies
    finally:
        app = oidc_http_client._transport.app  # type: ignore[attr-defined]
        app.state.settings.google_oidc_redirect_uri = ""
        app.state.auth_service = previous_service
