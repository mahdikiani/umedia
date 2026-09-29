"""Proves `apps/provider_connections` is wired to the *real* plugin system
(Phase 2/3), not just unit-tested in isolation: creating a `local`
connection through the live HTTP app round-trips through an actual spawned
plugin subprocess over its Unix socket.
"""

import asyncio
import os
from pathlib import Path

import httpx
import pytest

from apps.provider_connections.telegram_login import TelegramLoginError
from server.server import app as fastapi_app


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
    assert body["owner_id"]
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
        f"/providers/{uid}",
        json={"name": "After rename", "enabled": False},
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
async def test_provider_types_expose_connect_flow(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _authenticated(client)
    monkeypatch.setattr(fastapi_app.state.settings, "telegram_api_id", "")
    monkeypatch.setattr(fastapi_app.state.settings, "telegram_api_hash", "")

    response = await client.get("/provider-types")

    assert response.status_code == 200
    by_id = {item["id"]: item for item in response.json()}
    assert by_id["local"]["connect_flow"] == "token"
    assert by_id["s3"]["connect_flow"] == "token"
    assert by_id["google_drive"]["connect_flow"] == "oauth"
    assert by_id["telegram"]["connect_flow"] == "session"
    assert by_id["telegram"]["available"] is False
    assert "UMEDIA_TELEGRAM_API_ID" in by_id["telegram"]["unavailable_reason"]
    assert "UMEDIA_TELEGRAM_API_HASH" in by_id["telegram"]["unavailable_reason"]
    assert by_id["telegram"]["fields"] == []

    rejected = await client.post(
        "/providers",
        json={
            "provider_type": "telegram",
            "name": "Archive channel",
            "config": {"channel_id": "-100123", "session": "session"},
        },
    )
    assert rejected.status_code == 422
    assert "UMEDIA_TELEGRAM_API_ID" in rejected.text


@pytest.mark.asyncio
async def test_telegram_login_api_advances_to_two_step_password(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _authenticated(client)
    login_service = fastapi_app.state.telegram_login_service

    async def start(**_values: object) -> dict[str, str]:
        await asyncio.sleep(0)
        return {"login_id": "login-1", "step": "code"}

    async def advance(**_values: object) -> tuple[dict[str, str], None]:
        await asyncio.sleep(0)
        return {"step": "password"}, None

    monkeypatch.setattr(login_service, "start", start)
    monkeypatch.setattr(login_service, "advance", advance)

    started = await client.post(
        "/providers/telegram/login/start",
        json={
            "name": "Archive channel",
            "phone": "+1234567890",
            "channel_ref": "@channelname",
        },
    )
    assert started.status_code == 200, started.text
    assert started.json() == {
        "login_id": "login-1",
        "step": "code",
        "connection": None,
    }

    verified = await client.post(
        "/providers/telegram/login/login-1/code",
        json={"value": "12345"},
    )
    assert verified.status_code == 200, verified.text
    assert verified.json() == {"login_id": None, "step": "password", "connection": None}


@pytest.mark.asyncio
async def test_telegram_login_api_returns_specific_code_error(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _authenticated(client)
    login_service = fastapi_app.state.telegram_login_service

    async def start(**_values: object) -> dict[str, str]:
        await asyncio.sleep(0)
        return {"login_id": "pending-login", "step": "code"}

    async def advance(**_values: object) -> tuple[dict[str, str], None]:
        await asyncio.sleep(0)
        raise TelegramLoginError(
            400,
            "Telegram login code is invalid or expired. Cancel and request a new code.",
        )

    monkeypatch.setattr(login_service, "start", start)
    monkeypatch.setattr(login_service, "advance", advance)

    started = await client.post(
        "/providers/telegram/login/start",
        json={"name": "Archive", "phone": "+1234567890", "channel_ref": "@channel"},
    )
    assert started.status_code == 200

    rejected = await client.post(
        "/providers/telegram/login/pending-login/code",
        json={"value": "12345"},
    )

    assert rejected.status_code == 400
    assert rejected.json()["detail"] == (
        "Telegram login code is invalid or expired. Cancel and request a new code."
    )


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

    # Placement rejects a disabled preferred connection before the plugin
    # gateway runs (MediaFileValidationError → 422).
    assert response.status_code == 422
    assert "disabled" in response.text


@pytest.mark.asyncio
async def test_user_cannot_see_or_edit_anothers_connection(
    client: httpx.AsyncClient,
) -> None:
    await _authenticated(client)
    library = Path(os.environ["UMEDIA_DATA_DIR"]) / "storage" / "owner-a-library"
    created = await client.post(
        "/providers",
        json={
            "provider_type": "local",
            "name": "Admin library",
            "config": {"root_path": str(library)},
        },
    )
    assert created.status_code == 201, created.text
    uid = created.json()["uid"]
    admin_uid = created.json()["owner_id"]

    member_credentials = {
        "email": "provider-member@example.com",
        "password": "a secure member password",
    }
    created_user = await client.post(
        "/users",
        json={**member_credentials, "role": "user"},
    )
    assert created_user.status_code == 201, created_user.text

    member = httpx.AsyncClient(
        transport=client._transport,
        base_url=str(client.base_url),
    )
    async with member:
        login = await member.post("/auth/sessions", json=member_credentials)
        assert login.status_code == 201, login.text

        listed = await member.get("/providers")
        assert listed.status_code == 200
        assert all(item["uid"] != uid for item in listed.json())

        types = await member.get("/provider-types")
        assert types.status_code == 200
        assert all(item["id"] != "local" for item in types.json())

        blocked_local = await member.post(
            "/providers",
            json={
                "provider_type": "local",
                "name": "Nope",
                "config": {"root_path": str(library / "member")},
            },
        )
        assert blocked_local.status_code == 403
        assert blocked_local.json()["error_code"] == "local_admin_required"

        assert (
            await member.patch(
                f"/providers/{uid}",
                json={"name": "Hijack"},
            )
        ).status_code == 404
        assert (await member.delete(f"/providers/{uid}")).status_code == 404
        assert (await member.get(f"/providers/{uid}/objects")).status_code == 404
        assert (await member.get(f"/providers/{uid}/sync")).status_code == 404
        assert (await member.post(f"/providers/{uid}/sync")).status_code == 404

    admin_list = await client.get("/providers")
    match = next(item for item in admin_list.json() if item["uid"] == uid)
    assert match["owner_id"] == admin_uid
    assert match["name"] == "Admin library"


@pytest.mark.asyncio
async def test_non_admin_cannot_sync_or_browse_owned_local_connection(
    client: httpx.AsyncClient,
) -> None:
    """Even if a local row is somehow owned by a member, sync/objects
    must refuse — same rule as placement (`connection_usable_by`)."""
    await _authenticated(client)
    member_credentials = {
        "email": "local-leftover@example.com",
        "password": "a secure member password",
    }
    created_user = await client.post(
        "/users",
        json={**member_credentials, "role": "user"},
    )
    assert created_user.status_code == 201, created_user.text
    member_uid = created_user.json()["uid"]

    library = Path(os.environ["UMEDIA_DATA_DIR"]) / "storage" / "leftover-local-library"
    created = await client.post(
        "/providers",
        json={
            "provider_type": "local",
            "name": "Leftover local",
            "config": {"root_path": str(library)},
        },
    )
    assert created.status_code == 201, created.text
    uid = created.json()["uid"]

    from apps.provider_connections.repository import ProviderConnectionRepository
    from server.server import app as fastapi_app

    reassigned = await ProviderConnectionRepository(
        fastapi_app.state.session_factory,
    ).update(uid, {"owner_id": member_uid})
    assert reassigned is not None
    assert reassigned.owner_id == member_uid

    member = httpx.AsyncClient(
        transport=client._transport,
        base_url=str(client.base_url),
    )
    async with member:
        login = await member.post("/auth/sessions", json=member_credentials)
        assert login.status_code == 201, login.text
        assert (await member.get(f"/providers/{uid}/objects")).status_code == 404
        assert (await member.get(f"/providers/{uid}/sync")).status_code == 404
        assert (await member.post(f"/providers/{uid}/sync")).status_code == 404


@pytest.mark.asyncio
async def test_owner_can_sync_their_connection(
    client: httpx.AsyncClient,
) -> None:
    await _authenticated(client)
    library = Path(os.environ["UMEDIA_DATA_DIR"]) / "storage" / "sync-owner-library"
    created = await client.post(
        "/providers",
        json={
            "provider_type": "local",
            "name": "Syncable",
            "config": {"root_path": str(library)},
        },
    )
    uid = created.json()["uid"]

    status = await client.get(f"/providers/{uid}/sync")
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "idle"

    accepted = await client.post(f"/providers/{uid}/sync")
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["connection_id"] == uid
