"""Route-level tests for `/users` and the admin/role enforcement around
provider connections, against the real HTTP app.

This module deliberately does NOT use conftest's shared `client` fixture:
that one points every module at the same session-wide database, and this
module must bootstrap (and mutate) its own administrator without leaving
`test_app_routes.py` -- which asserts a pristine, unconfigured
installation and may run *after* this file -- a configured database.
`Settings` is a Singleton constructed at import time (env overrides after
that are ignored), so this fixture retargets the singleton's own
`database_uri`/`data_dir` for the duration of the module; the app's
lifespan reads them at startup.
"""

import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from fastapi_mongo_base.sql.models import BaseEntity

from server.config import Settings
from server.server import app as fastapi_app

ADMIN_CREDENTIALS = {
    "email": "admin@example.com",
    "password": "a secure admin password",
}


@pytest_asyncio.fixture(scope="module")
async def client() -> AsyncGenerator[httpx.AsyncClient]:
    """An ASGI client against a module-private installation (fresh
    database and data dir; see module docstring)."""
    database = tempfile.NamedTemporaryFile(  # noqa: SIM115
        suffix=".sqlite3", delete=False,
    )
    database.close()
    settings = Settings()  # the singleton the lifespan will read
    previous_database_uri = settings.database_uri
    previous_data_dir = settings.data_dir
    settings.database_uri = f"sqlite+aiosqlite:///{database.name}"
    settings.data_dir = Path(tempfile.mkdtemp(prefix="umedia-users-data-"))
    try:
        async with LifespanManager(fastapi_app):
            async with fastapi_app.state.engine.begin() as connection:
                await connection.run_sync(BaseEntity.metadata.create_all)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=fastapi_app),
                base_url=f"https://{Settings.root_url}{Settings.base_path}",
            ) as ac:
                yield ac
    finally:
        settings.database_uri = previous_database_uri
        settings.data_dir = previous_data_dir
        Path(database.name).unlink(missing_ok=True)
        Path(f"{database.name}.signing.pem").unlink(missing_ok=True)


@pytest_asyncio.fixture(scope="module")
async def admin(client: httpx.AsyncClient) -> dict:
    """Bootstrap this module's administrator; returns their state block."""
    response = await client.post("/auth/setup", json=ADMIN_CREDENTIALS)
    assert response.status_code == 201, response.text
    return response.json()["user"]


def _anonymous(client: httpx.AsyncClient) -> httpx.AsyncClient:
    """A cookie-less client over the same ASGI transport."""
    return httpx.AsyncClient(
        transport=client._transport,
        base_url=str(client.base_url),
    )


@pytest.mark.asyncio
async def test_users_routes_require_authentication(
    client: httpx.AsyncClient, admin: dict,
) -> None:
    async with _anonymous(client) as anonymous:
        assert (await anonymous.get("/users")).status_code == 401
        assert (await anonymous.get("/auth/sessions/current")).status_code == 401


@pytest.mark.asyncio
async def test_current_session_reports_the_admin(
    client: httpx.AsyncClient, admin: dict,
) -> None:
    response = await client.get("/auth/sessions/current")
    assert response.status_code == 200
    assert response.json() == {
        "uid": admin["uid"],
        "email": "admin@example.com",
        "roles": ["admin"],
        "name": None,
    }


