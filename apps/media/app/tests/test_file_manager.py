"""Tests for FileManager class."""

from io import BytesIO
from unittest.mock import patch

import pytest
from fastapi import UploadFile
from fastapi_mongo_base.core.exceptions import BaseHTTPException

from apps.files.file_manager import FileManager
from apps.files.file_validator import FileHashCalculator
from apps.files.models import FileMetaData, ObjectMetaData
from apps.files.schemas import PermissionEnum
from apps.files.storage_backend import LocalStorageBackend

# @pytest_asyncio.fixture
# async def file_manager(file_manager: FileManager) -> AsyncGenerator[FileManager]:
#     """Create a FileManager instance for testing."""
#     from apps.files.storage_backend import LocalStorageBackend

#     original_backend = file_manager.storage_backend
#     file_manager._storage_backend = LocalStorageBackend()
#     yield file_manager
#     await file_manager.storage_backend.cleanup()
#     file_manager._storage_backend = original_backend


@pytest.fixture
def upload_file(sample_file: BytesIO) -> UploadFile:
    """Create an UploadFile instance for testing."""
    sample_file.seek(0)
    return UploadFile(file=sample_file, filename="test.txt")


def test_storage_backend_property(file_manager: FileManager) -> None:
    """Test storage_backend property."""
    backend = file_manager.storage_backend
    assert backend is not None
    assert file_manager._storage_backend is not None


@pytest.mark.asyncio
async def test_get_filepath_with_filename(file_manager: FileManager) -> None:
    """Test _get_filepath with filename parameter."""
    filepath, filename = await file_manager._get_filepath(
        user_id="test_user", filename="test/path/file.txt"
    )
    assert filepath == "test/path/file.txt"
    assert filename == "file.txt"


@pytest.mark.asyncio
async def test_get_filepath_with_file_filename(file_manager: FileManager) -> None:
    """Test _get_filepath with file_filename parameter."""
    filepath, filename = await file_manager._get_filepath(
        user_id="test_user", file_filename="uploaded_file.txt"
    )
    assert filepath == "uploaded_file.txt"
    assert filename == "uploaded_file.txt"


@pytest.mark.asyncio
async def test_get_filepath_invalid_ending_slash(file_manager: FileManager) -> None:
    """Test _get_filepath with invalid filename ending with slash."""
    with pytest.raises(BaseHTTPException) as exc_info:
        await file_manager._get_filepath(user_id="test_user", filename="test/path/")
    assert exc_info.value.status_code == 400
    assert exc_info.value.error_code == "invalid_filename"


@pytest.mark.asyncio
async def test_process_file_success(
    file_manager: FileManager, upload_file: UploadFile
) -> None:
    """Test successful file processing."""

    result = await file_manager.process_file(
        file=upload_file, user_id="test_user", blocking=True
    )

    assert isinstance(result, FileMetaData)
    assert result.user_id == "test_user"
    assert result.filename == "test.txt"
    assert result.content_type == "text/plain"
    assert result.size > 0
    assert result.filehash is not None


@pytest.mark.asyncio
async def test_process_file_stamps_workspace_id(
    file_manager: FileManager, upload_file: UploadFile
) -> None:
    """A workspace-scoped upload records workspace_id on the file."""
    result = await file_manager.process_file(
        file=upload_file, user_id="test_user", workspace_id="ws-1", blocking=True
    )

    assert result.workspace_id == "ws-1"


@pytest.mark.asyncio
async def test_process_file_existing_file(
    file_manager: FileManager, upload_file: UploadFile
) -> None:
    """Test processing file that already exists."""
    # Create an existing file with same hash
    existing_file = FileMetaData(
        user_id="test_user",
        filename="existing.txt",
        filehash=FileHashCalculator.calculate_file_hash_from_upload(upload_file),
        key="test_key",
        content_type="text/plain",
        size=100,
        parent_id=None,
    )
    await existing_file.save()

    result = await file_manager.process_file(
        file=upload_file,
        user_id="test_user",
        parent_id=existing_file.parent_id,
        blocking=True,
    )

    # Should return existing file
    assert result.uid == existing_file.uid


@pytest.mark.asyncio
async def test_change_file_success(
    file_manager: FileManager, upload_file: UploadFile
) -> None:
    """Test successful file change."""
    # Create existing file metadata
    existing_file = FileMetaData(
        user_id="test_user",
        filename="old.txt",
        filehash="old_hash",
        key="old_key",
        content_type="text/plain",
        size=100,
        parent_id=None,
    )
    await existing_file.save()

    result = await file_manager.change_file(
        file_metadata=existing_file, file=upload_file, blocking=True
    )

    assert result.filename == "test.txt"
    assert result.filehash != "old_hash"
    assert result.key != "old_key"


