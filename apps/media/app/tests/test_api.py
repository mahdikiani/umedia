import logging
from collections.abc import AsyncGenerator
from io import BytesIO
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from server.config import Settings


@pytest.fixture(scope="module")
def sample_file() -> BytesIO:
    """Create a sample file for testing."""
    content = b"Hello, World! This is a test file content."
    file = BytesIO(content)
    file.name = "test.txt"
    return file


@pytest_asyncio.fixture(scope="module")
async def media_id(
    authenticated_client: httpx.AsyncClient, sample_file: BytesIO
) -> AsyncGenerator[str]:
    response = await authenticated_client.post(
        "/f/upload",
        files={"file": ("test.txt", sample_file.read(), "text/plain")},
        params={"blocking": 1},
    )
    uid = response.json().get("uid")
    assert response.status_code == 200
    assert uid is not None
    yield uid
    sample_file.seek(0)


async def mine(authenticated_client: httpx.AsyncClient) -> list[dict]:
    response = await authenticated_client.get("/f")
    items = response.json().get("items")
    assert response.status_code == 200
    assert items is not None
    return items


@pytest.mark.asyncio
async def test_list_empty(authenticated_client: httpx.AsyncClient) -> None:
    response = await authenticated_client.get("/f")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 0


@pytest.mark.asyncio
async def test_retrieve_detail(
    authenticated_client: httpx.AsyncClient, media_id: str
) -> None:
    uid = media_id
    response = await authenticated_client.get(f"/f/{uid}", params={"details": 1})
    logging.info(response.json())
    assert response.status_code == 200
    assert response.json()["uid"] == uid
    assert uid in [item["uid"] for item in await mine(authenticated_client)]


@pytest.mark.asyncio
async def test_retrieve_sign_url(
    authenticated_client: httpx.AsyncClient, media_id: str
) -> None:
    uid = media_id
    if Settings.STORAGE_BACKEND == "s3":
        response = await authenticated_client.get(f"/f/{uid}", params={"signed_url": 1})
        assert response.status_code == 307
        assert (
            response.headers["Location"]
            == f"https://{Settings.root_url}{Settings.base_path}/f/{uid}"
        )
    else:
        with pytest.raises(NotImplementedError):
            response = await authenticated_client.get(
                f"/f/{uid}", params={"signed_url": 1}
            )


@pytest.mark.asyncio
async def test_retrieve_download(
    authenticated_client: httpx.AsyncClient, media_id: str
) -> None:
    uid = media_id
    response = await authenticated_client.get(f"/f/{uid}")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "text/plain"
    assert response.headers["Content-Length"] == "42"
    assert await response.aread() == b"Hello, World! This is a test file content."


@pytest.mark.asyncio
async def test_retrieve_download_range(
    authenticated_client: httpx.AsyncClient, media_id: str
) -> None:
    uid = media_id
    response = await authenticated_client.get(
        f"/f/{uid}", headers={"Range": "bytes=0-5"}
    )
    assert response.status_code == 206
    assert response.headers["Content-Type"] == "text/plain"
    assert response.headers["Content-Length"] == "6"
    assert await response.aread() == b"Hello,"


@pytest.mark.asyncio
async def test_download_public_premission_unauthenticated(
    client: httpx.AsyncClient, media_id: str
) -> None:
    uid = media_id
    response = await client.get(f"/f/{uid}")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "text/plain"
    assert response.headers["Content-Length"] == "42"
    assert await response.aread() == b"Hello, World! This is a test file content."


@pytest.mark.asyncio
async def test_download_public_premission_authenticated(
    authenticated_client: httpx.AsyncClient, media_id: str
) -> None:
    uid = media_id
    response = await authenticated_client.get(f"/f/{uid}")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "text/plain"
    assert response.headers["Content-Length"] == "42"
    assert await response.aread() == b"Hello, World! This is a test file content."


@pytest.mark.asyncio
async def test_shared_premission(client: httpx.AsyncClient, media_id: str) -> None:
    uid = media_id
    response = await client.get(f"/f/{uid}")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "text/plain"
    assert response.headers["Content-Length"] == "42"
    assert await response.aread() == b"Hello, World! This is a test file content."


@pytest.mark.asyncio
async def test_head(authenticated_client: httpx.AsyncClient, media_id: str) -> None:
    uid = media_id
    response = await authenticated_client.head(f"/f/{uid}")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "text/plain"
    assert response.headers["Content-Length"] == "42"


@pytest.mark.asyncio
async def test_update_file(
    authenticated_client: httpx.AsyncClient, media_id: str
) -> None:
    uid = media_id
    response = await authenticated_client.patch(
        f"/f/{uid}", json={"filename": "test2.txt"}
    )
    assert response.status_code == 200
    assert response.json()["filename"] == "test2.txt"
    assert uid in [item["uid"] for item in await mine(authenticated_client)]

    response = await authenticated_client.head(f"/f/{uid}", params={"details": 1})
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "text/plain"
    assert response.headers["Content-Length"] == "42"
    assert response.headers["Last-Modified"] is not None
    assert response.headers["ETag"] is not None


@pytest.mark.asyncio
async def test_change_file(
    authenticated_client: httpx.AsyncClient, media_id: str
) -> None:
    uid = media_id
    response = await authenticated_client.put(
        f"/f/{uid}", files={"file": ("test.txt", b"Good bye world!", "text/plain")}
    )
    assert response.status_code == 200
    assert response.json()["filename"] == "test.txt"
    assert uid in [item["uid"] for item in await mine(authenticated_client)]

    response = await authenticated_client.get(f"/f/{uid}")
    assert response.status_code == 200
    assert await response.aread() == b"Good bye world!"


@pytest.mark.asyncio
async def test_volume_check(
    authenticated_client: httpx.AsyncClient, media_id: str
) -> None:
    response = await authenticated_client.get("/f/statistics")
    assert response.status_code == 200
    assert response.json()["total_size"] == 15
    assert response.json()["history_size"] == 42


@pytest.mark.asyncio
async def test_delete_file(
    authenticated_client: httpx.AsyncClient, media_id: str
) -> None:
    from apps.files.file_manager import file_manager

    base_path = Path(file_manager.storage_backend.config["base_path"])
    uid = media_id
    response = await authenticated_client.delete(f"/f/{uid}")
    assert response.status_code == 200
    assert uid not in [item["uid"] for item in await mine(authenticated_client)]

    response = await authenticated_client.get(f"/f/{uid}")
    assert response.status_code == 404

    # restore
    response = await authenticated_client.patch(
        f"/f/{uid}", params={"is_deleted": 1}, json={"is_deleted": 0}
    )
    assert response.status_code == 200
    assert uid in [item["uid"] for item in await mine(authenticated_client)]

    response = await authenticated_client.get(f"/f/{uid}", params={"details": 1})
    assert response.status_code == 200
    filehash = response.json().get("filehash")
    assert filehash is not None
    assert filehash in [item["filehash"] for item in await mine(authenticated_client)]
    assert (base_path / "media" / filehash).exists()

    # delete from storage
    response = await authenticated_client.delete(f"/f/{uid}")
    assert (base_path / "media" / filehash).exists()

    response = await authenticated_client.delete(f"/f/{uid}", params={"is_deleted": 1})
    assert response.status_code == 200
    assert uid not in [item["uid"] for item in await mine(authenticated_client)]

    assert not (base_path / "media" / filehash).exists()
