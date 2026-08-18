"""Tests for local storage backend implementation."""

import logging
from collections.abc import Generator
from io import BytesIO

import pytest
from anyio import Path as AsyncPath

from apps.files.storage_backend.local_storage import LocalStorageBackend


@pytest.fixture(scope="session")
def temp_storage_dir() -> Generator[str]:
    """Create a temporary directory for file storage."""
    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:
        yield temp_dir


@pytest.fixture
def storage_backend(temp_storage_dir: str) -> LocalStorageBackend:
    """Create a LocalStorageBackend instance with temporary directory."""
    config = {
        "base_path": temp_storage_dir,
        "base_url": "http://localhost:8000/files",
        "create_dirs": True,
    }
    return LocalStorageBackend(config)


@pytest.mark.asyncio
async def test_upload_file(
    storage_backend: LocalStorageBackend, sample_file: BytesIO
) -> None:
    """Test uploading a file."""
    result = await storage_backend.upload_file(sample_file, "test.txt")

    assert result["key"] == "test.txt"
    assert result["size"] == len(sample_file.getvalue())
    assert result["url"] == "http://localhost:8000/files/test.txt"
    assert await AsyncPath(result["path"]).exists()


@pytest.mark.asyncio
async def test_download_file(
    storage_backend: LocalStorageBackend, sample_file: BytesIO
) -> None:
    """Test downloading a file."""
    # First upload a file
    await storage_backend.upload_file(sample_file, "test.txt")

    # Then download it
    downloaded = await storage_backend.download_file("test.txt")
    assert downloaded.getvalue() == sample_file.getvalue()


@pytest.mark.asyncio
async def test_stream_file(
    storage_backend: LocalStorageBackend, sample_file: BytesIO
) -> None:
    """Test streaming a file."""
    # Upload file first
    await storage_backend.upload_file(sample_file, "test.txt")

    # Stream the file
    chunks = [
        chunk async for chunk in storage_backend.stream_file("test.txt", chunk_size=4)
    ]

    # Combine chunks and verify content
    result = b"".join(chunks)
    assert result == sample_file.getvalue()


@pytest.mark.asyncio
async def test_delete_file(
    storage_backend: LocalStorageBackend, sample_file: BytesIO
) -> None:
    """Test deleting a file."""
    # Upload file first
    result = await storage_backend.upload_file(sample_file, "test.txt")
    file_path = AsyncPath(result["path"])

    # Verify file exists
    assert await file_path.exists()

    # Delete file
    success = await storage_backend.delete_file("test.txt")
    assert success
    assert not await file_path.exists()


@pytest.mark.asyncio
async def test_file_exists(
    storage_backend: LocalStorageBackend, sample_file: BytesIO
) -> None:
    """Test checking if a file exists."""
    # Check non-existent file
    exists = await storage_backend.file_exists("test.txt")
    assert not exists

    # Upload file
    await storage_backend.upload_file(sample_file, "test.txt")

    # Check existing file
    exists = await storage_backend.file_exists("test.txt")
    assert exists


@pytest.mark.asyncio
async def test_get_file_info(
    storage_backend: LocalStorageBackend, sample_file: BytesIO
) -> None:
    """Test getting file information."""
    # Upload file first
    await storage_backend.upload_file(sample_file, "test.txt")

    # Get file info
    info = await storage_backend.get_file_info("test.txt")

    assert info["key"] == "test.txt"
    assert info["size"] == len(sample_file.getvalue())
    assert info["content_type"] == "text/plain"
    assert "last_modified" in info
    assert "created" in info


@pytest.mark.asyncio
async def test_list_files(
    storage_backend: LocalStorageBackend, sample_file: BytesIO
) -> None:
    """Test listing files."""
    # Upload multiple files
    await storage_backend.upload_file(sample_file, "file1.txt")
    await storage_backend.upload_file(sample_file, "file2.txt")
    await storage_backend.upload_file(sample_file, "test.txt")
    await storage_backend.upload_file(sample_file, "subdir/file3.txt")

    # List all files
    files = await storage_backend.list_files()
    logging.info("Files: %s", "\n".join([file["key"] for file in files]))
    assert len(files) == 4

    # List files with prefix
    subdir_files = await storage_backend.list_files(prefix="subdir/")
    assert len(subdir_files) == 1
    assert subdir_files[0]["key"] == "subdir/file3.txt"


@pytest.mark.asyncio
async def test_copy_file(
    storage_backend: LocalStorageBackend, sample_file: BytesIO
) -> None:
    """Test copying a file."""
    # Upload original file
    await storage_backend.upload_file(sample_file, "original.txt")

    # Copy file
    success = await storage_backend.copy_file("original.txt", "copy.txt")
    assert success

    # Verify both files exist and have same content
    original = await storage_backend.download_file("original.txt")
    copy = await storage_backend.download_file("copy.txt")
    assert original.getvalue() == copy.getvalue()


@pytest.mark.asyncio
async def test_move_file(
    storage_backend: LocalStorageBackend, sample_file: BytesIO
) -> None:
    """Test moving a file."""
    # Upload original file
    await storage_backend.upload_file(sample_file, "source.txt")

    # Move file
    success = await storage_backend.move_file("source.txt", "destination.txt")
    assert success

    # Verify source doesn't exist and destination does
    assert not await storage_backend.file_exists("source.txt")
    assert await storage_backend.file_exists("destination.txt")


def test_get_public_url(storage_backend: LocalStorageBackend) -> None:
    """Test getting public URL for a file."""
    url = storage_backend.get_public_url("test.txt")
    assert url == "http://localhost:8000/files/test.txt"


@pytest.mark.asyncio
async def test_cleanup(
    storage_backend: LocalStorageBackend, sample_file: BytesIO
) -> None:
    """Test cleaning up storage."""
    # Upload some files
    await storage_backend.upload_file(sample_file, "file1.txt")
    await storage_backend.upload_file(sample_file, "file2.txt")

    # Verify files exist
    assert await storage_backend.file_exists("file1.txt")
    assert await storage_backend.file_exists("file2.txt")

    # Cleanup
    await storage_backend.cleanup()

    # Verify files are deleted
    assert not await storage_backend.file_exists("file1.txt")
    assert not await storage_backend.file_exists("file2.txt")


@pytest.mark.asyncio
async def test_file_not_found(storage_backend: LocalStorageBackend) -> None:
    """Test handling of non-existent files."""
    with pytest.raises(Exception) as exc_info:
        await storage_backend.download_file("nonexistent.txt")
    assert "File not found" in str(exc_info.value)


@pytest.mark.asyncio
async def test_directory_traversal_prevention(
    storage_backend: LocalStorageBackend, sample_file: BytesIO
) -> None:
    """Test prevention of directory traversal attacks."""
    # Try to upload file with path traversal
    result = await storage_backend.upload_file(sample_file, "../../../test.txt")

    # Verify file is stored safely within base directory
    assert "../" not in result["path"]
    assert result["path"].startswith(storage_backend.base_path.as_posix())
