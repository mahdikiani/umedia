"""Integration tests for the complete file management workflow."""

import base64
from dataclasses import dataclass, field
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import UploadFile
from fastapi_mongo_base.core.exceptions import BaseHTTPException
from usso.exceptions import PermissionDenied

from apps.files.file_manager import FileManager
from apps.files.models import FileMetaData, ObjectMetaData
from apps.files.routes import FilesRouter, resolve_upload_identity
from apps.files.schemas import (
    FileMetaDataSchema,
    FileMetaDataUpdate,
    PermissionEnum,
    PermissionSchema,
)


@pytest.fixture
def files_router() -> FilesRouter:
    """Create a FilesRouter instance for testing."""
    return FilesRouter()


@pytest.fixture
def sample_file_content() -> bytes:
    """Create sample file content."""
    return b"Hello, World! This is a test file for integration testing."


@pytest.fixture
def upload_file(sample_file_content: bytes) -> UploadFile:
    """Create an UploadFile for testing."""
    file = BytesIO(sample_file_content)
    return UploadFile(file=file, filename="integration_test.txt")


@pytest.mark.asyncio
async def test_complete_file_upload_workflow(
    file_manager: FileManager, upload_file: UploadFile
) -> None:
    """Test complete file upload workflow."""
    # Step 1: Process file
    file_metadata = await file_manager.process_file(
        file=upload_file, user_id="integration_user", blocking=True
    )

    # Verify file metadata
    assert file_metadata.user_id == "integration_user"
    assert file_metadata.filename == "integration_test.txt"
    assert file_metadata.content_type == "text/plain"
    assert file_metadata.size > 0
    assert file_metadata.filehash is not None
    assert file_metadata.key is not None

    # Save to database
    # await file_metadata.save()

    # Step 2: Verify file exists in database
    retrieved_file = await FileMetaData.get_item(
        uid=file_metadata.uid, user_id="integration_user"
    )
    assert retrieved_file is not None
    assert retrieved_file.uid == file_metadata.uid

    # Step 3: Verify object metadata exists
    object_metadata = await ObjectMetaData.get_key(file_metadata.key)
    assert object_metadata is not None
    assert object_metadata.key == file_metadata.key
    assert object_metadata.object_hash == file_metadata.filehash

    # Step 4: Test file download
    downloaded_content = await file_manager.download_file(file_metadata)
    assert downloaded_content is not None
    assert (
        downloaded_content.read()
        == b"Hello, World! This is a test file for integration testing."
    )

    # Step 5: Test file streaming
    stream_chunks = [chunk async for chunk in file_manager.stream_file(file_metadata)]

    assert len(stream_chunks) > 0
    assert (
        b"".join(stream_chunks)
        == b"Hello, World! This is a test file for integration testing."
    )


@pytest.mark.asyncio
async def test_file_update_workflow(
    file_manager: FileManager, upload_file: UploadFile
) -> None:
    """Test file update workflow."""
    # Step 1: Create initial file
    initial_file = await file_manager.process_file(
        file=upload_file, user_id="update_user", blocking=True
    )
    await initial_file.save()
    initial_hash = initial_file.filehash
    initial_key = initial_file.key

    # Step 2: Create new file content
    new_content = b"Updated file content for testing."
    new_file = BytesIO(new_content)
    new_upload_file = UploadFile(file=new_file, filename="updated_test.txt")

    # Step 3: Update file
    updated_file = await file_manager.change_file(
        file_metadata=initial_file,
        file=new_upload_file,
        blocking=True,
        overwrite=False,  # Keep history
    )

    # Verify update
    assert updated_file.filename == "updated_test.txt"
    assert updated_file.filehash != initial_hash
    assert updated_file.key != initial_key
    assert len(updated_file.history) > 0
    assert updated_file.history[0].filehash == initial_hash

    # Step 4: Verify new content
    downloaded_content = await file_manager.download_file(updated_file)
    assert downloaded_content.read() == new_content