@pytest.mark.asyncio
async def test_change_file_with_history(
    file_manager: FileManager, upload_file: UploadFile
) -> None:
    """Test file change with history preservation."""
    existing_file = FileMetaData(
        user_id="test_user",
        filename="old.txt",
        filehash="old_hash",
        key="old_key",
        content_type="text/plain",
        size=100,
        parent_id=None,
    )
    await existing_file.save()

    result = await file_manager.change_file(
        file_metadata=existing_file,
        file=upload_file,
        blocking=True,
        overwrite=False,
    )

    assert len(result.history) > 0
    assert result.history[0].filehash == "old_hash"


@pytest.mark.asyncio
async def test_manage_upload_to_storage_success(file_manager: FileManager) -> None:
    """Test successful upload to storage."""
    file_bytes = BytesIO(b"test content")
    file_key = "test/key"
    filehash = "test_hash"
    content_type = "text/plain"
    size = 12

    result = await file_manager._manage_upload_to_storage(
        file_bytes=file_bytes,
        file_key=file_key,
        filehash=filehash,
        content_type=content_type,
        size=size,
    )

    assert isinstance(result, ObjectMetaData)
    assert result.key == file_key
    assert result.object_hash == filehash
    assert result.content_type == content_type
    assert result.size == size


@pytest.mark.asyncio
async def test_manage_upload_to_storage_existing_object(
    file_manager: FileManager,
) -> None:
    """Test upload when object already exists."""
    # Create existing object
    existing_obj = ObjectMetaData(
        key="test/key", size=100, object_hash="test_hash", content_type="text/plain"
    )
    await existing_obj.save()

    file_bytes = BytesIO(b"test content")
    file_key = "test/key"
    filehash = "test_hash"
    content_type = "text/plain"
    size = 12

    result = await file_manager._manage_upload_to_storage(
        file_bytes=file_bytes,
        file_key=file_key,
        filehash=filehash,
        content_type=content_type,
        size=size,
    )

    assert result.uid == existing_obj.uid


@pytest.mark.asyncio
async def test_manage_upload_to_storage_upload_failed(
    file_manager: FileManager,
) -> None:
    """Test upload failure handling."""
    file_bytes = BytesIO(b"test content")
    file_key = "test/key"
    filehash = "test_hash"
    content_type = "text/plain"
    size = 12

    with patch.object(file_manager.storage_backend, "upload_file") as mock_upload:
        mock_upload.return_value = False
        with pytest.raises(BaseHTTPException) as exc_info:
            await file_manager._manage_upload_to_storage(
                file_bytes=file_bytes,
                file_key=file_key,
                filehash=filehash,
                content_type=content_type,
                size=size,
            )

    assert exc_info.value.status_code == 500
    assert exc_info.value.error_code == "upload_failed"


@pytest.mark.asyncio
async def test_download_file(
    file_manager: FileManager, upload_file: UploadFile
) -> None:
    """Test file download."""
    file_metadata = await file_manager.process_file(
        file=upload_file, user_id="test_user", blocking=True
    )

    result = await file_manager.download_file(file_metadata)

    assert isinstance(result, BytesIO)
    assert result.read() == b"Hello, World! This is a test file content."


@pytest.mark.asyncio
async def test_stream_file(file_manager: FileManager, upload_file: UploadFile) -> None:
    """Test file streaming."""

    file_metadata = await file_manager.process_file(
        file=upload_file, user_id="test_user", blocking=True
    )

    chunks = [chunk async for chunk in file_manager.stream_file(file_metadata)]

    assert chunks == [b"Hello, World! This is a test file content."]


@pytest.mark.asyncio
async def test_delete_file(file_manager: FileManager) -> None:
    """Test file deletion."""
    file_metadata = FileMetaData(
        user_id="test_user",
        filename="test.txt",
        filehash="test_hash",
        key="test/key",
        content_type="text/plain",
        size=100,
    )

    result = await file_manager.delete_file(file_metadata)

    assert result is True


@pytest.mark.asyncio
async def test_generate_presigned_url(
    file_manager: FileManager, upload_file: UploadFile
) -> None:
    """Test presigned URL generation."""
    file_metadata = await file_manager.process_file(
        file=upload_file, user_id="test_user", blocking=True
    )

    if not isinstance(file_manager.storage_backend, LocalStorageBackend):
        result = await file_manager.generate_presigned_url(
            file_metadata, expires_in=3600
        )
        assert result is not None


@pytest.mark.asyncio
async def test_process_file_with_public_permission(
    file_manager: FileManager, upload_file: UploadFile
) -> None:
    """Test file processing with public permission."""

    result = await file_manager.process_file(
        file=upload_file,
        user_id="test_user",
        blocking=True,
    )

    assert result.public_permission.permission == PermissionEnum.READ


@pytest.mark.asyncio
async def test_process_file_invalid_public_permission(
    file_manager: FileManager, upload_file: UploadFile
) -> None:
    """Test file processing with invalid public permission."""

    # Should not raise exception, just log warning
    result = await file_manager.process_file(
        file=upload_file,
        user_id="test_user",
        blocking=True,
        public_permission='{"invalid": "json"}',
    )

    assert result is not None
    assert result.public_permission.permission == PermissionEnum.NONE
