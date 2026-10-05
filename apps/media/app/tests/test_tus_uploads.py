import asyncio
import base64
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


def _metadata(fields: dict[str, str]) -> str:
    return ",".join(
        f"{key} {base64.b64encode(value.encode()).decode()}"
        for key, value in fields.items()
    )


@pytest.fixture(scope="module")
async def connection_id(client: httpx.AsyncClient) -> str:
    await _authenticated(client)
    root = Path(os.environ["UMEDIA_DATA_DIR"]) / "storage" / "tus-library"
    response = await client.post(
        "/providers",
        json={
            "provider_type": "local",
            "name": "tus-upload-test-library",
            "config": {"root_path": str(root)},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["uid"]


async def _create_upload(
    client: httpx.AsyncClient,
    *,
    size: int,
    metadata: dict[str, str],
) -> httpx.Response:
    return await client.post(
        "/uploads",
        headers={
            "Tus-Resumable": "1.0.0",
            "Upload-Length": str(size),
            "Upload-Metadata": _metadata(metadata),
        },
    )


@pytest.mark.asyncio
async def test_tus_upload_finalizes_media_file_and_removes_staged_bytes(
    client: httpx.AsyncClient,
    connection_id: str,
) -> None:
    await _authenticated(client)
    content = b"resumable MediaFile upload"
    created = await _create_upload(
        client,
        size=len(content),
        metadata={
            "provider_connection_id": connection_id,
            "name": "resumable.txt",
            "filetype": "text/plain",
        },
    )
    assert created.status_code == 201, created.text

    location = created.headers["location"]
    patched = await client.patch(
        location,
        content=content,
        headers={
            "Tus-Resumable": "1.0.0",
            "Upload-Offset": "0",
            "Content-Type": "application/offset+octet-stream",
        },
    )
    assert patched.status_code == 204, patched.text
    assert patched.headers["upload-offset"] == str(len(content))

    deadline = asyncio.get_event_loop().time() + 5
    media_file: dict[str, str | int] | None = None
    while asyncio.get_event_loop().time() < deadline:
        page = await client.get("/files")
        media_file = next(
            (item for item in page.json()["items"] if item["name"] == "resumable.txt"),
            None,
        )
        if media_file is not None and media_file["status"] != "processing":
            break
        await asyncio.sleep(0.1)

    assert media_file is not None, "upload finalizer did not create a MediaFile"
    assert media_file["status"] == "completed"
    assert media_file["size"] == len(content)
    downloaded = await client.get(f"/files/{media_file['uid']}/content")
    assert downloaded.content == content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata", "expected_message"),
    [
        ({"filetype": "text/plain"}, "must include 'name'"),
        ({"name": "missing-type.txt"}, "must include 'filetype'"),
    ],
)
async def test_tus_rejects_missing_required_metadata_before_staging(
    client: httpx.AsyncClient,
    connection_id: str,
    metadata: dict[str, str],
    expected_message: str,
) -> None:
    await _authenticated(client)
    response = await _create_upload(
        client,
        size=5,
        metadata={**metadata, "provider_connection_id": connection_id},
    )

    assert response.status_code == 400
    assert expected_message in response.text


@pytest.mark.asyncio
async def test_tus_rejects_unknown_provider_connection_before_staging(
    client: httpx.AsyncClient,
) -> None:
    await _authenticated(client)
    response = await _create_upload(
        client,
        size=5,
        metadata={
            "provider_connection_id": "missing-connection",
            "name": "rejected.txt",
            "filetype": "text/plain",
        },
    )

    assert response.status_code == 400
    assert "Unknown provider_connection_id" in response.text


@pytest.mark.asyncio
async def test_tus_endpoints_require_authentication(client: httpx.AsyncClient) -> None:
    anonymous = httpx.AsyncClient(
        transport=client._transport,
        base_url=str(client.base_url),
    )
    async with anonymous:
        response = await _create_upload(
            anonymous,
            size=1,
            metadata={"name": "private.txt", "filetype": "text/plain"},
        )

    assert response.status_code == 401