@pytest.mark.asyncio
async def test_directory_operations() -> None:
    """Test directory operations."""
    # Step 1: Create root directory
    root_dir = await FileMetaData.create_directory(
        user_id="dir_user", dirname="root", parent_id=None
    )

    # Step 2: Create subdirectory
    sub_dir = await FileMetaData.create_directory(
        user_id="dir_user", dirname="sub", parent_id=root_dir.uid
    )

    # Step 3: Create file in subdirectory
    file_in_sub = FileMetaData(
        user_id="dir_user",
        filename="file_in_sub.txt",
        filehash="sub_file_hash",
        key="sub/file_key",
        content_type="text/plain",
        size=50,
        parent_id=sub_dir.uid,
    )
    await file_in_sub.save()

    # Step 4: List files in root directory
    root_files = await FileMetaData.list_items(
        user_id="dir_user", parent_id=root_dir.uid
    )
    assert len(root_files) == 1
    assert root_files[0].uid == sub_dir.uid

    # Step 5: List files in subdirectory
    sub_files = await FileMetaData.list_items(user_id="dir_user", parent_id=sub_dir.uid)
    assert len(sub_files) == 1
    assert sub_files[0].uid == file_in_sub.uid

    # Step 6: Test path resolution
    parent_id, filename = await FileMetaData.get_path(
        filepath="root/sub/file.txt", user_id="dir_user", create=True
    )
    assert parent_id == sub_dir.uid
    assert filename == "file.txt"


@pytest.mark.asyncio
async def test_permission_workflow() -> None:
    """Test permission workflow."""
    # Step 1: Create file
    file = FileMetaData(
        user_id="owner_user",
        filename="permission_test.txt",
        filehash="perm_hash",
        key="perm/key",
        content_type="text/plain",
        size=100,
    )
    await file.save()

    # Step 2: Test owner permissions
    owner_permission = file.user_permission("owner_user")
    assert owner_permission.permission == PermissionEnum.OWNER
    assert owner_permission.read is True
    assert owner_permission.write is True
    assert owner_permission.delete is True

    # Step 3: Test other user permissions (should be NONE by default)
    other_permission = file.user_permission("other_user")
    assert other_permission.permission == PermissionEnum.NONE
    assert other_permission.read is False
    assert other_permission.write is False
    assert other_permission.delete is False

    # Step 4: Set permission for other user
    from apps.files.schemas import Permission

    new_permission = Permission(user_id="other_user", permission=PermissionEnum.READ)
    await file.set_permission("other_user", new_permission)

    # Step 5: Verify permission was set
    updated_permission = file.user_permission("other_user")
    assert updated_permission.permission == PermissionEnum.READ
    assert updated_permission.read is True
    assert updated_permission.write is False

    # Step 6: Test public permission
    file.public_permission.permission = PermissionEnum.READ
    await file.save()

    public_permission = file.user_permission(None)
    assert public_permission.permission == PermissionEnum.READ


@pytest.mark.asyncio
async def test_deletion_workflow() -> None:
    """Test file deletion workflow."""
    # Step 1: Create file
    file = FileMetaData(
        user_id="delete_user",
        filename="delete_test.txt",
        filehash="delete_hash",
        key="delete/key",
        content_type="text/plain",
        size=100,
    )
    await file.save()

    # Step 2: Soft delete
    await file.soft_delete("delete_user")
    assert file.is_deleted is True
    assert file.deleted_at is not None

    # Step 3: Verify file is not accessible normally
    files = await FileMetaData.list_items(user_id="delete_user", is_deleted=False)
    assert len([f for f in files if f.uid == file.uid]) == 0

    # Step 4: Verify file is accessible when including deleted
    files = await FileMetaData.list_items(user_id="delete_user", is_deleted=True)
    assert len([f for f in files if f.uid == file.uid]) == 1

    # Step 5: Restore file
    await file.restore("delete_user")
    assert file.is_deleted is False
    assert file.deleted_at is None

    # Step 6: Hard delete
    await file.soft_delete("delete_user")
    await file.hard_delete("delete_user")

    # Step 7: Verify file is completely deleted
    retrieved = await FileMetaData.get_item(file.uid, user_id="delete_user")
    assert retrieved is None


