"""`UMEDIA_AUTH_MODE=usso`: the host product's USSO token is the identity."""

import os
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import Request
from usso.user import UserData

from apps.auth.usso_mode import UssoTokenAuthenticator, build_usso_authenticator
from server.config import Settings
from server.server import app as fastapi_app


class FakeVerifier:
    """Accepts `Bearer <uid>[:<tenant>[:<role>]]` instead of real JWTs."""

    def usso_access_security(self, request: Request) -> UserData | None:
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            return None
        uid, _, rest = header.removeprefix("Bearer ").partition(":")
        tenant, _, role = rest.partition(":")
        return UserData(
            sub=uid,
            tenant_id=tenant or None,
            roles=[role] if role else [],
            email=f"{uid}@example.com",
            name=uid.title(),
        )


@pytest.fixture
def usso_mode(client: httpx.AsyncClient) -> Iterator[None]:
    previous = fastapi_app.state.usso_auth
    fastapi_app.state.usso_auth = UssoTokenAuthenticator(
        FakeVerifier(), tenant_id="tenant-1",
    )
    yield
    fastapi_app.state.usso_auth = previous


def _bearer(uid: str, *, tenant: str = "tenant-1", role: str = "") -> dict[str, str]:
    return {"Authorization": f"Bearer {uid}:{tenant}:{role}"}


@pytest.mark.asyncio
async def test_private_routes_need_a_usso_identity(
    client: httpx.AsyncClient, usso_mode: None,
) -> None:
    assert (await client.get("/files")).status_code == 401
    assert (
        await client.get("/files", headers=_bearer("sara"))
    ).status_code == 200


@pytest.mark.asyncio
async def test_token_for_another_tenant_is_rejected(
    client: httpx.AsyncClient, usso_mode: None,
) -> None:
    response = await client.get("/files", headers=_bearer("sara", tenant="other"))
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_auth_state_reports_the_usso_user_without_local_accounts(
    client: httpx.AsyncClient, usso_mode: None,
) -> None:
    anonymous = (await client.get("/auth/state")).json()
    assert anonymous["configured"] is True
    assert anonymous["authenticated"] is False

    state = (await client.get("/auth/state", headers=_bearer("sara"))).json()
    assert state["authenticated"] is True
    assert state["user"] == {
        "uid": "sara", "email": "sara@example.com", "roles": [],
    }

    current = await client.get("/auth/sessions/current", headers=_bearer("sara"))
    assert current.status_code == 200
    assert current.json()["uid"] == "sara"
    assert current.json()["name"] == "Sara"


@pytest.mark.asyncio
async def test_account_management_endpoints_do_not_exist(
    client: httpx.AsyncClient, usso_mode: None,
) -> None:
    for method, path in [
        ("POST", "/auth/setup"),
        ("POST", "/auth/sessions"),
        ("POST", "/auth/refresh"),
        ("PATCH", "/auth/password"),
        ("GET", "/users"),
    ]:
        response = await client.request(method, path, headers=_bearer("sara"))
        assert response.status_code == 404, (method, path)
        assert response.json()["code"] == "auth_managed_by_usso"


@pytest.mark.asyncio
async def test_admin_role_comes_from_the_usso_token(
    client: httpx.AsyncClient, usso_mode: None,
) -> None:
    member = await client.patch(
        "/settings/placement", json={}, headers=_bearer("sara"),
    )
    admin = await client.patch(
        "/settings/placement", json={}, headers=_bearer("boss", role="admin"),
    )
    assert member.status_code == 403
    assert admin.status_code == 200


@pytest.mark.asyncio
async def test_each_usso_user_only_sees_their_own_connections(
    client: httpx.AsyncClient, usso_mode: None,
) -> None:
    library = Path(os.environ["UMEDIA_DATA_DIR"]) / "storage" / "usso-sara-library"
    library.mkdir(parents=True, exist_ok=True)
    created = await client.post(
        "/providers",
        json={
            "provider_type": "local",
            "name": "sara-files",
            "config": {"root_path": str(library)},
        },
        headers=_bearer("sara", role="admin"),
    )
    assert created.status_code == 201, created.text

    own = (await client.get("/providers", headers=_bearer("sara", role="admin"))).json()
    other = (await client.get("/providers", headers=_bearer("omid"))).json()
    assert "sara-files" in [item["name"] for item in own]
    assert "sara-files" not in [item["name"] for item in other]


def _settings(**overrides: object) -> Settings:
    """Stand-in for the (singleton) Settings, so tests never mutate it."""
    values = {"auth_mode": "lite", "usso_base_url": "", "usso_tenant_id": None}
    return SimpleNamespace(**(values | overrides))  # type: ignore[return-value]


def test_authenticator_is_off_in_lite_mode_and_validated_otherwise() -> None:
    assert build_usso_authenticator(_settings()) is None

    with pytest.raises(ValueError, match="USSO_BASE_URL"):
        build_usso_authenticator(_settings(auth_mode="usso"))

    configured = _settings(auth_mode="usso", usso_base_url="https://sso.example.com/")
    assert isinstance(build_usso_authenticator(configured), UssoTokenAuthenticator)

    with pytest.raises(ValueError, match="UMEDIA_AUTH_MODE"):
        build_usso_authenticator(_settings(auth_mode="nonsense"))
