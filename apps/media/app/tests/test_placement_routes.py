"""Instance placement settings: `GET/PATCH /settings/placement`."""

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


@pytest.mark.asyncio
async def test_get_and_patch_placement(
    client: httpx.AsyncClient,
) -> None:
    await _authenticated(client)
    created = await client.post(
        "/providers",
        json={
            "provider_type": "local",
            "name": "placement-library",
            "config": {"root_path": str(_library_dir("placement-library"))},
        },
    )
    assert created.status_code == 201, created.text
    connection_id = created.json()["uid"]

    fetched = await client.get("/settings/placement")
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    assert body["policy"] == "default"
    assert body["default_connection_id"] is None
    assert body["fill_order"] == []

    patched = await client.patch(
        "/settings/placement",
        json={
            "policy": "fill_order",
            "default_connection_id": connection_id,
            "fill_order": [connection_id],
        },
    )
    assert patched.status_code == 200, patched.text
    updated = patched.json()
    assert updated["policy"] == "fill_order"
    assert updated["default_connection_id"] == connection_id
    assert updated["fill_order"] == [connection_id]

    reread = await client.get("/settings/placement")
    assert reread.json() == updated

    unknown = await client.patch(
        "/settings/placement",
        json={"default_connection_id": "missing-connection"},
    )
    assert unknown.status_code == 422


def test_bind_existing_folders_migration_follows_placement() -> None:
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0009_bind_existing_folders.py"
    )
    spec = importlib.util.spec_from_file_location("bind_existing_folders", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0009_bind_existing_folders"
    assert module.down_revision == "0008_folder_placement"


@pytest.mark.asyncio
async def test_upload_without_connection_id_uses_placement(
    client: httpx.AsyncClient,
) -> None:
    await _authenticated(client)
    created = await client.post(
        "/providers",
        json={
            "provider_type": "local",
            "name": "root-upload-library",
            "config": {"root_path": str(_library_dir("root-upload-library"))},
        },
    )
    assert created.status_code == 201, created.text
    connection_id = created.json()["uid"]
    placed = await client.patch(
        "/settings/placement",
        json={
            "policy": "default",
            "default_connection_id": connection_id,
            "fill_order": [],
        },
    )
    assert placed.status_code == 200, placed.text

    uploaded = await client.post(
        "/files",
        data={"name": "placed.txt"},
        files={"file": ("placed.txt", b"from-policy", "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert body["provider_connection_id"] == connection_id
    assert body["status"] == "completed"
