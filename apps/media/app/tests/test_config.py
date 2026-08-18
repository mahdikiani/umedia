"""Test configuration and utilities."""

import os
from collections.abc import AsyncGenerator
from io import BytesIO

import pytest
from fastapi_mongo_base.models import BaseEntity
from fastapi_mongo_base.utils import basic


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment() -> None:
    """Set up test environment variables."""
    os.environ["STORAGE_BACKEND"] = "local"
    os.environ["PUBLIC_ACCESS_TYPE"] = "READ"
    os.environ["PROJECT_NAME"] = "test_project"
    os.environ["ROOT_URL"] = "test.example.com"
    os.environ["BASE_PATH"] = "/api/media/v1/"


@pytest.fixture(scope="session")
def test_models() -> list[type[BaseEntity]]:
    """Get all test models."""
    return basic.get_all_subclasses(BaseEntity)


@pytest.fixture(autouse=True)
async def cleanup_database() -> None:
    """Clean up database after each test."""
    yield
    # Clean up all collections after each test
    from apps.files.models import FileMetaData, ObjectMetaData

    # Delete all test data
    await FileMetaData.delete_all()
    await ObjectMetaData.delete_all()


class TestDataFactory:
    """Factory for creating test data."""

    @staticmethod
    def create_file_metadata(
        user_id: str = "test_user",
        filename: str = "test.txt",
        filehash: str = "test_hash",
        key: str = "test/key",
        content_type: str = "text/plain",
        size: int = 100,
        parent_id: str | None = None,
        is_directory: bool = False,
        **kwargs: object,
    ) -> dict[str, object]:
        """Create file metadata dictionary."""
        return {
            "user_id": user_id,
            "filename": filename,
            "filehash": filehash,
            "key": key,
            "content_type": content_type,
            "size": size,
            "parent_id": parent_id,
            "is_directory": is_directory,
            **kwargs,
        }

    @staticmethod
    def create_object_metadata(
        key: str = "test/key",
        size: int = 100,
        object_hash: str = "test_hash",
        content_type: str = "text/plain",
        url: str | None = None,
        **kwargs: object,
    ) -> dict[str, object]:
        """Create object metadata dictionary."""
        return {
            "key": key,
            "size": size,
            "object_hash": object_hash,
            "content_type": content_type,
            "url": url,
            **kwargs,
        }


@pytest.fixture
def test_data_factory() -> TestDataFactory:
    """Provide test data factory."""
    return TestDataFactory()


class MockStorageBackend:
    """Mock storage backend for testing."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.urls: dict[str, str] = {}

    async def upload_file(
        self,
        file_bytes: object,
        key: str,
        content_type: str = "application/octet-stream",
        **kwargs: object,
    ) -> dict[str, object]:
        """Mock upload file."""
        content = file_bytes.read() if hasattr(file_bytes, "read") else file_bytes
        self.files[key] = content
        self.urls[key] = f"http://example.com/{key}"
        return {"url": self.urls[key]}

    async def download_file(self, key: str) -> BytesIO:
        """Mock download file."""
        content = self.files.get(key, b"")
        return BytesIO(content)

    async def stream_file(self, key: str, **kwargs: object) -> AsyncGenerator[bytes]:
        """Mock stream file."""
        content = self.files.get(key, b"")

        async def stream() -> AsyncGenerator[bytes]:  # noqa: RUF029
            yield content

        return stream()

    async def delete_file(self, key: str) -> bool:
        """Mock delete file."""
        if key in self.files:
            del self.files[key]
            return True
        return False

    async def file_exists(self, key: str) -> bool:
        """Mock file exists."""
        return key in self.files

    async def generate_presigned_url(
        self, key: str, expires_in: int = 3600
    ) -> str | None:
        """Mock generate presigned URL."""
        return f"http://example.com/presigned/{key}?expires={expires_in}"

    def get_public_url(self, key: str) -> str | None:
        """Mock get public URL."""
        return self.urls.get(key)


@pytest.fixture
def mock_storage_backend() -> MockStorageBackend:
    """Provide mock storage backend."""
    return MockStorageBackend()