@pytest.mark.asyncio
async def test_volume_calculation() -> None:
    """Test volume calculation."""
    # Step 1: Create files for different users
    user1_file1 = FileMetaData(
        user_id="volume_user1",
        filename="file1.txt",
        key="vol/file1",
        content_type="text/plain",
        size=100,
    )
    await user1_file1.save()

    user1_file2 = FileMetaData(
        user_id="volume_user1",
        filename="file2.txt",
        key="vol/file2",
        content_type="text/plain",
        size=200,
    )
    await user1_file2.save()

    user2_file = FileMetaData(
        user_id="volume_user2",
        filename="file3.txt",
        key="vol/file3",
        content_type="text/plain",
        size=300,
    )
    await user2_file.save()

    # Step 2: Calculate volumes
    user1_volume = await FileMetaData.get_volume("volume_user1")
    user2_volume = await FileMetaData.get_volume("volume_user2")

    assert user1_volume["active_size"] == 300  # 100 + 200
    assert user2_volume["active_size"] == 300  # 300


@pytest.mark.asyncio
async def test_duplicate_file_handling(
    file_manager: FileManager, upload_file: UploadFile
) -> None:
    """Test handling of duplicate files."""
    # Step 1: Upload first file
    file1 = await file_manager.process_file(
        file=upload_file, user_id="dup_user", parent_id=None, blocking=True
    )

    # Step 2: Upload same file again
    upload_file.file.seek(0)  # Reset file pointer
    file2 = await file_manager.process_file(
        file=upload_file, user_id="dup_user", parent_id=None, blocking=True
    )

    # Step 3: Verify same file is returned (deduplication)
    assert file2.uid == file1.uid
    assert file2.filehash == file1.filehash

    # Step 4: Upload same file to different parent
    upload_file.file.seek(0)
    file3 = await file_manager.process_file(
        file=upload_file,
        user_id="dup_user",
        parent_id="different_parent",
        blocking=True,
    )

    # Should still return the same key but not same filemetadata due to same hash
    assert file3.uid != file1.uid
    assert file3.key == file1.key


@pytest.mark.asyncio
async def test_base64_upload_workflow(files_router: FilesRouter) -> None:
    """Test base64 upload workflow."""
    # Step 1: Prepare base64 content
    content = b"Base64 test content"
    base64_content = base64.b64encode(content).decode()
    mime_type = "text/plain"

    # Step 2: Mock the upload process
    with patch.object(files_router, "get_user") as mock_get_user:
        mock_get_user.return_value = type(
            "User",
            (),
            {
                "uid": "base64_user",
                "email": "test@example.com",
                "username": "base64_user",
                "is_active": True,
                "is_verified": True,
                "tenant_id": "test_tenant",
            },
        )()

        with patch.object(files_router, "upload_file") as mock_upload:
            mock_upload.return_value = FileMetaDataSchema(
                user_id="base64_user",
                filename="base64_test.txt",
                filehash="base64_hash",
                key="base64/key",
                content_type="text/plain",
                size=len(content),
            )

            result = await files_router.upload_file_base64(
                request=AsyncMock(),
                user_id="base64_user",
                file=base64_content,
                mime_type=mime_type,
            )

            assert result.filename == "base64_test.txt"
            assert result.content_type == "text/plain"
            assert result.size == len(content)


class _DummyRequest:
    """Minimal stand-in for a Request with just what upload_file needs."""

    def __init__(
        self,
        headers: dict[str, str] | None = None,
        form_data: dict[str, str] | None = None,
    ) -> None:
        self.headers = headers or {}
        self._form_data = form_data or {}

    async def form(self) -> dict[str, str]:
        return self._form_data


@dataclass
class _DummyUser:
    uid: str
    workspace_id: str | None = None
    user_id: str = ""
    scopes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.user_id:
            self.user_id = self.uid


