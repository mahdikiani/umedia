"""NextCloud/OwnCloud storage backend implementation using WebDAV."""

import logging
from collections.abc import AsyncGenerator
from io import BytesIO
from typing import Any
from urllib.parse import quote, urljoin

from fastapi_mongo_base.core.exceptions import BaseHTTPException
from pydantic import BaseModel, field_validator

from server.config import Settings

from .base_storage import StorageBackend


class NextCloudConfig(BaseModel):
    base_url: str = Settings.NEXTCLOUD_BASE_URL
    username: str = Settings.NEXTCLOUD_USERNAME
    password: str = Settings.NEXTCLOUD_PASSWORD
    webdav_path: str | None = Settings.NEXTCLOUD_WEBDAV_PATH
    timeout: int = 30

    @field_validator("webdav_path")
    @classmethod
    def validate_webdav_path(cls, v: str) -> str:
        if not v.endswith("/"):
            return f"{v}/"
        return v


class NextCloudStorageBackend(StorageBackend):
    """NextCloud/OwnCloud storage backend using WebDAV API."""

    supported_backend = "nextcloud"

    def __init__(self, config: dict[str, Any] | None = None, **kwargs: object) -> None:
        """
        Initialize NextCloud storage backend.

        Args:
            config: NextCloud configuration containing:
                - base_url: NextCloud/OwnCloud base URL
                - username: Username for authentication
                - password: Password or app password
                - webdav_path: WebDAV path (default: /remote.php/webdav/)
                - timeout: Request timeout in seconds (default: 30)
        """
        import nextcloud_client

        self.config = NextCloudConfig.model_validate(config)
        self._nc: nextcloud_client.Client | None = None

    def _get_file_url(self, key: str) -> str:
        """Get full WebDAV URL for file."""
        safe_key = quote(key.lstrip("/"), safe="/")
        return urljoin(self.config.base_url, f"{self.config.webdav_path}/{safe_key}")

    def get_client(self) -> object:
        """Get NextCloud client."""
        import nextcloud_client

        if not self._nc:
            self._nc = nextcloud_client.Client(self.config.base_url)
            self._nc.login(self.config.username, self.config.password)
        return self._nc

    async def upload_file(
        self,
        file_bytes: BytesIO,
        key: str,
        content_type: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: object,
    ) -> dict[str, Any]:
        """Upload file to NextCloud."""
        file_url = self._get_file_url(key)
        file_bytes.seek(0)
        file_size = len(file_bytes.getvalue())

        try:
            # Create parent directories if they don't exist
            await self._ensure_directories_exist(key)
            self.get_client().put_file_contents(key, file_bytes.getvalue())
        except Exception as e:
            logging.exception("Failed to upload file")
            raise BaseHTTPException(
                status_code=500,
                error_code="upload_failed",
                detail="Failed to upload file",
            ) from e

        return {
            "key": key,
            "url": file_url,
            "size": file_size,
        }

    async def _ensure_directories_exist(self, key: str) -> None:
        """Ensure parent directories exist."""
        path_parts = key.split("/")[:-1]  # Remove filename
        current_path = ""

        nc = self.get_client()
        for part in path_parts:
            current_path = f"{current_path}/{part}" if current_path else part
            try:
                nc.file_info(current_path)
            except Exception:
                if not nc.mkdir(current_path):
                    return False
        return True

    async def download_file(self, key: str, **kwargs: object) -> BytesIO:
        """Download file from NextCloud."""
        try:
            nc = self.get_client()
            file_content = BytesIO(nc.get_file_contents(f"/{key}"))
            file_content.seek(0)
        except Exception as e:
            logging.exception("Failed to download file")
            raise BaseHTTPException(
                status_code=500,
                error="download_failed",
                detail="Failed to download file",
            ) from e

        head = file_content.read(12)
        if head == b"<html><head>":
            raise BaseHTTPException(
                status_code=404,
                error="file_not_found",
                detail="File not found",
            )
        file_content.seek(0)
        return file_content

    async def stream_file(
        self,
        key: str,
        *,
        start: int | None = None,
        end: int | None = None,
        **kwargs: object,
    ) -> AsyncGenerator[bytes]:
        """Stream file from NextCloud."""
        try:
            nc = self.get_client()
            file_content = BytesIO(nc.get_file_contents(key))

            start = start or 0
            chunk_size = kwargs.get("chunk_size", 8192)  # 8KB default chunk size
            end = end or file_content.getbuffer().nbytes

            file_content.seek(start)
            remaining = end - start + 1

            while True:
                chunk = file_content.read(min(chunk_size, remaining))
                if remaining > 0:
                    remaining -= len(chunk)

                if not chunk:
                    break

                yield chunk

        finally:
            file_content.close()

    async def delete_file(self, key: str, **kwargs: object) -> bool:
        """Delete file from NextCloud."""
        try:
            self.get_client().delete(key)
        except Exception as e:
            logging.exception("Failed to delete file")
            raise BaseHTTPException(
                status_code=500,
                error="delete_failed",
                detail="Failed to delete file",
            ) from e

        return True

    async def file_exists(self, key: str, **kwargs: object) -> bool:
        """Check if file exists in NextCloud."""
        try:
            self.get_client().file_info(key)
        except Exception:
            return False

        return True

    async def get_file_info(self, key: str, **kwargs: object) -> dict[str, Any]:
        """Get file information from NextCloud."""
        try:
            file_info = self.get_client().file_info(key)
        except Exception as e:
            logging.exception("Failed to get file info")
            raise BaseHTTPException(
                status_code=500,
                error="get_file_info_failed",
                detail="Failed to get file info",
            ) from e

        if not file_info:
            raise BaseHTTPException(
                status_code=404,
                error="file_not_found",
                detail="File not found",
            )

        return {
            "path": file_info.path,
            "file_type": file_info.file_type,
            "file_name": file_info.name,
            "attributes": file_info.attributes,
            # "size": file_info.size,
            # "mtime": file_info.mtime,
        }

    async def generate_presigned_url(
        self, key: str, expires_in: int = 3600, **kwargs: object
    ) -> str | None:
        link_info = self.get_client().share_file_with_link(key)
        return link_info.get_link()
