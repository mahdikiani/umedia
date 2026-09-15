"""GET and POST /auth/refresh match production usso cookie-mode auth."""

import httpx
import pytest

from server.server import app as fastapi_app

_CREDENTIALS = {
    "email": "admin@example.com",
    "password": "a secure first password",
}


async def _ensure_session(client: httpx.AsyncClient) -> None:
    """Idempotent login for this module's shared client/database."""
    state = (await client.get("/auth/state")).json()
    if not state["configured"]:
        response = await client.post("/auth/setup", json=_CREDENTIALS)
        assert response.status_code == 201, response.text
        return
    if not state["authenticated"]:
        response = await client.post("/auth/sessions", json=_CREDENTIALS)
        assert response.status_code == 201, response.text


@pytest.mark.asyncio
async def test_get_refresh_uses_httponly_cookie_and_omits_tokens(
    client: httpx.AsyncClient,
) -> None:
    await _ensure_session(client)

    refreshed = await client.get("/auth/refresh")
    assert refreshed.status_code == 200, refreshed.text
    body = refreshed.json()
    assert body["status"] == "refreshed"
    assert body["authenticated"] is True
    assert "access_token" not in body
    assert "usso-access-token" in refreshed.cookies
    assert "usso-refresh-token" in refreshed.cookies
    assert (await client.get("/auth/sessions/current")).status_code == 200


@pytest.mark.asyncio
async def test_post_refresh_from_cookie_also_omits_tokens(
    client: httpx.AsyncClient,
) -> None:
    await _ensure_session(client)

    refreshed = await client.post("/auth/refresh")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["status"] == "refreshed"
    assert "access_token" not in refreshed.json()


@pytest.mark.asyncio
async def test_post_refresh_from_body_returns_tokens_for_api_clients(
    client: httpx.AsyncClient,
) -> None:
    await _ensure_session(client)
    refresh_token = client.cookies["usso-refresh-token"]

    refreshed = await client.post(
        "/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert refreshed.status_code == 200, refreshed.text
    body = refreshed.json()
    assert body["status"] == "refreshed"
    assert body["access_token"]
    assert body["expires_in"] > 0


@pytest.mark.asyncio
async def test_get_refresh_accepts_legacy_umedia_refresh_cookie(
    client: httpx.AsyncClient,
) -> None:
    await _ensure_session(client)
    legacy = client.cookies.get("umedia_refresh") or client.cookies.get(
        "usso-refresh-token",
    )
    assert legacy

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fastapi_app),
        base_url=str(client.base_url),
        cookies={"umedia_refresh": legacy, "usso-access-token": "expired"},
    ) as legacy_client:
        refreshed = await legacy_client.get("/auth/refresh")
        assert refreshed.status_code == 200, refreshed.text
        assert "usso-refresh-token" in refreshed.cookies
        assert refreshed.cookies.get("umedia_refresh") in (None, "")


@pytest.mark.asyncio
async def test_auth_state_refreshes_when_only_refresh_cookie_is_valid(
    client: httpx.AsyncClient,
) -> None:
    # Given: a valid login whose refresh cookie is copied into a fresh client
    await _ensure_session(client)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fastapi_app),
        base_url=str(client.base_url),
    ) as login_client:
        logged_in = await login_client.post("/auth/sessions", json=_CREDENTIALS)
        assert logged_in.status_code == 201, logged_in.text
        refresh_token = login_client.cookies["usso-refresh-token"]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fastapi_app),
        base_url=str(client.base_url),
        cookies={
            "usso-refresh-token": refresh_token,
            "usso-access-token": "invalid",
        },
    ) as refresh_only_client:
        # When: session bootstrap checks auth state without a valid access JWT
        state = await refresh_only_client.get("/auth/state")

        # Then: state silently refreshes and returns a new access cookie
        assert state.status_code == 200, state.text
        assert state.json()["authenticated"] is True
        assert "usso-access-token" in state.cookies


@pytest.mark.asyncio
async def test_session_cookies_use_lax_samesite(
    client: httpx.AsyncClient,
) -> None:
    # Given: a configured application
    await _ensure_session(client)

    # When: a browser session is created
    response = await client.post("/auth/sessions", json=_CREDENTIALS)

    # Then: both session cookies support top-level navigation
    cookies = response.headers.get_list("set-cookie")
    session_cookies = [cookie for cookie in cookies if "usso-" in cookie]
    assert len(session_cookies) == 2
    assert all("SameSite=lax" in cookie for cookie in session_cookies)


@pytest.mark.asyncio
async def test_auth_state_leaves_cookies_when_refresh_fails(
    client: httpx.AsyncClient,
) -> None:
    # Given: an invalid access cookie and an invalid refresh cookie
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fastapi_app),
        base_url=str(client.base_url),
        cookies={
            "usso-refresh-token": "invalid-refresh",
            "usso-access-token": "invalid-access",
        },
    ) as invalid_client:
        # When: the public auth-state route attempts silent refresh
        state = await invalid_client.get("/auth/state")

        # Then: it stays unauthenticated without clearing either cookie
        assert state.status_code == 200, state.text
        assert state.json()["authenticated"] is False
        assert "set-cookie" not in state.headers


@pytest.mark.asyncio
async def test_refresh_without_a_token_is_unauthorized(
    client: httpx.AsyncClient,
) -> None:
    """A client with no cookies must not ride the module session jar."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fastapi_app),
        base_url=str(client.base_url),
    ) as anonymous:
        get_response = await anonymous.get("/auth/refresh")
        assert get_response.status_code == 401
        post_response = await anonymous.post("/auth/refresh")
        assert post_response.status_code == 401


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.asyncio
async def test_refresh_with_tampered_cookie_is_unauthorized_and_clears_cookies(
    client: httpx.AsyncClient,
    method: str,
) -> None:
    # Given: a structurally valid refresh JWT with a tampered signature
    await _ensure_session(client)
    refresh_token = client.cookies["usso-refresh-token"]
    header, payload, signature = refresh_token.split(".")
    replacement = "A" if signature[0] != "A" else "B"
    tampered_token = ".".join((header, payload, replacement + signature[1:]))
    # Seed with an explicit domain so httpx can match the host-scoped
    # Set-Cookie clears (dict-seeded cookies use domain="" and never drop).
    host = httpx.URL(str(client.base_url)).host or "test"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fastapi_app),
        base_url=str(client.base_url),
    ) as invalid_client:
        invalid_client.cookies.set(
            "usso-refresh-token", tampered_token, domain=host, path="/",
        )
        invalid_client.cookies.set(
            "usso-access-token", "stale-access-token", domain=host, path="/",
        )
        # When: either browser refresh endpoint receives the stale cookie
        response = await invalid_client.request(method, "/auth/refresh")

        # Then: invalid JWTs are authentication failures and the session is cleared
        assert response.status_code == 401, response.text
        assert invalid_client.cookies.get("usso-access-token") is None
        assert invalid_client.cookies.get("usso-refresh-token") is None
