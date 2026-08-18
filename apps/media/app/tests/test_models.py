"""Tests for FileMetaData and ObjectMetaData models."""

import pytest
import pytest_asyncio
from usso.exceptions import PermissionDenied

from apps.files.models import FileMetaData, ObjectMetaData
from apps.files.schemas import Permission, PermissionEnum, PermissionSchema


class TestObjectMetaData:
    """Test cases for ObjectMetaData model."""

    @pytest.mark.asyncio
    async def test_object_metadata_creation(self) -> None:
        """Test ObjectMetaData creation."""
        obj = ObjectMetaData(
            key="test/key",
            size=100,
            object_hash="test_hash",
            content_type="text/plain",
            url="http://example.com/test.txt",
        )
        await obj.save()

        assert obj.key == "test/key"
        assert obj.size == 100
        assert obj.object_hash == "test_hash"
        assert obj.content_type == "text/plain"
        assert obj.url == "http://example.com/test.txt"
        assert obj.access_at is not None

    @pytest.mark.asyncio
    async def test_object_metadata_get_key(self) -> None:
        """Test get_key class method."""
        obj = ObjectMetaData(
            key="test/get_key",
            size=100,
            object_hash="test_hash",
            content_type="text/plain",
        )
        await obj.save()

        retrieved = await ObjectMetaData.get_key("test/get_key")
        assert retrieved is not None
        assert retrieved.uid == obj.uid

        # Test non-existent key
        not_found = await ObjectMetaData.get_key("non/existent")
        assert not_found is None

    @pytest.mark.asyncio
    async def test_object_metadata_delete_with_other_files(self) -> None:
        """Test delete when other files reference the same key."""
        # Create object
        obj = ObjectMetaData(
            key="test/shared_key",
            size=100,
            object_hash="test_hash",
            content_type="text/plain",
        )
        await obj.save()

        # Create file that references this object
        file1 = FileMetaData(
            user_id="user1",
            filename="file1.txt",
            key="test/shared_key",
            content_type="text/plain",
            size=100,
        )
        await file1.save()

        # Delete object - should not actually delete due to other files
        await obj.delete()

        # Object should still exist
        retrieved = await ObjectMetaData.get_key("test/shared_key")
        assert retrieved is None

    @pytest.mark.asyncio
    async def test_object_metadata_delete_key(self) -> None:
        """Test delete_key class method."""
        obj = ObjectMetaData(
            key="test/delete_key",
            size=100,
            object_hash="test_hash",
            content_type="text/plain",
        )
        await obj.save()

        await ObjectMetaData.delete_key("test/delete_key")

        # Object should be deleted
        retrieved = await ObjectMetaData.get_key("test/delete_key")
        assert retrieved is None


