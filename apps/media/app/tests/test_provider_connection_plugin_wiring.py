"""Proves `apps/provider_connections` is wired to the *real* plugin system
(Phase 2/3), not just unit-tested in isolation: creating a `local`
connection through the live HTTP app round-trips through an actual spawned
plugin subprocess over its Unix socket.
"""

import os
from pathlib import Path

import httpx
import pytest


async def _authenticated(client: httpx.AsyncClient) -> None:
    """Ensure *this* client has a valid session cookie.

    conftest.py's SQLite file is shared across the whole test session (one
    `DATABASE_URL`), but the `client` fixture is module-scoped -- a fresh
    app/lifespan (and cookie jar) per test module. So "an admin already
    exists" (checked via `configured`) does not mean *this* client is
    logged in; when another module's test created the admin first, this
    one still needs its own `/auth/sessions` login, not a skip.
    """
    credentials = {"email": "admin@example.com", "password": "a secure first password"}
    state = (await client.get("/auth/state")).json()
    if state["configured"]:
        response = await client.post("/auth/sessions", json=credentials)
    else:
        response = await client.post("/auth/setup", json=credentials)
    assert response.status_code == 201, response.text


@pytest.mark.asyncio
async def test_creating_a_local_connection_round_trips_through_the_real_plugin(
    client: httpx.AsyncClient,
) -> None:
    await _authenticated(client)

    library = Path(os.environ["UMEDIA_DATA_DIR"]) / "storage" / "library"

    response = await client.post(
        "/providers",
        json={
            "provider_type": "local",
            "name": "Library",
            "config": {"root_path": str(library)},
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["provider_type"] == "local"
    assert body["status"] == "configured"
    # The plugin's own connect() actually ran: it creates the directory as
    # part of validating read/write access (plugins/local/backend.py).
    assert library.is_dir()


@pytest.mark.asyncio
async def test_creating_a_connection_outside_the_allowed_root_is_rejected(
    client: httpx.AsyncClient,
) -> None:
    await _authenticated(client)
    outside = Path(os.environ["UMEDIA_DATA_DIR"]).parent / "definitely-outside"

    response = await client.post(
        "/providers",
        json={
            "provider_type": "local",
            "name": "Escape attempt",
            "config": {"root_path": str(outside)},
        },
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_created_connections_are_enabled_by_default(
    client: httpx.AsyncClient,
) -> None:
    await _authenticated(client)
    library = (
        Path(os.environ["UMEDIA_DATA_DIR"]) / "storage" / "enabled-default-library"
    )

    response = await client.post(
        "/providers",
        json={
            "provider_type": "local",
            "name": "Enabled by default",
            "config": {"root_path": str(library)},
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["enabled"] is True


@pytest.mark.asyncio
async def test_patch_renames_and_disables_a_connection(
    client: httpx.AsyncClient,
) -> None:
    await _authenticated(client)
    library = Path(os.environ["UMEDIA_DATA_DIR"]) / "storage" / "patch-library"
    created = await client.post(
        "/providers",
        json={
            "provider_type": "local",
            "name": "Before rename",
            "config": {"root_path": str(library)},
        },
    )
    uid = created.json()["uid"]

    patched = await client.patch(
        f"/providers/{uid}", json={"name": "After rename", "enabled": False},
    )

    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["name"] == "After rename"
    assert body["enabled"] is False

    listed = await client.get("/providers")
    match = next(item for item in listed.json() if item["uid"] == uid)
    assert match["name"] == "After rename"
    assert match["enabled"] is False


@pytest.mark.asyncio
async def test_patch_missing_connection_is_404(client: httpx.AsyncClient) -> None:
    await _authenticated(client)

    response = await client.patch("/providers/does-not-exist", json={"name": "X"})

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_provider_types_expose_connect_flow(client: httpx.AsyncClient) -> None:
    await _authenticated(client)

    response = await client.get("/provider-types")

    assert response.status_code == 200
    by_id = {item["id"]: item for item in response.json()}
    assert by_id["local"]["connect_flow"] == "token"
    assert by_id["s3"]["connect_flow"] == "token"
    assert by_id["google_drive"]["connect_flow"] == "oauth"
    assert by_id["telegram"]["connect_flow"] == "session"


@pytest.mark.asyncio
async def test_disabled_connection_rejects_resource_operations(
    client: httpx.AsyncClient,
) -> None:
    await _authenticated(client)
    library = Path(os.environ["UMEDIA_DATA_DIR"]) / "storage" / "disabled-library"
    created = await client.post(
        "/providers",
        json={
            "provider_type": "local",
            "name": "Will be disabled",
            "config": {"root_path": str(library)},
        },
    )
    uid = created.json()["uid"]
    await client.patch(f"/providers/{uid}", json={"enabled": False})

    response = await client.post(
        "/files",
        data={
            "provider_connection_id": uid,
            "name": "should-fail.txt",
        },
        files={"file": ("should-fail.txt", b"x", "text/plain")},
    )

    # MediaFileService.upload() wraps any exception from the plugin
    # gateway -- including this upfront validation one -- into a generic
    # 400 MediaFileWriteFailedError; same pre-existing behavior the
    # Resource layer had (an unknown provider_connection_id gets the
    # same treatment).
    assert response.status_code == 400
    assert "disabled" in response.text
