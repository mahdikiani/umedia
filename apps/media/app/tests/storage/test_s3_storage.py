"""Tests for S3 storage backend implementation."""

from collections.abc import AsyncGenerator
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pytest

from apps.files.storage_backend.s3_storage import S3StorageBackend
from server.config import Settings


@pytest.fixture
async def storage_backend() -> AsyncGenerator[S3StorageBackend]:
    """Create a S3StorageBackend instance with configuration from env."""
    if not Settings.S3_ACCESS_KEY:
        pytest.skip("NEXTCLOUD config is not set")

    storage_backend = S3StorageBackend()
    yield storage_backend
    await storage_backend.cleanup()


@pytest.mark.asyncio
async def test_session_health_check(storage_backend: S3StorageBackend) -> None:
    """Test S3 session health check."""
    is_healthy = await storage_backend._check_session_health()
    assert is_healthy


def test_upload_file(
    storage_backend: S3StorageBackend,
    sample_file: BytesIO,
    test_key: str,
    test_file: dict[str, object],
) -> None:
    """Test uploading a file."""
    assert test_file["key"] == test_key
    assert test_file["bucket"] == storage_backend.bucket_name
    assert test_file["size"] == len(sample_file.getvalue())
    assert test_file["url"] == f"{storage_backend.config.domain}/{test_key}"


@pytest.mark.asyncio
async def test_upload_and_download_file(
    storage_backend: S3StorageBackend,
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
    storage_backend: S3StorageBackend,
    sample_file: BytesIO,
    test_key: str,
    test_file: dict[str, object],
) -> None:
    """Test streaming a file."""
    chunks = [chunk async for chunk in storage_backend.stream_file(test_key)]
    content = b"".join(chunks)
    assert content == sample_file.getvalue()


@pytest.mark.asyncio
async def test_file_exists(
    storage_backend: S3StorageBackend,
    sample_file: BytesIO,
    test_key: str,
) -> None:
    """Test checking if a file exists."""
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
    storage_backend: S3StorageBackend,
    sample_file: BytesIO,
    test_key: str,
    test_file: dict[str, object],
) -> None:
    """Test getting file information."""
    info = await storage_backend.get_file_info(test_key)
    assert info["key"] == test_key
    assert info["size"] == len(sample_file.getvalue())
    assert info["content_type"] == "text/plain"
    assert isinstance(info["last_modified"], datetime)
    assert "etag" in info


@pytest.mark.asyncio
async def test_copy_file(
    storage_backend: S3StorageBackend,
    test_key: str,
    test_file: dict[str, object],
) -> None:
    """Test copying a file."""
    dest_key = f"{Path(test_key).parent}/copy.txt"

    # Copy file
    success = await storage_backend.copy_file(test_key, dest_key)
    assert success

    # Verify both files exist and have same content
    original = await storage_backend.download_file(test_key)
    copy = await storage_backend.download_file(dest_key)
    assert original.getvalue() == copy.getvalue()

    # Cleanup
    await storage_backend.delete_file(dest_key)


@pytest.mark.asyncio
async def test_move_file(
    storage_backend: S3StorageBackend,
    sample_file: BytesIO,
    test_key: str,
) -> None:
    """Test moving a file."""
    dest_key = f"{Path(test_key).parent}/moved.txt"

    # Upload original file
    await storage_backend.upload_file(sample_file, test_key)

    # Move file
    success = await storage_backend.move_file(test_key, dest_key)
    assert success

    # Verify source doesn't exist and destination does
    assert not await storage_backend.file_exists(test_key)
    assert await storage_backend.file_exists(dest_key)

    # Cleanup
    await storage_backend.delete_file(dest_key)


@pytest.mark.asyncio
async def test_list_files(
    storage_backend: S3StorageBackend,
    sample_file: BytesIO,
    test_key: str,
) -> None:
    """Test listing files."""
    test_prefix = str(Path(test_key).parent)
    files_to_create = [
        f"{test_prefix}/file1.txt",
        f"{test_prefix}/file2.txt",
        f"{test_prefix}/subdir/file3.txt",
    ]

    # Upload multiple files
    for file_key in files_to_create:
        await storage_backend.upload_file(sample_file, file_key)

    # List all files
    files = await storage_backend.list_files(prefix=test_prefix)
    assert len(files) == len(files_to_create)

    # List files with subdir prefix
    subdir_files = await storage_backend.list_files(prefix=f"{test_prefix}/subdir/")
    assert len(subdir_files) == 1
    assert subdir_files[0]["key"] == f"{test_prefix}/subdir/file3.txt"

    # Cleanup
    for file_key in files_to_create:
        await storage_backend.delete_file(file_key)


def test_get_public_url(storage_backend: S3StorageBackend, test_key: str) -> None:
    """Test getting public URL for a file."""
    url = storage_backend.get_public_url(test_key)
    assert url == f"{storage_backend.config.domain}/{test_key}"


@pytest.mark.asyncio
async def test_generate_presigned_url(
    storage_backend: S3StorageBackend,
    test_key: str,
    test_file: dict[str, object],
) -> None:
    """Test generating presigned URL."""
    url = await storage_backend.generate_presigned_url(test_key, expires_in=3600)
    assert url is not None
    assert test_key in url
    assert "Expires=" in url
    assert "Signature=" in url
