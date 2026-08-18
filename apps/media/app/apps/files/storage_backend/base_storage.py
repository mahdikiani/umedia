"""Abstract base class for storage backends."""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from io import BytesIO

from fastapi_mongo_base.utils import basic
from singleton import AbstractSingleton


class StorageBackend(ABC, metaclass=AbstractSingleton):
    """Abstract base class for all storage backends."""

    @property
    @abstractmethod
    def supported_backend(self) -> str:
        """Get all storage backends."""
        raise NotImplementedError("Supported backend is not implemented")

    @abstractmethod
    def __init__(
        self, config: dict[str, object] | None = None, **kwargs: object
    ) -> None:
        """
        Initialize storage backend with configuration.

        Args:
            config: Backend-specific configuration parameters
        """

    @classmethod
    def create_storage_backend(
        cls,
        backend_type: str = "local",
        config: dict[str, object] | None = None,
        **kwargs: object,
    ) -> "StorageBackend":
        """Create storage backend instance."""
        for backend in basic.get_all_subclasses(cls):
            if backend.supported_backend == backend_type:
                return backend(config, **kwargs)
        raise ValueError(f"Unsupported backend type: {backend_type}")

    @abstractmethod
    async def upload_file(
        self,
        file_bytes: BytesIO,
        key: str,
        *,
        content_type: str | None = None,
        metadata: dict[str, object] | None = None,
        **kwargs: object,
    ) -> dict[str, object]:
        """
        Upload file to storage backend.

        Args:
            file_bytes: File content as BytesIO
            key: Unique identifier for the file
            content_type: MIME type of the file
            metadata: Additional metadata for the file
            **kwargs: Backend-specific parameters

        Returns:
            dict: Upload result with backend-specific information
        """
        pass

    @abstractmethod
    async def download_file(self, key: str, **kwargs: object) -> BytesIO:
        """
        Download file from storage backend.

        Args:
            key: Unique identifier for the file
            **kwargs: Backend-specific parameters

        Returns:
            BytesIO: File content
        """
        pass

    @abstractmethod
    async def stream_file(
        self,
        key: str,
        *,
        start: int | None = None,
        end: int | None = None,
        **kwargs: object,
    ) -> AsyncGenerator[bytes]:
        """
        Stream file from storage backend.

        Args:
            key: Unique identifier for the file
            **kwargs: Backend-specific parameters (e.g., byte ranges)

        Yields:
            bytes: File content chunks
        """
        pass

    @abstractmethod
    async def delete_file(self, key: str, **kwargs: object) -> bool:
        """
        Delete file from storage backend.

        Args:
            key: Unique identifier for the file
            **kwargs: Backend-specific parameters

        Returns:
            bool: True if deletion was successful
        """
        pass

    @abstractmethod
    async def file_exists(self, key: str, **kwargs: object) -> bool:
        """
        Check if file exists in storage backend.

        Args:
            key: Unique identifier for the file
            **kwargs: Backend-specific parameters

        Returns:
            bool: True if file exists
        """
        pass

    @abstractmethod
    async def get_file_info(self, key: str, **kwargs: object) -> dict[str, object]:
        """
        Get file information from storage backend.

        Args:
            key: Unique identifier for the file
            **kwargs: Backend-specific parameters

        Returns:
            dict: File information (size, content_type, etc.)
        """
        pass

    async def generate_presigned_url(
        self, key: str, expires_in: int = 3600, **kwargs: object
    ) -> str | None:
        """
        Generate presigned URL for file access (if supported).

        Args:
            key: Unique identifier for the file
            expires_in: URL expiration time in seconds
            **kwargs: Backend-specific parameters

        Returns:
            str | None: Presigned URL or None if not supported
        """
        raise NotImplementedError("Presigned URL generation is not supported")