def test_resolve_upload_identity_service_request_honors_override() -> None:
    """
    A service (API-key) caller -- e.g. mirza-bot uploading for a specific
    Telegram user -- can attribute the upload to that user's own
    workspace instead of the shared API key's own identity.
    """
    request = _DummyRequest(headers={"x-api-key": "uak-test"})
    user = _DummyUser(uid="media-service-key", workspace_id="service-key-ws")

    user_id, workspace_id = resolve_upload_identity(
        request, user, "telegram-user-1", "telegram-user-1-ws"
    )

    assert user_id == "telegram-user-1"
    assert workspace_id == "telegram-user-1-ws"


def test_resolve_upload_identity_service_request_falls_back_without_override() -> None:
    """A service request with no explicit override keeps the key's own id."""
    request = _DummyRequest(headers={"x-api-key": "uak-test"})
    user = _DummyUser(uid="media-service-key", workspace_id="service-key-ws")

    user_id, workspace_id = resolve_upload_identity(request, user, None, None)

    assert user_id == "media-service-key"
    assert workspace_id == "service-key-ws"


@pytest.mark.asyncio
async def test_upload_file_blocking_as_form_field_does_not_collide(
    files_router: FilesRouter, sample_file_content: bytes
) -> None:
    """
    blocking sent as multipart form data (not a query param) must not
    collide with the explicit blocking= kwarg passed to process_file.
    """
    with patch.object(files_router, "get_user") as mock_get_user:
        mock_get_user.return_value = _DummyUser(uid="form-blocking-user")

        upload = UploadFile(file=BytesIO(sample_file_content), filename="form.txt")
        result = await files_router.upload_file(
            request=_DummyRequest(form_data={"blocking": "1"}),
            file=upload,
            user_id=None,
            workspace_id=None,
            parent_id=None,
            filename=None,
        )

    assert result.user_id == "form-blocking-user"


def test_resolve_upload_identity_jwt_user_cannot_override() -> None:
    """A JWT end-user can never claim a different identity via the body."""
    request = _DummyRequest()
    user = _DummyUser(uid="real-user", workspace_id="real-ws")

    user_id, workspace_id = resolve_upload_identity(
        request, user, "attacker-uid", "attacker-ws"
    )

    assert user_id == "real-user"
    assert workspace_id == "real-ws"


@pytest.mark.asyncio
async def test_error_handling(file_manager: FileManager) -> None:
    """Test error handling scenarios."""
    # Test 1: Invalid filename ending with slash
    with pytest.raises(BaseHTTPException) as exc_info:
        await file_manager._get_filepath(user_id="error_user", filename="invalid/path/")
    assert exc_info.value.status_code == 400
    assert exc_info.value.error_code == "invalid_filename"

    # Test 2: Upload failure
    file_bytes = BytesIO(b"test content")
    with patch.object(file_manager.storage_backend, "upload_file") as mock_upload:
        mock_upload.return_value = False

        with pytest.raises(BaseHTTPException) as exc_info:
            await file_manager._manage_upload_to_storage(
                file_bytes=file_bytes,
                file_key="test/key",
                filehash="test_hash",
                content_type="text/plain",
                size=12,
            )
        assert exc_info.value.status_code == 500
        assert exc_info.value.error_code == "upload_failed"

    # Test 3: Permission denied
    file = FileMetaData(
        user_id="owner_user",
        filename="perm_test.txt",
        filehash="perm_hash",
        key="perm/key",
        content_type="text/plain",
        size=100,
    )
    await file.save()

    with pytest.raises(PermissionDenied):  # PermissionDenied
        await file.soft_delete("other_user")


@pytest.mark.asyncio
async def test_file_streaming_with_range(
    file_manager: FileManager, upload_file: UploadFile
) -> None:
    """Test file streaming with range requests."""
    # Step 1: Upload file
    file_metadata = await file_manager.process_file(
        file=upload_file, user_id="stream_user", blocking=True
    )
    await file_metadata.save()

    # Step 2: Test streaming with range
    stream_chunks = [
        chunk async for chunk in file_manager.stream_file(file_metadata, start=0, end=9)
    ]

    # Verify partial content
    content = b"".join(stream_chunks)
    assert len(content) <= 10  # Should be 10 bytes or less
    assert content.startswith(b"Hello, Wor")


