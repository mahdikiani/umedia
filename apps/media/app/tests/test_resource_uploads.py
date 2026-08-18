"""Route-level tests for the tus resumable-upload endpoints
(`apps/resources/uploads.py`) -- against the real HTTP app, a real spawned
`local` plugin, and the real background-task finalization path, mirroring
`test_resource_routes.py`'s pattern.

The completion hook schedules `_finalize()` as a detached background task
(module docstring explains why: so the browser's final PATCH response
isn't blocked on the actual plugin write) -- these tests can't assume the
resource exists the instant the PATCH response comes back, so they poll
briefly for it, same as a real frontend would poll `GET /resources`.
"""

import asyncio
import base64
import os
from pathlib import Path

import httpx
import pytest


def _tus_metadata(fields: dict[str, str]) -> str:
    """`key value` pairs, base64-encoded per the tus Creation extension --
    see apps.resources.uploads' `_validate_metadata` for the decoding
    side (that's tuspyserver's job, not ours, but tests build requests by
    hand rather than through tus-js-client)."""
    return ",".join(
        f"{key} {base64.b64encode(value.encode()).decode()}"
        for key, value in fields.items()
    )


async def _authenticated(client: httpx.AsyncClient) -> None:
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
    library = Path(os.environ["UMEDIA_DATA_DIR"]) / "storage" / "tus-uploads-library"
    response = await client.post(
        "/providers",
        json={
            "provider_type": "local",
            "name": "Tus uploads test library",
            "config": {"root_path": str(library)},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["uid"]


async def _create_upload(
    client: httpx.AsyncClient, *, size: int, metadata: dict[str, str],
) -> httpx.Response:
    return await client.post(
        "/uploads",
        headers={
            "Tus-Resumable": "1.0.0",
            "Upload-Length": str(size),
            "Upload-Metadata": _tus_metadata(metadata),
        },
    )


async def _patch_upload(
    client: httpx.AsyncClient, location: str, content: bytes, *, offset: int = 0,
) -> httpx.Response:
    return await client.patch(
        location,
        content=content,
        headers={
            "Tus-Resumable": "1.0.0",
            "Upload-Offset": str(offset),
            "Content-Type": "application/offset+octet-stream",
        },
    )


async def _wait_for_resource(
    client: httpx.AsyncClient, name: str, *, timeout_seconds: float = 5.0,
) -> dict:
    """Finalization is a detached background task (see module docstring)
    -- poll `GET /resources` the same way a real frontend would, rather
    than assuming it's already there when the PATCH response comes back."""
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    while asyncio.get_event_loop().time() < deadline:
        listed = await client.get("/resources")
        match = next((r for r in listed.json() if r["name"] == name), None)
        if match is not None and match["status"] != "processing":
            return match
        await asyncio.sleep(0.1)
    raise AssertionError(f"Resource '{name}' never finished processing")


@pytest.mark.asyncio
async def test_tus_upload_round_trip_creates_a_completed_resource(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    await _authenticated(client)
    content = b"hello from tus"

    created = await _create_upload(
        client,
        size=len(content),
        metadata={
            "provider_connection_id": connection_id,
            "name": "tus.txt",
            "filetype": "text/plain",
        },
    )
    assert created.status_code == 201, created.text
    location = created.headers["location"]

    patched = await _patch_upload(client, location, content)
    assert patched.status_code == 204, patched.text
    assert patched.headers["upload-offset"] == str(len(content))

    resource = await _wait_for_resource(client, "tus.txt")
    assert resource["status"] == "completed"
    assert resource["size"] == len(content)

    downloaded = await client.get(f"/resources/{resource['uid']}/content")
    assert downloaded.content == content


@pytest.mark.asyncio
async def test_tus_upload_is_resumable_across_two_patches(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    await _authenticated(client)
    first_half, second_half = b"first half-", b"second half"
    content = first_half + second_half

    created = await _create_upload(
        client,
        size=len(content),
        metadata={
            "provider_connection_id": connection_id,
            "name": "resumed.txt",
            "filetype": "text/plain",
        },
    )
    location = created.headers["location"]

    first = await _patch_upload(client, location, first_half, offset=0)
    assert first.status_code == 204
    assert first.headers["upload-offset"] == str(len(first_half))

    # A real client would re-HEAD to discover the offset after a dropped
    # connection; asserting it here doubles as the resumability contract.
    head = await client.head(
        location, headers={"Tus-Resumable": "1.0.0"},
    )
    assert head.headers["upload-offset"] == str(len(first_half))

    second = await _patch_upload(
        client, location, second_half, offset=len(first_half),
    )
    assert second.status_code == 204
    assert second.headers["upload-offset"] == str(len(content))

    resource = await _wait_for_resource(client, "resumed.txt")
    assert resource["status"] == "completed"
    assert resource["size"] == len(content)


@pytest.mark.asyncio
async def test_tus_upload_rejects_unknown_connection_before_staging_bytes(
    client: httpx.AsyncClient,
) -> None:
    await _authenticated(client)

    created = await _create_upload(
        client,
        size=5,
        metadata={
            "provider_connection_id": "does-not-exist",
            "name": "x.txt",
            "filetype": "text/plain",
        },
    )
    assert created.status_code == 400, created.text


@pytest.mark.asyncio
async def test_tus_upload_rejects_missing_name(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    await _authenticated(client)

    created = await _create_upload(
        client,
        size=5,
        metadata={"provider_connection_id": connection_id, "filetype": "text/plain"},
    )
    assert created.status_code == 400, created.text


@pytest.mark.asyncio
async def test_tus_upload_rejects_missing_filetype(
    client: httpx.AsyncClient, connection_id: str,
) -> None:
    # tuspyserver's own HEAD route requires this -- see uploads.py's
    # `_validate_metadata` docstring for why we also require it upfront.
    await _authenticated(client)

    created = await _create_upload(
        client,
        size=5,
        metadata={"provider_connection_id": connection_id, "name": "x.txt"},
    )
    assert created.status_code == 400, created.text


@pytest.mark.asyncio
async def test_tus_endpoints_require_authentication(client: httpx.AsyncClient) -> None:
    anonymous = httpx.AsyncClient(
        transport=client._transport,
        base_url=str(client.base_url),
    )
    async with anonymous:
        response = await anonymous.post(
            "/uploads",
            headers={
                "Tus-Resumable": "1.0.0",
                "Upload-Length": "5",
                "Upload-Metadata": _tus_metadata({"name": "x"}),
            },
        )
        assert response.status_code == 401
