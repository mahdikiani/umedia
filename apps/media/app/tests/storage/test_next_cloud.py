"""Tests for NextCloud storage backend implementation."""

from collections.abc import Generator
from io import BytesIO
from pathlib import Path

import pytest
from fastapi_mongo_base.core.exceptions import BaseHTTPException

from apps.files.storage_backend.nextcloud_storage import NextCloudStorageBackend
from server.config import Settings


@pytest.fixture
def storage_backend() -> Generator[NextCloudStorageBackend]:
    """Create a NextCloudStorageBackend instance with configuration from env."""

    if not Settings.NEXTCLOUD_BASE_URL:
        pytest.skip("NEXTCLOUD config is not set")

    nc = NextCloudStorageBackend(Settings().storage_config)
    yield nc
    # await nc.delete_file("testdir")


@pytest.mark.asyncio
async def test_upload_file(
    storage_backend: NextCloudStorageBackend,
    sample_file: BytesIO,
    test_key: str,
) -> None:
    """Test uploading a file."""
    result = await storage_backend.upload_file(
        sample_file, test_key, content_type="text/plain"
    )

    assert result["key"] == test_key
    assert result["size"] == len(sample_file.getvalue())
    # assert result["url"] == storage_backend._get_file_url(test_key)

    # Cleanup
    await storage_backend.delete_file(test_key)


@pytest.mark.asyncio
async def test_upload_and_download_file(
    storage_backend: NextCloudStorageBackend,
    sample_file: BytesIO,
    test_key: str,
    test_file: dict[str, object],
) -> None:
    """Test uploading and then downloading a file."""
    # Download
    downloaded = await storage_backend.download_file(test_key)
    assert downloaded.getvalue() == sample_file.getvalue()


@pytest.mark.asyncio
async def test_stream_file(
    storage_backend: NextCloudStorageBackend,
    sample_file: BytesIO,
    test_key: str,
    test_file: dict[str, object],
) -> None:
    """Test streaming a file."""
    # Stream the file
    chunks = [chunk async for chunk in storage_backend.stream_file(test_key)]
    content = b"".join(chunks)
    assert content == sample_file.getvalue()


@pytest.mark.asyncio
async def test_file_exists(
    storage_backend: NextCloudStorageBackend,
    sample_file: BytesIO,
    test_key: str,
) -> None:
    """Test checking if a file exists."""
    # Check non-existent file
    exists = await storage_backend.file_exists(test_key)
    assert not exists

    # Upload file
    await storage_backend.upload_file(sample_file, test_key)

    # Check existing file
    exists = await storage_backend.file_exists(test_key)
    assert exists

    # Cleanup
    await storage_backend.delete_file(test_key)


@pytest.mark.asyncio
async def test_get_file_info(
    storage_backend: NextCloudStorageBackend,
    sample_file: BytesIO,
    test_key: str,
    test_file: dict[str, object],
) -> None:
    """Test getting file information."""
    # Get file info
    info = await storage_backend.get_file_info(test_key)
    assert info["path"] == f"/{test_key}"
    assert int(info["attributes"].get("{DAV:}getcontentlength")) == len(
        sample_file.getvalue()
    )
    # assert info["url"] == storage_backend._get_file_url(test_key)


@pytest.mark.asyncio
async def test_nested_directories(
    storage_backend: NextCloudStorageBackend,
    sample_file: BytesIO,
    test_key: str,
) -> None:
    """Test creating and using nested directories."""
    nested_key = f"{Path(test_key).parent}/nested/deep/testfile.txt"

    # Upload to nested path
    await storage_backend.upload_file(sample_file, nested_key)

    # Verify file exists
    assert await storage_backend.file_exists(nested_key)

    # Download and verify content
    downloaded = await storage_backend.download_file(nested_key)
    assert downloaded.getvalue() == sample_file.getvalue()

    # Cleanup
    await storage_backend.delete_file(nested_key)


@pytest.mark.asyncio
async def test_generate_share_link(
    storage_backend: NextCloudStorageBackend,
    sample_file: BytesIO,
    test_key: str,
    test_file: dict[str, object],
) -> None:
    """Test generating share link for a file."""
    # Generate share link
    share_link = await storage_backend.generate_presigned_url(test_key)
    assert share_link is not None
    assert share_link.startswith(storage_backend.config.base_url)


@pytest.mark.asyncio
async def test_error_handling(
    storage_backend: NextCloudStorageBackend,
    test_key: str,
) -> None:
    """Test error handling for non-existent files."""
    path = Path(test_key).parent
    non_existent_key = f"{path}/nonexistent.txt"

    # Try to download non-existent file
    with pytest.raises(BaseHTTPException) as exc_info:
        await storage_backend.download_file(non_existent_key)
    assert exc_info.value.status_code == 404
    assert "File not found" in str(exc_info.value.detail)

    # Try to get info of non-existent file
    with pytest.raises(BaseHTTPException) as exc_info:
        await storage_backend.get_file_info(non_existent_key)
    assert exc_info.value.status_code == 404
    assert "File not found" in str(exc_info.value.detail)
