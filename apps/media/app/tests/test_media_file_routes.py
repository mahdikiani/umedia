"""Route-level tests for the dual-layer library REST surface
(docs/11-dual-layer-library.md "API"): `/files`, `/f/{uid}`,
`/providers/{uid}/objects`, `/providers/{uid}/sync` -- against the real
HTTP app with real SQLite and a real spawned `local` plugin subprocess
(module-scoped `client` fixture in conftest.py)."""

import asyncio
import os
from pathlib import Path

import httpx
import pytest

ADMIN_CREDENTIALS = {
    "email": "admin@example.com",
    "password": "a secure first password",
}


async def _authenticated(client: httpx.AsyncClient) -> None:
    state = (await client.get("/auth/state")).json()
    if state["configured"]:
        response = await client.post("/auth/sessions", json=ADMIN_CREDENTIALS)
    else:
        response = await client.post("/auth/setup", json=ADMIN_CREDENTIALS)
    assert response.status_code == 201, response.text


def _library_dir(name: str) -> Path:
    return Path(os.environ["UMEDIA_DATA_DIR"]) / "storage" / name


@pytest.fixture(scope="module")
async def connection_id(client: httpx.AsyncClient) -> str:
    await _authenticated(client)
    response = await client.post(
        "/providers",
        json={
            "provider_type": "local",
            "name": "files-routes-test-library",
            "config": {"root_path": str(_library_dir("files-routes-library"))},
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    # The two dual-layer flags are exposed and default to false.
    assert body["import_existing"] is False
    assert body["mirror_structure"] is False
    return body["uid"]


@pytest.mark.asyncio
async def test_upload_list_get_head_content(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    await _authenticated(client)

    created = await client.post(
        "/files",
        data={"provider_connection_id": connection_id, "name": "hello.txt"},
        files={"file": ("hello.txt", b"hello, files", "text/plain")},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "hello.txt"
    assert body["type"] == "file"
    assert body["status"] == "completed"
    assert body["size"] == len(b"hello, files")
    assert body["provider_connection_id"] == connection_id
    assert body["storage_object_id"]
    uid = body["uid"]

    listed = await client.get("/files")
    assert listed.status_code == 200
    page = listed.json()
    assert any(item["uid"] == uid for item in page["items"])
    assert page["limit"] == 50
    assert page["offset"] == 0
    assert page["total"] >= 1
    assert page["has_more"] is False

    fetched = await client.get(f"/files/{uid}")
    assert fetched.status_code == 200
    assert fetched.json()["uid"] == uid

    head = await client.head(f"/files/{uid}")
    assert head.status_code == 200
    assert head.headers["content-length"] == str(len(b"hello, files"))
    assert head.headers["content-type"] == "text/plain; charset=utf-8"

    content = await client.get(f"/files/{uid}/content")
    assert content.status_code == 200
    assert content.content == b"hello, files"

    named = await client.get(f"/files/{uid}/content/hello.txt")
    assert named.status_code == 200
    assert named.content == b"hello, files"

    ranged = await client.get(
        f"/files/{uid}/content", headers={"Range": "bytes=0-4"},
    )
    assert ranged.status_code == 206
    assert ranged.content == b"hello"
    assert ranged.headers["content-range"] == f"bytes 0-4/{len(b'hello, files')}"


@pytest.mark.asyncio
async def test_markdown_content_is_utf8_plain_text_not_html(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    """Browsers mojibake UTF-8 `.md` without charset, and treat
    libmagic's `text/html` guess as a broken page."""
    await _authenticated(client)
    body = "# عنوان\n\nبا <کد> نمونه.\n".encode()
    created = await client.post(
        "/files",
        data={"provider_connection_id": connection_id, "name": "notes.md"},
        files={"file": ("notes.md", body, "text/markdown")},
    )
    assert created.status_code == 201, created.text
    uid = created.json()["uid"]
    content = await client.get(f"/files/{uid}/content")
    assert content.status_code == 200
    assert content.content == body
    assert content.headers["content-type"] == "text/plain; charset=utf-8"
    assert "html" not in content.headers["content-type"]


@pytest.mark.asyncio
async def test_content_download_query_sets_attachment_disposition(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    await _authenticated(client)
    created = await client.post(
        "/files",
        data={"provider_connection_id": connection_id, "name": "save-me.txt"},
        files={"file": ("save-me.txt", b"payload", "text/plain")},
    )
    assert created.status_code == 201, created.text
    uid = created.json()["uid"]

    inline = await client.get(f"/files/{uid}/content")
    assert inline.headers["content-disposition"].startswith("inline;")

    download = await client.get(f"/files/{uid}/content?download=1")
    assert download.headers["content-disposition"].startswith("attachment;")


@pytest.mark.asyncio
async def test_folders_are_library_only_and_nest(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    await _authenticated(client)

    folder = await client.post("/files", data={"name": "a-folder", "type": "folder"})
    assert folder.status_code == 201, folder.text
    folder_body = folder.json()
    assert folder_body["type"] == "folder"
    assert folder_body["content_type"] == "inode/directory"
    assert folder_body["storage_object_id"] is None  # no provider write
    folder_uid = folder_body["uid"]

    child = await client.post(
        "/files",
        data={
            "provider_connection_id": connection_id,
            "name": "child.txt",
            "parent_id": folder_uid,
        },
        files={"file": ("child.txt", b"nested", "text/plain")},
    )
    assert child.status_code == 201, child.text

    listed = await client.get("/files", params={"parent_id": folder_uid})
    assert [item["name"] for item in listed.json()["items"]] == ["child.txt"]

    searched = await client.get(
        "/files", params={"q": "child", "parent_id": folder_uid},
    )
    assert searched.status_code == 200
    assert [item["uid"] for item in searched.json()["items"]] == [
        child.json()["uid"],
    ]


@pytest.mark.asyncio
async def test_patch_renames_and_moves(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    await _authenticated(client)
    folder_uid = (
        await client.post("/files", data={"name": "move-target", "type": "folder"})
    ).json()["uid"]
    created = await client.post(
        "/files",
        data={"name": "before.txt", "parent_id": folder_uid},
        files={"file": ("before.txt", b"v1", "text/plain")},
    )
    assert created.status_code == 201, created.text
    uid = created.json()["uid"]
    assert created.json()["parent_id"] == folder_uid
    del connection_id

    renamed = await client.patch(f"/files/{uid}", json={"name": "after.txt"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "after.txt"
    assert renamed.json()["parent_id"] == folder_uid

    to_root = await client.patch(f"/files/{uid}", json={"parent_id": None})
    assert to_root.status_code == 200, to_root.text
    assert to_root.json()["parent_id"] is None

    moved = await client.patch(f"/files/{uid}", json={"parent_id": folder_uid})
    assert moved.status_code == 200, moved.text
    assert moved.json()["parent_id"] == folder_uid


@pytest.mark.asyncio
async def test_acl_second_user_needs_a_grant(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    await _authenticated(client)
    created = await client.post(
        "/files",
        data={"provider_connection_id": connection_id, "name": "isolated.txt"},
        files={"file": ("isolated.txt", b"owner eyes only", "text/plain")},
    )
    uid = created.json()["uid"]

    member_credentials = {
        "email": "files-viewer@example.com",
        "password": "a secure viewer password",
    }
    created_user = await client.post(
        "/users", json={**member_credentials, "role": "user"},
    )
    assert created_user.status_code == 201, created_user.text
    member_uid = created_user.json()["uid"]

    member = httpx.AsyncClient(
        transport=client._transport, base_url=str(client.base_url),
    )
    async with member:
        login = await member.post("/auth/sessions", json=member_credentials)
        assert login.status_code == 201, login.text

        assert (await member.get(f"/files/{uid}")).status_code == 404
        assert (await member.get(f"/files/{uid}/content")).status_code == 404
        hidden_search = await member.get("/files", params={"q": "isolated"})
        assert all(
            item["uid"] != uid for item in hidden_search.json()["items"]
        )

        granted = await client.put(
            f"/files/{uid}/permissions",
            json={"user_id": member_uid, "permission": "read"},
        )
        assert granted.status_code == 200, granted.text
        assert granted.json()["permissions"] == [
            {"user_id": member_uid, "permission": 10},
        ]

        content = await member.get(f"/files/{uid}/content")
        assert content.status_code == 200
        assert content.content == b"owner eyes only"

        visible_search = await member.get("/files", params={"q": "isolated"})
        assert [item["uid"] for item in visible_search.json()["items"]] == [uid]

        renamed = await member.patch(f"/files/{uid}", json={"name": "nope.txt"})
        assert renamed.status_code == 403, renamed.text


@pytest.mark.asyncio
async def test_public_link_alias(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    await _authenticated(client)
    created = await client.post(
        "/files",
        data={"provider_connection_id": connection_id, "name": "shared.txt"},
        files={"file": ("shared.txt", b"share me", "text/plain")},
    )
    uid = created.json()["uid"]

    anonymous = httpx.AsyncClient(
        transport=client._transport, base_url=str(client.base_url),
    )
    async with anonymous:
        assert (await anonymous.get(f"/f/{uid}")).status_code == 404

        made_public = await client.patch(
            f"/files/{uid}", json={"public_permission": "read"},
        )
        assert made_public.status_code == 200
        assert made_public.json()["public_permission"] == "read"

        content = await anonymous.get(f"/f/{uid}")
        assert content.status_code == 200
        assert content.content == b"share me"

        # Trailing filename is cosmetic -- wrong name still resolves by uid.
        aliased = await anonymous.get(f"/f/{uid}/shared.txt")
        assert aliased.status_code == 200
        assert aliased.content == b"share me"
        wrong_name = await anonymous.get(f"/f/{uid}/other-name.bin")
        assert wrong_name.status_code == 200
        assert wrong_name.content == b"share me"

        head = await anonymous.head(f"/f/{uid}/shared.txt")
        assert head.status_code == 200
        assert head.headers["content-length"] == str(len(b"share me"))


@pytest.mark.asyncio
async def test_delete_restore_and_permanent_delete(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    await _authenticated(client)
    created = await client.post(
        "/files",
        data={"provider_connection_id": connection_id, "name": "doomed.txt"},
        files={"file": ("doomed.txt", b"bye", "text/plain")},
    )
    uid = created.json()["uid"]

    soft = await client.delete(f"/files/{uid}")
    assert soft.status_code == 204
    assert (await client.get(f"/files/{uid}")).status_code == 404

    restored = await client.post(f"/files/{uid}/restore")
    assert restored.status_code == 200
    assert (await client.get(f"/files/{uid}")).status_code == 200

    await client.delete(f"/files/{uid}")
    hard = await client.delete(f"/files/{uid}", params={"permanent": "true"})
    assert hard.status_code == 204
    assert (await client.get(f"/files/{uid}")).status_code == 404


@pytest.mark.asyncio
async def test_sync_imports_preexisting_provider_files(
    client: httpx.AsyncClient,
) -> None:
    """POST /providers/{uid}/sync: remote objects land as StorageObjects
    plus MediaFiles under a root folder named after the connection."""
    await _authenticated(client)
    library = _library_dir("files-sync-library")
    (library / "docs").mkdir(parents=True, exist_ok=True)
    (library / "hello.txt").write_bytes(b"preexisting")
    (library / "docs" / "nested.txt").write_bytes(b"deep")

    created = await client.post(
        "/providers",
        json={
            "provider_type": "local",
            "name": "sync-test-library",
            "config": {"root_path": str(library)},
        },
    )
    assert created.status_code == 201, created.text
    provider_uid = created.json()["uid"]

    synced = await client.post(f"/providers/{provider_uid}/sync")
    assert synced.status_code == 202, synced.text
    assert synced.json()["status"] == "started"

    # Sync runs detached; poll until the library root appears.
    root = None
    for _ in range(100):
        await asyncio.sleep(0.05)
        roots = (await client.get("/files")).json()["items"]
        root = next(
            (item for item in roots if item["name"] == "sync-test-library"),
            None,
        )
        if root is not None:
            break
    assert root is not None, "sync task never produced the connection root"
    assert synced.json()["connection_id"] == provider_uid

    children = []
    for _ in range(100):
        children = (
            await client.get("/files", params={"parent_id": root["uid"]})
        ).json()["items"]
        names = sorted(item["name"] for item in children)
        if names == ["docs", "hello.txt"]:
            break
        await asyncio.sleep(0.05)
    assert sorted(item["name"] for item in children) == ["docs", "hello.txt"]

    imported = next(item for item in children if item["name"] == "hello.txt")
    content = await client.get(f"/files/{imported['uid']}/content")
    assert content.status_code == 200
    assert content.content == b"preexisting"

    # The physical index browse (separate menu) sees the same objects.
    objects = await client.get(f"/providers/{provider_uid}/objects")
    assert objects.status_code == 200, objects.text
    object_page = objects.json()
    references = {
        item["content_reference"] for item in object_page["items"]
    }
    assert object_page["limit"] == 50
    assert object_page["offset"] == 0
    assert {"docs", "hello.txt", "docs/nested.txt"} <= references

    searched_objects = await client.get(
        f"/providers/{provider_uid}/objects",
        params={"q": "nested", "parent_ref": "docs"},
    )
    assert searched_objects.status_code == 200
    assert [
        item["content_reference"]
        for item in searched_objects.json()["items"]
    ] == ["docs/nested.txt"]


@pytest.mark.asyncio
async def test_connect_with_import_existing_runs_the_import(
    client: httpx.AsyncClient,
) -> None:
    await _authenticated(client)
    library = _library_dir("files-autoimport-library")
    library.mkdir(parents=True, exist_ok=True)
    (library / "already-there.txt").write_bytes(b"import me")

    created = await client.post(
        "/providers",
        json={
            "provider_type": "local",
            "name": "auto-import-library",
            "config": {"root_path": str(library)},
            "import_existing": True,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["import_existing"] is True

    # The import runs as a detached background task; give the loop a
    # moment rather than assuming it finished before the response.
    root = None
    for _ in range(100):
        await asyncio.sleep(0.05)
        roots = (await client.get("/files")).json()["items"]
        root = next(
            (item for item in roots if item["name"] == "auto-import-library"),
            None,
        )
        if root is not None:
            break
    assert root is not None, "import task never produced the connection root"

    children = (
        await client.get("/files", params={"parent_id": root["uid"]})
    ).json()["items"]
    assert [item["name"] for item in children] == ["already-there.txt"]


@pytest.mark.asyncio
async def test_files_routes_require_authentication(
    client: httpx.AsyncClient,
    connection_id: str,
) -> None:
    """Library metadata stays session-gated (401). Content URLs are
    session-optional and apply the open matrix -- a private file must
    404 for anonymous callers (not 401), so existence stays hidden."""
    await _authenticated(client)
    created = await client.post(
        "/files",
        data={"provider_connection_id": connection_id, "name": "secret.png"},
        files={"file": ("secret.png", b"png-bytes", "image/png")},
    )
    assert created.status_code == 201, created.text
    uid = created.json()["uid"]

    anonymous = httpx.AsyncClient(
        transport=client._transport, base_url=str(client.base_url),
    )
    async with anonymous:
        assert (await anonymous.get("/files")).status_code == 401
        assert (await anonymous.get(f"/files/{uid}")).status_code == 401
        assert (await anonymous.get(f"/files/{uid}/content")).status_code == 404
        assert (
            await anonymous.get(f"/files/{uid}/content/secret.png")
        ).status_code == 404


@pytest.mark.asyncio
async def test_private_content_is_not_shared_cacheable(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    """Authenticated `/files/{uid}/content` sits behind Cloudflare on
    preview. Without `Cache-Control: private, no-store`, an edge cache
    keyed only by URL can serve bytes to an anonymous/incognito client
    after the owner loaded the image once (e.g. via `<img src>`)."""
    await _authenticated(client)
    created = await client.post(
        "/files",
        data={"provider_connection_id": connection_id, "name": "logo.png"},
        files={"file": ("logo.png", b"logo-bytes", "image/png")},
    )
    assert created.status_code == 201, created.text
    uid = created.json()["uid"]

    content = await client.get(f"/files/{uid}/content/logo.png")
    assert content.status_code == 200
    assert content.content == b"logo-bytes"
    cache_control = content.headers["cache-control"].lower()
    assert "private" in cache_control
    assert "no-store" in cache_control

    head = await client.head(f"/files/{uid}")
    assert head.status_code == 200
    head_cc = head.headers["cache-control"].lower()
    assert "private" in head_cc
    assert "no-store" in head_cc


@pytest.mark.asyncio
async def test_public_link_content_may_be_shared_cached(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    """Once `public_permission=read`, `/f/{uid}` is intentionally
    anonymous — edge caching is fine. Non-public files reachable via
    `/f/` only because of ACL must stay private (no shared cache)."""
    await _authenticated(client)
    created = await client.post(
        "/files",
        data={"provider_connection_id": connection_id, "name": "pub.txt"},
        files={"file": ("pub.txt", b"public bytes", "text/plain")},
    )
    uid = created.json()["uid"]
    await client.patch(f"/files/{uid}", json={"public_permission": "read"})

    anonymous = httpx.AsyncClient(
        transport=client._transport, base_url=str(client.base_url),
    )
    async with anonymous:
        content = await anonymous.get(f"/f/{uid}/pub.txt")
        assert content.status_code == 200
        assert content.content == b"public bytes"
        assert "public" in content.headers["cache-control"].lower()
        assert "no-store" not in content.headers["cache-control"].lower()


@pytest.mark.asyncio
async def test_anonymous_can_open_public_file_via_files_content(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    """A permanently public file must open on `/files/{uid}/content` too
    (not only `/f/{uid}`) -- same open matrix, 404 only when no grant."""
    await _authenticated(client)
    created = await client.post(
        "/files",
        data={"provider_connection_id": connection_id, "name": "banner.png"},
        files={"file": ("banner.png", b"banner-bytes", "image/png")},
    )
    uid = created.json()["uid"]
    await client.patch(f"/files/{uid}", json={"public_permission": "read"})

    anonymous = httpx.AsyncClient(
        transport=client._transport, base_url=str(client.base_url),
    )
    async with anonymous:
        content = await anonymous.get(f"/files/{uid}/content/banner.png")
        assert content.status_code == 200
        assert content.content == b"banner-bytes"
