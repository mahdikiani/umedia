"""Tests for storage backend implementations."""

import uuid
from collections.abc import AsyncGenerator
from io import BytesIO
from pathlib import Path

import pytest
from _pytest.fixtures import FixtureRequest
from fastapi_mongo_base.core.exceptions import BaseHTTPException

from apps.files.storage_backend import (
    LocalStorageBackend,
    NextCloudStorageBackend,
    S3StorageBackend,
    StorageBackend,
)
from server.config import Settings


@pytest.fixture(
    params=[
        pytest.param(S3StorageBackend, id="s3"),
        pytest.param(LocalStorageBackend, id="local"),
        pytest.param(NextCloudStorageBackend, id="nextcloud"),
    ]
)
async def storage_backend(request: FixtureRequest) -> AsyncGenerator:
    """Create a storage backend instance with configuration from env."""
    backend_class = request.param

    # Skip if required config is not available
    if backend_class == S3StorageBackend and not Settings.S3_ACCESS_KEY:
        pytest.skip("S3 config is not set")
    elif backend_class == NextCloudStorageBackend and not Settings.NEXTCLOUD_BASE_URL:
        pytest.skip("NEXTCLOUD config is not set")
    # LocalStorageBackend doesn't need external config, so no skip needed

    storage_backend: StorageBackend = backend_class(Settings().storage_config)
    yield storage_backend
    await storage_backend.cleanup()


@pytest.mark.asyncio
async def test_upload_file(
    storage_backend: S3StorageBackend | LocalStorageBackend | NextCloudStorageBackend,
    sample_file: BytesIO,
    test_key: str,
) -> None:
    """Test uploading a file."""
    result = await storage_backend.upload_file(
        sample_file, test_key, content_type="text/plain"
    )

    assert result["key"] == test_key
    assert result["size"] == len(sample_file.getvalue())
    if isinstance(storage_backend, NextCloudStorageBackend):
        assert result["url"] == storage_backend._get_file_url(test_key)
    elif isinstance(storage_backend, S3StorageBackend):
        assert result["url"] == f"{storage_backend.config.domain}/{test_key}"


@pytest.mark.asyncio
async def test_upload_and_download_file(
    storage_backend: S3StorageBackend | LocalStorageBackend | NextCloudStorageBackend,
    sample_file: BytesIO,
    test_key: str,
) -> None:
    """Test uploading and then downloading a file."""
    # Upload
    await storage_backend.upload_file(sample_file, test_key)

    # Download
    downloaded = await storage_backend.download_file(test_key)
    assert downloaded.getvalue() == sample_file.getvalue()


@pytest.mark.asyncio
async def test_stream_file(
    storage_backend: S3StorageBackend | LocalStorageBackend | NextCloudStorageBackend,
    sample_file: BytesIO,
    test_key: str,
) -> None:
    """Test streaming a file."""
    # Upload file first
    await storage_backend.upload_file(sample_file, test_key)

    # Stream the file
    chunks = [chunk async for chunk in storage_backend.stream_file(test_key)]
    content = b"".join(chunks)
    assert content == sample_file.getvalue()