@pytest.mark.asyncio
async def test_admin_creates_lists_updates_and_deletes_a_user(
    client: httpx.AsyncClient, admin: dict,
) -> None:
    created = await client.post(
        "/users",
        json={
            "email": "Lifecycle@Example.com",
            "password": "a secure user password",
            "role": "user",
            "name": "Before",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["email"] == "lifecycle@example.com"  # canonicalized
    assert body["name"] == "Before"
    assert body["roles"] == ["user"]
    assert body["is_active"] is True
    uid = body["uid"]

    listed = await client.get("/users")
    assert listed.status_code == 200
    assert {user["email"] for user in listed.json()} == {
        "admin@example.com",
        "lifecycle@example.com",
    }

    promoted = await client.patch(
        f"/users/{uid}", json={"name": "After", "role": "admin"},
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["name"] == "After"
    assert promoted.json()["roles"] == ["admin"]

    # An explicit null clears the optional name; demote back to "user"
    # (allowed -- the bootstrap admin still remains).
    cleared = await client.patch(
        f"/users/{uid}", json={"name": None, "role": "user"},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["name"] is None
    assert cleared.json()["roles"] == ["user"]

    assert (await client.delete(f"/users/{uid}")).status_code == 204
    remaining = (await client.get("/users")).json()
    assert {user["email"] for user in remaining} == {"admin@example.com"}


@pytest.mark.asyncio
async def test_create_user_rejects_duplicates_and_weak_passwords(
    client: httpx.AsyncClient, admin: dict,
) -> None:
    duplicate = await client.post(
        "/users",
        json={
            "email": "admin@example.com",
            "password": "a secure user password",
            "role": "user",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error_code"] == "identifier_exists"

    weak = await client.post(
        "/users",
        json={
            "email": "weak@example.com",
            "password": "short-pass!",  # 11 characters, minimum is 12
            "role": "user",
        },
    )
    assert weak.status_code == 422


@pytest.mark.asyncio
async def test_admin_cannot_remove_themselves_or_the_last_admin(
    client: httpx.AsyncClient, admin: dict,
) -> None:
    self_delete = await client.delete(f"/users/{admin['uid']}")
    assert self_delete.status_code == 403
    assert self_delete.json()["error_code"] == "cannot_delete_self"

    demotion = await client.patch(
        f"/users/{admin['uid']}", json={"role": "user"},
    )
    assert demotion.status_code == 409
    assert demotion.json()["error_code"] == "last_admin_required"

    assert (await client.patch(
        "/users/missing-uid", json={"role": "user"},
    )).status_code == 404
    assert (await client.delete("/users/missing-uid")).status_code == 404


@pytest.mark.asyncio
async def test_non_admins_are_read_only(
    client: httpx.AsyncClient, admin: dict,
) -> None:
    member_credentials = {
        "email": "member@example.com",
        "password": "a secure member password",
    }
    created = await client.post(
        "/users",
        json={**member_credentials, "role": "user", "name": "Media Member"},
    )
    assert created.status_code == 201, created.text
    member_uid = created.json()["uid"]

    async with _anonymous(client) as member:
        login = await member.post("/auth/sessions", json=member_credentials)
        assert login.status_code == 201, login.text
        assert login.json()["user"]["roles"] == ["user"]

        state = (await member.get("/auth/state")).json()
        assert state["authenticated"] is True
        assert state["user"]["email"] == "member@example.com"

        current = await member.get("/auth/sessions/current")
        assert current.status_code == 200
        assert current.json()["name"] == "Media Member"

        # User management is admin-only, whoever the target is.
        assert (await member.get("/users")).status_code == 403
        assert (await member.post(
            "/users",
            json={
                "email": "sneaky@example.com",
                "password": "a secure user password",
            },
        )).status_code == 403
        assert (await member.patch(
            f"/users/{member_uid}", json={"name": "Renamed"},
        )).status_code == 403
        assert (await member.delete(f"/users/{member_uid}")).status_code == 403

        # Provider connections: reads for every authenticated user,
        # writes for administrators only.
        assert (await member.get("/provider-types")).status_code == 200
        assert (await member.get("/providers")).status_code == 200
        blocked = await member.post(
            "/providers",
            json={"provider_type": "local", "name": "x", "config": {}},
        )
        assert blocked.status_code == 403
        assert blocked.json()["error_code"] == "admin_required"
        assert (await member.patch(
            "/providers/some-uid", json={"name": "y"},
        )).status_code == 403
        assert (await member.delete("/providers/some-uid")).status_code == 403
