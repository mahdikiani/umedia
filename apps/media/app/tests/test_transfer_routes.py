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
            "name": "Transfer routes library",
            "config": {"root_path": str(_library_dir("transfer-routes-library"))},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["uid"]


async def _wait_transfer(
    client: httpx.AsyncClient, uid: str, *, timeout: float = 10.0,
) -> dict:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        response = await client.get(f"/files/transfers/{uid}")
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] in {"completed", "failed", "partial", "cancelled"}:
            return body
        await asyncio.sleep(0.05)
    raise AssertionError(f"transfer {uid} did not finish")


@pytest.mark.asyncio
async def test_create_transfer_returns_202_and_completes_move(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    await _authenticated(client)
    folder = await client.post("/files", data={"name": "inbox", "type": "folder"})
    assert folder.status_code == 201, folder.text
    folder_uid = folder.json()["uid"]

    uploaded = await client.post(
        "/files",
        data={"provider_connection_id": connection_id, "name": "move-me.txt"},
        files={"file": ("move-me.txt", b"payload", "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    file_uid = uploaded.json()["uid"]

    created = await client.post(
        "/files/transfers",
        json={
            "operation": "move",
            "source_ids": [file_uid],
            "dest_parent_id": folder_uid,
        },
    )
    assert created.status_code == 202, created.text
    body = created.json()
    assert body["status"] == "queued"
    assert body["operation"] == "move"
    assert body["source_ids"] == [file_uid]
    assert body["progress_pct"] == 0

    done = await _wait_transfer(client, body["uid"])
    assert done["status"] == "completed"
    assert done["progress_pct"] == 100
    assert done["done_items"] == done["total_items"]

    fetched = await client.get(f"/files/{file_uid}")
    assert fetched.status_code == 200
    assert fetched.json()["parent_id"] == folder_uid


@pytest.mark.asyncio
async def test_list_transfers(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    await _authenticated(client)
    folder = await client.post(
        "/files",
        data={"name": "Transfer list target", "type": "folder"},
    )
    assert folder.status_code == 201, folder.text

    uploaded = await client.post(
        "/files",
        data={"provider_connection_id": connection_id, "name": "listed.txt"},
        files={"file": ("listed.txt", b"s", "text/plain")},
    )
    assert uploaded.status_code == 201
    created = await client.post(
        "/files/transfers",
        json={
            "operation": "move",
            "source_ids": [uploaded.json()["uid"]],
            "dest_parent_id": folder.json()["uid"],
        },
    )
    assert created.status_code == 202
    await _wait_transfer(client, created.json()["uid"])

    listed = await client.get("/files/transfers")
    assert listed.status_code == 200
    items = listed.json()
    assert isinstance(items, list)
    assert any(item["uid"] == created.json()["uid"] for item in items)


@pytest.mark.asyncio
async def test_get_transfer_404_for_other_user(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    await _authenticated(client)
    uploaded = await client.post(
        "/files",
        data={"provider_connection_id": connection_id, "name": "private-xfer.txt"},
        files={"file": ("private-xfer.txt", b"p", "text/plain")},
    )
    assert uploaded.status_code == 201
    created = await client.post(
        "/files/transfers",
        json={
            "operation": "copy",
            "source_ids": [uploaded.json()["uid"]],
            "dest_parent_id": None,
        },
    )
    assert created.status_code == 202
    transfer_uid = created.json()["uid"]
    await _wait_transfer(client, transfer_uid)

    member_credentials = {
        "email": "transfer-viewer@example.com",
        "password": "a secure viewer password",
    }
    created_user = await client.post(
        "/users", json={**member_credentials, "role": "user"},
    )
    assert created_user.status_code == 201, created_user.text

    member = httpx.AsyncClient(
        transport=client._transport, base_url=str(client.base_url),
    )
    async with member:
        login = await member.post("/auth/sessions", json=member_credentials)
        assert login.status_code == 201, login.text
        assert (await member.get(f"/files/transfers/{transfer_uid}")).status_code == 404