@pytest.mark.asyncio
async def test_file_exists(
    storage_backend: S3StorageBackend | LocalStorageBackend | NextCloudStorageBackend,
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


@pytest.mark.asyncio
async def test_get_file_info(
    storage_backend: S3StorageBackend | LocalStorageBackend | NextCloudStorageBackend,
    sample_file: BytesIO,
    test_key: str,
) -> None:
    """Test getting file information."""
    # Upload file first
    await storage_backend.upload_file(sample_file, test_key)

    # Get file info
    info = await storage_backend.get_file_info(test_key)
    assert info["size"] == len(sample_file.getvalue())

    # Backend-specific assertions
    if isinstance(storage_backend, S3StorageBackend):
        assert info["content_type"] == "binary/octet-stream"
        assert "etag" in info
    elif isinstance(storage_backend, LocalStorageBackend):
        assert info["content_type"] == "text/plain"
        assert "last_modified" in info
    else:  # NextCloud
        assert "mtime" in info


@pytest.mark.asyncio
async def test_nested_directories(
    storage_backend: S3StorageBackend | LocalStorageBackend | NextCloudStorageBackend,
    sample_file: BytesIO,
    test_key: str,
) -> None:
    """Test creating and using nested directories."""
    nested_key = f"{Path(test_key).parent}/nested/deep/test.txt"

    # Upload to nested path
    await storage_backend.upload_file(sample_file, nested_key)

    # Verify file exists
    assert await storage_backend.file_exists(nested_key)

    # Download and verify content
    downloaded = await storage_backend.download_file(nested_key)
    assert downloaded.getvalue() == sample_file.getvalue()


@pytest.mark.asyncio
async def test_file_operations_with_special_chars(
    storage_backend: S3StorageBackend | LocalStorageBackend | NextCloudStorageBackend,
    sample_file: BytesIO,
) -> None:
    """Test file operations with special characters in the filename."""
    test_key = f"test/{uuid.uuid4()}/special @#$% chars.txt"

    # Upload file
    await storage_backend.upload_file(sample_file, test_key)

    # Verify file exists
    assert await storage_backend.file_exists(test_key)

    # Download and verify content
    downloaded = await storage_backend.download_file(test_key)
    assert downloaded.getvalue() == sample_file.getvalue()


@pytest.mark.asyncio
async def test_error_handling(
    storage_backend: S3StorageBackend | LocalStorageBackend | NextCloudStorageBackend,
) -> None:
    """Test error handling for non-existent files."""
    non_existent_key = f"test/{uuid.uuid4()}/nonexistent.txt"

    # Try to download non-existent file
    with pytest.raises(BaseHTTPException) as exc_info:
        await storage_backend.download_file(non_existent_key)
    assert exc_info.value.status_code in (404, 500)  # S3 returns 404, others 500
    assert any(
        msg in str(exc_info.value.detail)
        for msg in ("File not found", "Failed to download file")
    )

    # Try to get info of non-existent file
    with pytest.raises(BaseHTTPException) as exc_info:
        await storage_backend.get_file_info(non_existent_key)
    assert exc_info.value.status_code in (404, 500)
    assert any(
        msg in str(exc_info.value.detail)
        for msg in ("File not found", "Failed to get file info")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chunk_size,expected_chunks",
    [
        (4, 11),  # Small chunks
        (8, 6),  # Medium chunks
        (16, 3),  # Large chunks (entire file)
    ],
)
async def test_stream_file_chunk_sizes(
    storage_backend: S3StorageBackend | LocalStorageBackend | NextCloudStorageBackend,
    sample_file: BytesIO,
    test_key: str,
    chunk_size: int,
    expected_chunks: int,
) -> None:
    """Test streaming with different chunk sizes."""
    await storage_backend.upload_file(sample_file, test_key)

    chunks = [
        chunk
        async for chunk in storage_backend.stream_file(test_key, chunk_size=chunk_size)
    ]
    assert len(chunks) == expected_chunks
    assert b"".join(chunks) == sample_file.getvalue()


@pytest.mark.asyncio
async def test_large_file_handling(
    storage_backend: S3StorageBackend | LocalStorageBackend | NextCloudStorageBackend,
    test_key: str,
) -> None:
    """Test handling of large files (1MB)."""
    # Create a 1MB file
    content = b"x" * (1024 * 1024)  # 1MB
    large_file = BytesIO(content)

    # Upload and verify
    await storage_backend.upload_file(large_file, test_key)
    downloaded = await storage_backend.download_file(test_key)
    assert downloaded.getvalue() == content

    # Stream and verify
    streamed = b"".join([
        chunk async for chunk in storage_backend.stream_file(test_key, chunk_size=65536)
    ])
    assert streamed == content
