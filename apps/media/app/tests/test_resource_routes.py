"""Route-level tests for `apps.resources.routes` (P4.4/P4.5), against the
real HTTP app -- real SQLite, a real spawned `local` plugin subprocess
(via the module-scoped `client` fixture in conftest.py), proving the full
provider-connection -> resource -> plugin chain works end to end through
the actual REST surface, not just the service layer (already covered by
test_resource_service.py) in isolation.
"""

import os
from pathlib import Path

import httpx
import pytest


async def _authenticated(client: httpx.AsyncClient) -> None:
    """See test_provider_connection_plugin_wiring.py's docstring for why
    this can't just check `configured` and skip -- the `client` fixture is
    module-scoped, so this module's client needs its own login even if
    another module's test already created the admin."""
    credentials = {"email": "admin@example.com", "password": "a secure first password"}
    state = (await client.get("/auth/state")).json()
    if state["configured"]:
        response = await client.post("/auth/sessions", json=credentials)
    else:
        response = await client.post("/auth/setup", json=credentials)
    assert response.status_code == 201, response.text


@pytest.fixture(scope="module")
async def connection_id(client: httpx.AsyncClient) -> str:
    await _authenticated(client)
    library = (
        Path(os.environ["UMEDIA_DATA_DIR"]) / "storage" / "resources-routes-library"
    )
    response = await client.post(
        "/providers",
        json={
            "provider_type": "local",
            "name": "Resources routes test library",
            "config": {"root_path": str(library)},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["uid"]


@pytest.mark.asyncio
async def test_create_list_get_head_a_file(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    await _authenticated(client)

    created = await client.post(
        "/resources",
        data={
            "provider_connection_id": connection_id,
            "name": "hello.txt",
        },
        files={"file": ("hello.txt", b"hello, resources", "text/plain")},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "hello.txt"
    assert body["status"] == "completed"
    assert body["size"] == len(b"hello, resources")
    assert body["public_permission"] == "none"
    uid = body["uid"]

    listed = await client.get("/resources")
    assert any(item["uid"] == uid for item in listed.json())

    fetched = await client.get(f"/resources/{uid}")
    assert fetched.status_code == 200
    assert fetched.json()["uid"] == uid

    head = await client.head(f"/resources/{uid}")
    assert head.status_code == 200
    assert head.headers["content-length"] == str(len(b"hello, resources"))
    assert head.headers["content-type"] == "text/plain"


@pytest.mark.asyncio
async def test_create_deduplicates_identical_uploads(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    await _authenticated(client)
    payload = {
        "provider_connection_id": connection_id,
        "name": "dup-a.txt",
    }
    files = {"file": ("dup-a.txt", b"same bytes here", "text/plain")}

    first = await client.post("/resources", data=payload, files=files)
    second = await client.post(
        "/resources",
        data={**payload, "name": "dup-b.txt"},
        files={"file": ("dup-b.txt", b"same bytes here", "text/plain")},
    )

    assert first.json()["uid"] == second.json()["uid"]
    assert second.json()["name"] == "dup-a.txt"  # original name wins


@pytest.mark.asyncio
async def test_create_folder_and_list_its_children(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    await _authenticated(client)

    folder = await client.post(
        "/resources",
        data={
            "provider_connection_id": connection_id,
            "name": "a-folder",
            "type": "folder",
        },
    )
    assert folder.status_code == 201, folder.text
    folder_uid = folder.json()["uid"]
    assert folder.json()["content_type"] == "inode/directory"

    child = await client.post(
        "/resources",
        data={
            "provider_connection_id": connection_id,
            "name": "child.txt",
            "parent_id": folder_uid,
        },
        files={"file": ("child.txt", b"nested", "text/plain")},
    )
    assert child.status_code == 201, child.text

    listed = await client.get("/resources", params={"parent_id": folder_uid})
    names = [item["name"] for item in listed.json()]
    assert names == ["child.txt"]


@pytest.mark.asyncio
async def test_content_read_full_and_ranged(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    await _authenticated(client)
    created = await client.post(
        "/resources",
        data={"provider_connection_id": connection_id, "name": "range.txt"},
        files={"file": ("range.txt", b"0123456789", "text/plain")},
    )
    uid = created.json()["uid"]

    full = await client.get(f"/resources/{uid}/content")
    assert full.status_code == 200
    assert full.content == b"0123456789"
    assert full.headers["content-length"] == "10"

    ranged = await client.get(
        f"/resources/{uid}/content", headers={"Range": "bytes=2-4"},
    )
    assert ranged.status_code == 206
    assert ranged.content == b"234"
    assert ranged.headers["content-range"] == "bytes 2-4/10"
    assert ranged.headers["content-length"] == "3"

    unsatisfiable = await client.get(
        f"/resources/{uid}/content", headers={"Range": "bytes=100-200"},
    )
    assert unsatisfiable.status_code == 416


@pytest.mark.asyncio
async def test_content_read_rejects_folders(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    await _authenticated(client)
    folder = await client.post(
        "/resources",
        data={
            "provider_connection_id": connection_id,
            "name": "not-streamable",
            "type": "folder",
        },
    )
    uid = folder.json()["uid"]

    response = await client.get(f"/resources/{uid}/content")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_rename_move_and_overwrite_content(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    await _authenticated(client)
    created = await client.post(
        "/resources",
        data={"provider_connection_id": connection_id, "name": "before.txt"},
        files={"file": ("before.txt", b"v1", "text/plain")},
    )
    uid = created.json()["uid"]

    renamed = await client.put(f"/resources/{uid}", data={"name": "after.txt"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "after.txt"

    overwritten = await client.put(
        f"/resources/{uid}",
        files={"file": ("after.txt", b"v2 is longer", "text/plain")},
    )
    assert overwritten.status_code == 200, overwritten.text
    assert overwritten.json()["size"] == len(b"v2 is longer")

    content = await client.get(f"/resources/{uid}/content")
    assert content.content == b"v2 is longer"


@pytest.mark.asyncio
async def test_soft_delete_restore_and_hard_delete(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    await _authenticated(client)
    created = await client.post(
        "/resources",
        data={"provider_connection_id": connection_id, "name": "doomed.txt"},
        files={"file": ("doomed.txt", b"bye", "text/plain")},
    )
    uid = created.json()["uid"]

    soft = await client.delete(f"/resources/{uid}")
    assert soft.status_code == 204
    assert (await client.get(f"/resources/{uid}")).status_code == 404

    restored = await client.post(f"/resources/{uid}/restore")
    assert restored.status_code == 200
    assert (await client.get(f"/resources/{uid}")).status_code == 200

    await client.delete(f"/resources/{uid}")
    hard = await client.delete(f"/resources/{uid}", params={"permanent": "true"})
    assert hard.status_code == 204
    assert (await client.get(f"/resources/{uid}")).status_code == 404
    # Actually gone, not just soft-deleted again -- restoring a hard-deleted
    # resource is impossible, the row no longer exists.
    assert (await client.post(f"/resources/{uid}/restore")).status_code == 404


@pytest.mark.asyncio
async def test_volume_stats(client: httpx.AsyncClient, connection_id: str) -> None:
    await _authenticated(client)
    await client.post(
        "/resources",
        data={"provider_connection_id": connection_id, "name": "counted.txt"},
        files={"file": ("counted.txt", b"12345", "text/plain")},
    )

    stats = await client.get("/resources/volume-stats")
    assert stats.status_code == 200
    body = stats.json()
    assert body["active_count"] >= 1
    assert body["active_size"] >= 5


@pytest.mark.asyncio
async def test_resources_routes_require_authentication(
    client: httpx.AsyncClient,
) -> None:
    anonymous = httpx.AsyncClient(
        transport=client._transport,
        base_url=str(client.base_url),
    )
    async with anonymous:
        response = await anonymous.get("/resources")
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_second_user_cannot_see_anothers_file_until_shared(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    """Multi-user isolation over the real REST surface: an authenticated
    second user gets 404 (not 403 -- existence is private too) for a
    resource the first user hasn't shared, then READ access once a
    `PUT /resources/{uid}/permissions` grant lands."""
    await _authenticated(client)
    created = await client.post(
        "/resources",
        data={"provider_connection_id": connection_id, "name": "isolated.txt"},
        files={"file": ("isolated.txt", b"owner eyes only", "text/plain")},
    )
    assert created.status_code == 201, created.text
    uid = created.json()["uid"]
    assert created.json()["owner_id"]  # creator became the owner

    member_credentials = {
        "email": "viewer@example.com",
        "password": "a secure viewer password",
    }
    created_user = await client.post(
        "/users", json={**member_credentials, "role": "user"},
    )
    assert created_user.status_code == 201, created_user.text
    member_uid = created_user.json()["uid"]

    member = httpx.AsyncClient(
        transport=client._transport,
        base_url=str(client.base_url),
    )
    async with member:
        login = await member.post("/auth/sessions", json=member_credentials)
        assert login.status_code == 201, login.text

        # Authenticated but not shared with: the resource doesn't exist
        # as far as this user can tell.
        assert (await member.get(f"/resources/{uid}")).status_code == 404
        assert (await member.get(f"/resources/{uid}/content")).status_code == 404
        owned = await member.get("/resources")
        assert all(item["uid"] != uid for item in owned.json())
        shared = await member.get(
            "/resources", params={"scope": "shared_with_me"},
        )
        assert shared.json() == []

        # The owner grants READ.
        granted = await client.put(
            f"/resources/{uid}/permissions",
            json={"user_id": member_uid, "permission": "read"},
        )
        assert granted.status_code == 200, granted.text
        assert granted.json()["permissions"] == [
            {"user_id": member_uid, "permission": 10},
        ]
        listed_grants = await client.get(f"/resources/{uid}/permissions")
        assert listed_grants.status_code == 200
        assert listed_grants.json() == [
            {"user_id": member_uid, "permission": 10},
        ]

        # Now readable -- and it shows up under scope=shared_with_me.
        fetched = await member.get(f"/resources/{uid}")
        assert fetched.status_code == 200
        assert fetched.json()["name"] == "isolated.txt"
        content = await member.get(f"/resources/{uid}/content")
        assert content.status_code == 200
        assert content.content == b"owner eyes only"
        shared = await member.get(
            "/resources", params={"scope": "shared_with_me"},
        )
        assert [item["uid"] for item in shared.json()] == [uid]

        # READ is not WRITE: renames stay forbidden (403 now, the
        # resource is visibly there).
        renamed = await member.put(f"/resources/{uid}", data={"name": "nope.txt"})
        assert renamed.status_code == 403, renamed.text


@pytest.mark.asyncio
async def test_public_link_is_404_until_marked_public_then_readable_anonymously(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    await _authenticated(client)
    created = await client.post(
        "/resources",
        data={"provider_connection_id": connection_id, "name": "shared.txt"},
        files={"file": ("shared.txt", b"share me", "text/plain")},
    )
    uid = created.json()["uid"]

    anonymous = httpx.AsyncClient(
        transport=client._transport,
        base_url=str(client.base_url),
    )
    async with anonymous:
        not_yet_public = await anonymous.get(f"/f/{uid}")
        assert not_yet_public.status_code == 404

        made_public = await client.put(
            f"/resources/{uid}", data={"public_permission": "read"},
        )
        assert made_public.status_code == 200
        assert made_public.json()["public_permission"] == "read"

        content = await anonymous.get(f"/f/{uid}")
        assert content.status_code == 200
        assert content.content == b"share me"

        head = await anonymous.head(f"/f/{uid}")
        assert head.status_code == 200
        assert head.headers["content-length"] == str(len(b"share me"))

        details = await anonymous.get(f"/f/{uid}", params={"details": "true"})
        assert details.status_code == 200
        assert details.json()["uid"] == uid