@pytest.mark.asyncio
async def test_cleanup_orphaned_files() -> None:
    """Test cleanup of orphaned files."""
    # Step 1: Create file without ObjectMetaData
    orphan_file = FileMetaData(
        user_id="cleanup_user",
        filename="orphan.txt",
        key="orphan/key",
        content_type="text/plain",
        size=100,
    )
    await orphan_file.save()

    # Step 2: Create file with ObjectMetaData
    valid_file = FileMetaData(
        user_id="cleanup_user",
        filename="valid.txt",
        key="valid/key",
        content_type="text/plain",
        size=100,
    )
    await valid_file.save()

    obj = ObjectMetaData(
        key="valid/key",
        size=100,
        object_hash="valid_hash",
        content_type="text/plain",
    )
    await obj.save()

    # Step 3: Run cleanup
    await FileMetaData.remove_no_key_files()

    # Step 4: Verify orphaned file is deleted
    retrieved_orphan = await FileMetaData.get_item(
        orphan_file.uid, user_id="cleanup_user"
    )
    assert retrieved_orphan is None

    # Step 5: Verify valid file still exists
    retrieved_valid = await FileMetaData.get_item(
        valid_file.uid, user_id="cleanup_user"
    )
    assert retrieved_valid is not None

    await obj.delete()


@pytest.mark.asyncio
async def test_service_caller_can_manage_file_uploaded_on_behalf_of_other_user(
    files_router: FilesRouter,
) -> None:
    """
    Regression: live bug found sending mirza-bot a voice message.

    A service (API-key) caller uploads on behalf of a Telegram user (the
    file is correctly attributed to that user, not the shared key). The
    same service key must still be able to set public_permission on that
    file right after -- that's exactly what MediaClient.upload() does as
    its second call. Before the fix, the ordinary per-file permission
    check (key doesn't own the file) rejected this with 403.
    """
    service_request = _DummyRequest(headers={"x-api-key": "uak-test"})
    service_key_identity = _DummyUser(uid="media-service-key")

    with (
        patch.object(
            files_router, "get_user", AsyncMock(return_value=service_key_identity)
        ),
        # get_file's own root_permission read-check is independent of the
        # write-side fix under test here; a real, properly-scoped service
        # key already passes it in production.
        patch.object(files_router, "authorize", AsyncMock(return_value=True)),
    ):
        uploaded = await files_router.upload_file(
            request=service_request,
            file=UploadFile(file=BytesIO(b"voice note bytes"), filename="voice.ogg"),
            user_id="telegram-user-1",
            workspace_id=None,
            parent_id=None,
            filename=None,
            blocking=True,
        )
        assert uploaded.user_id == "telegram-user-1"

        updated = await files_router.update_item(
            request=service_request,
            uid=uploaded.uid,
            update=FileMetaDataUpdate(
                public_permission=PermissionSchema(permission=PermissionEnum.READ)
            ),
        )

    assert updated.public_permission.permission == PermissionEnum.READ


@pytest.mark.asyncio
async def test_jwt_user_still_cannot_manage_someone_elses_file(
    files_router: FilesRouter,
) -> None:
    """The service-request bypass must not weaken normal JWT ownership checks."""
    owner = FileMetaData(
        user_id="file-owner",
        filename="owned.txt",
        filehash="owned-hash",
        key="owned/key",
        content_type="text/plain",
        size=10,
    )
    await owner.save()

    jwt_request = _DummyRequest()
    other_user = _DummyUser(uid="someone-else")

    async def authorize_by_action(*, action: str, **_kwargs: object) -> bool:  # noqa: RUF029
        # get_file's internal read-check must pass so we actually reach
        # update_item's manage/update check -- the one under test here.
        return action == "read"

    with (
        patch.object(files_router, "get_user", AsyncMock(return_value=other_user)),
        patch.object(files_router, "authorize", side_effect=authorize_by_action),
        pytest.raises(PermissionDenied),
    ):
        await files_router.update_item(
            request=jwt_request,
            uid=owner.uid,
            update=FileMetaDataUpdate(
                public_permission=PermissionSchema(permission=PermissionEnum.READ)
            ),
        )