class TestFileMetaData:
    """Test cases for FileMetaData model."""

    @pytest.fixture
    def sample_file(self) -> FileMetaData:
        """Create a sample file for testing."""
        return FileMetaData(
            user_id="test_user",
            filename="test.txt",
            filehash="test_hash",
            key="test/key",
            content_type="text/plain",
            size=100,
            parent_id=None,
        )

    @pytest_asyncio.fixture
    async def sample_directory(self) -> FileMetaData:
        """Create a sample directory for testing."""
        return await FileMetaData.create_directory(
            user_id="test_user",
            dirname="test_dir",
            parent_id=None,
        )

    @pytest.mark.asyncio
    async def test_file_metadata_creation(self, sample_file: FileMetaData) -> None:
        """Test FileMetaData creation."""
        await sample_file.save()

        assert sample_file.user_id == "test_user"
        assert sample_file.filename == "test.txt"
        assert sample_file.filehash == "test_hash"
        assert sample_file.key == "test/key"
        assert sample_file.content_type == "text/plain"
        assert sample_file.size == 100
        assert sample_file.is_directory is False

    @pytest.mark.asyncio
    async def test_directory_creation(self, sample_directory: FileMetaData) -> None:
        """Test directory creation."""
        await sample_directory.save()

        assert sample_directory.is_directory is True
        assert sample_directory.filename == "test_dir"

    @pytest.mark.asyncio
    async def test_get_queryset_basic(self) -> None:
        """Test get_queryset with basic parameters."""
        query = FileMetaData.get_queryset(user_id="test_user", is_deleted=False)

        assert query["$or"][0]["user_id"] == "test_user"
        assert query["is_deleted"] is False

    @pytest.mark.asyncio
    async def test_get_queryset_workspace_id_included(self) -> None:
        """Passing workspace_id adds it as an extra visibility scope."""
        query = FileMetaData.get_queryset(user_id="test_user", workspace_id="ws-1")

        assert {"workspace_id": "ws-1"} in query["$or"]

    @pytest.mark.asyncio
    async def test_workspace_member_sees_other_members_file(
        self, sample_file: FileMetaData
    ) -> None:
        """A workspace member can list/get a file uploaded by someone else."""
        sample_file.workspace_id = "ws-1"
        await sample_file.save()

        files, total = await FileMetaData.list_total_combined(
            user_id="other_user", workspace_id="ws-1", offset=0, limit=10
        )
        assert total >= 1
        assert any(f.uid == sample_file.uid for f in files)

        retrieved = await FileMetaData.get_item(
            uid=sample_file.uid, user_id="other_user", workspace_id="ws-1"
        )
        assert retrieved is not None
        assert retrieved.uid == sample_file.uid

    @pytest.mark.asyncio
    async def test_non_member_cannot_see_workspace_file(
        self, sample_file: FileMetaData
    ) -> None:
        """A user outside the workspace, with no workspace_id, sees nothing."""
        sample_file.workspace_id = "ws-1"
        await sample_file.save()

        retrieved = await FileMetaData.get_item(
            uid=sample_file.uid, user_id="other_user"
        )
        assert retrieved is None

    @pytest.mark.asyncio
    async def test_list_items_basic(self, sample_file: FileMetaData) -> None:
        """Test list_items with basic parameters."""
        await sample_file.save()

        files, total = await FileMetaData.list_total_combined(
            user_id="test_user", offset=0, limit=10
        )

        assert len(files) >= 1
        assert total >= 1
        assert files[0].user_id == "test_user"

    @pytest.mark.asyncio
    async def test_list_items_with_filters(self, sample_file: FileMetaData) -> None:
        """Test list_items with various filters."""
        await sample_file.save()

        # Test with filename filter
        files = await FileMetaData.list_items(user_id="test_user", filename="test.txt")
        assert len(files) >= 1
        assert files[0].filename == "test.txt"

        # Test with content_type filter
        files = await FileMetaData.list_items(
            user_id="test_user", content_type="text/plain"
        )
        assert len(files) >= 1
        assert files[0].content_type == "text/plain"

        # Test with is_directory filter
        files = await FileMetaData.list_items(user_id="test_user", is_directory=False)
        assert len(files) >= 1
        assert files[0].is_directory is False

    @pytest.mark.asyncio
    async def test_get_item(self, sample_file: FileMetaData) -> None:
        """Test get_item method."""
        await sample_file.save()

        retrieved = await FileMetaData.get_item(
            uid=sample_file.uid, user_id="test_user"
        )

        assert retrieved is not None
        assert retrieved.uid == sample_file.uid
        assert retrieved.filename == "test.txt"

    @pytest.mark.asyncio
    async def test_create_directory(self) -> None:
        """Test create_directory class method."""
        directory = await FileMetaData.create_directory(
            user_id="test_user", dirname="new_dir", parent_id=None
        )

        assert directory.is_directory is True
        assert directory.filename == "new_dir"
        assert directory.user_id == "test_user"

    @pytest.mark.asyncio
    async def test_get_path_simple(self) -> None:
        """Test get_path with simple path."""
        parent_id, filename = await FileMetaData.get_path(
            filepath="simple.txt", user_id="test_user", create=True
        )

        assert parent_id is None
        assert filename == "simple.txt"

    @pytest.mark.asyncio
    async def test_get_path_nested(self) -> None:
        """Test get_path with nested path."""
        # Create parent directory first
        await FileMetaData.create_directory(
            user_id="test_user", dirname="parent", parent_id=None
        )

        parent_id, filename = await FileMetaData.get_path(
            filepath="parent/child/file.txt", user_id="test_user", create=True
        )

        assert parent_id is not None
        assert filename == "file.txt"

    @pytest.mark.asyncio
    async def test_get_path_directory(self) -> None:
        """Test get_path with directory path ending in slash."""
        parent_id, filename = await FileMetaData.get_path(
            filepath="test/dir/", user_id="test_user", create=True
        )

        assert parent_id is not None
        assert filename is None

    @pytest.mark.asyncio
    async def test_get_path_not_found(self) -> None:
        """Test get_path when path doesn't exist and create=False."""
        with pytest.raises(FileNotFoundError):
            await FileMetaData.get_path(
                filepath="non/existent/path.txt", user_id="test_user", create=False
            )

    @pytest.mark.asyncio
    async def test_user_permission_owner(self, sample_file: FileMetaData) -> None:
        """Test user_permission for file owner."""
        await sample_file.save()

        permission = sample_file.user_permission("test_user")
        assert permission.permission == PermissionEnum.OWNER

    @pytest.mark.asyncio
    async def test_user_permission_public(self, sample_file: FileMetaData) -> None:
        """Test user_permission for public access."""
        sample_file.public_permission = PermissionSchema(permission=PermissionEnum.READ)
        await sample_file.save()

        permission = sample_file.user_permission(None)
        assert permission.permission == PermissionEnum.READ

    @pytest.mark.asyncio
    async def test_user_permission_other_user(self, sample_file: FileMetaData) -> None:
        """Test user_permission for other user."""
        await sample_file.save()

        permission = sample_file.user_permission("other_user")
        assert permission.permission == PermissionEnum.NONE

    @pytest.mark.asyncio
    async def test_set_permission_success(self, sample_file: FileMetaData) -> None:
        """Test set_permission success."""
        await sample_file.save()

        new_permission = Permission(
            user_id="other_user", permission=PermissionEnum.READ
        )

        await sample_file.set_permission("other_user", new_permission)

        # Check if permission was added
        permission = sample_file.user_permission("other_user")
        assert permission.permission == PermissionEnum.READ

    @pytest.mark.asyncio
    async def test_set_permission_update_existing(
        self, sample_file: FileMetaData
    ) -> None:
        """Test set_permission updating existing permission."""
        await sample_file.save()

        # Add initial permission
        initial_permission = Permission(
            user_id="other_user", permission=PermissionEnum.READ
        )
        await sample_file.set_permission("other_user", initial_permission)

        # Update permission
        updated_permission = Permission(
            user_id="other_user", permission=PermissionEnum.WRITE
        )
        await sample_file.set_permission("other_user", updated_permission)

        # Check if permission was updated
        permission = sample_file.user_permission("other_user")
        assert permission.permission == PermissionEnum.WRITE

    @pytest.mark.asyncio
    async def test_set_permission_owner_denied(self, sample_file: FileMetaData) -> None:
        """Test set_permission denied for owner."""
        await sample_file.save()

        permission = Permission(
            user_id="test_user",  # Owner
            permission=PermissionEnum.READ,
        )

        with pytest.raises(PermissionDenied):
            await sample_file.set_permission("test_user", permission)

    @pytest.mark.asyncio
    async def test_set_permission_user_mismatch(
        self, sample_file: FileMetaData
    ) -> None:
        """Test set_permission with user ID mismatch."""
        await sample_file.save()

        permission = Permission(user_id="other_user", permission=PermissionEnum.READ)

        with pytest.raises(PermissionDenied):
            await sample_file.set_permission("different_user", permission)

    @pytest.mark.asyncio
    async def test_exists_key(self) -> None:
        """Test exists_key class method."""
        file = FileMetaData(
            user_id="test_user",
            filename="test.txt",
            key="test/exists_key",
            content_type="text/plain",
            size=100,
        )
        await file.save()

        assert await FileMetaData.exists_key("test/exists_key") is True
        assert await FileMetaData.exists_key("non/existent") is False

    @pytest.mark.asyncio
    async def test_soft_delete_file(self, sample_file: FileMetaData) -> None:
        """Test soft_delete for a file."""
        await sample_file.save()

        await sample_file.soft_delete("test_user")

        assert sample_file.is_deleted is True
        assert sample_file.deleted_at is not None

    @pytest.mark.asyncio
    async def test_soft_delete_directory(self, sample_directory: FileMetaData) -> None:
        """Test soft_delete for a directory with children."""
        await sample_directory.save()

        # Create child file
        child_file: FileMetaData = await FileMetaData(
            user_id="test_user",
            filename="child.txt",
            key="test/child_key",
            content_type="text/plain",
            size=50,
            parent_id=sample_directory.uid,
        ).save()

        await sample_directory.soft_delete("test_user")

        # Check parent is deleted
        assert sample_directory.is_deleted is True

        # Check child is also deleted
        file_after = await FileMetaData.get_item(
            child_file.uid, user_id="test_user", is_deleted=True
        )
        assert file_after.is_deleted is True

    @pytest.mark.asyncio
    async def test_soft_delete_permission_denied(
        self, sample_file: FileMetaData
    ) -> None:
        """Test soft_delete with insufficient permissions."""
        await sample_file.save()

        with pytest.raises(PermissionDenied):
            await sample_file.soft_delete("other_user")

    @pytest.mark.asyncio
    async def test_hard_delete_file(self, sample_file: FileMetaData) -> None:
        """Test hard_delete for a file."""
        await sample_file.save()
        await sample_file.soft_delete("test_user")

        await sample_file.hard_delete("test_user")

        # File should be deleted
        retrieved = await FileMetaData.get_item(sample_file.uid, user_id="test_user")
        assert retrieved is None

    @pytest.mark.asyncio
    async def test_hard_delete_not_soft_deleted(
        self, sample_file: FileMetaData
    ) -> None:
        """Test hard_delete on file that hasn't been soft deleted."""
        await sample_file.save()

        with pytest.raises(ValueError, match="File must be soft deleted first"):
            await sample_file.hard_delete("test_user")

    @pytest.mark.asyncio
    async def test_restore_file(self, sample_file: FileMetaData) -> None:
        """Test restore for a file."""
        await sample_file.save()
        await sample_file.soft_delete("test_user")

        await sample_file.restore("test_user")

        assert sample_file.is_deleted is False
        assert sample_file.deleted_at is None

    @pytest.mark.asyncio
    async def test_restore_permission_denied(self, sample_file: FileMetaData) -> None:
        """Test restore with insufficient permissions."""
        await sample_file.save()
        await sample_file.soft_delete("test_user")

        with pytest.raises(PermissionDenied):
            await sample_file.restore("other_user")

    @pytest.mark.asyncio
    async def test_delete_legacy(self, sample_file: FileMetaData) -> None:
        """Test legacy delete method."""
        await sample_file.save()

        await sample_file.delete("test_user")

        # File should be soft deleted
        assert sample_file.is_deleted is True

        # Second delete should hard delete
        await sample_file.delete("test_user")

        # File should be completely deleted
        retrieved = await FileMetaData.get_item(sample_file.uid, user_id="test_user")
        assert retrieved is None

    @pytest.mark.asyncio
    async def test_get_volume(self) -> None:
        """Test get_volume class method."""
        # Create files for test user
        file1 = FileMetaData(
            user_id="volume_user",
            filename="file1.txt",
            key="test/file1",
            content_type="text/plain",
            size=100,
        )
        await file1.save()

        file2 = FileMetaData(
            user_id="volume_user",
            filename="file2.txt",
            key="test/file2",
            content_type="text/plain",
            size=200,
        )
        await file2.save()

        # Create file for different user
        other_file = FileMetaData(
            user_id="other_user",
            filename="other.txt",
            key="test/other",
            content_type="text/plain",
            size=300,
        )
        await other_file.save()

        volume = await FileMetaData.get_volume("volume_user")
        assert volume["active_size"] == 300

        # Test with no files
        volume = await FileMetaData.get_volume("no_files_user")
        assert volume["active_size"] == 0

    @pytest.mark.asyncio
    async def test_remove_no_key_files(self) -> None:
        """Test remove_no_key_files class method."""
        # Create file with key that has no ObjectMetaData
        orphan_file = FileMetaData(
            user_id="test_user",
            filename="orphan.txt",
            key="orphan/key",
            content_type="text/plain",
            size=100,
        )
        await orphan_file.save()

        # Create file with key that has ObjectMetaData
        valid_file = FileMetaData(
            user_id="test_user",
            filename="valid.txt",
            key="valid/key",
            content_type="text/plain",
            size=100,
        )
        await valid_file.save()

        # Create ObjectMetaData for valid file
        obj = ObjectMetaData(
            key="valid/key",
            size=100,
            object_hash="valid_hash",
            content_type="text/plain",
        )
        await obj.save()

        await FileMetaData.remove_no_key_files()

        # Orphan file should be deleted
        retrieved = await FileMetaData.get_item(orphan_file.uid, user_id="test_user")
        assert retrieved is None

        # Valid file should still exist
        retrieved = await FileMetaData.get_item(valid_file.uid, user_id="test_user")
        assert retrieved is not None

        await obj.delete()
