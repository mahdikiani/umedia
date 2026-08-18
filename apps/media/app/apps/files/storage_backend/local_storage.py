"""Local file storage backend implementation."""

import logging
import shutil
from collections.abc import AsyncGenerator
from io import BytesIO
from pathlib import Path

import aiofiles
from fastapi_mongo_base.core.exceptions import BaseHTTPException

from server.config import Settings

from .base_storage import StorageBackend


class LocalStorageBackend(StorageBackend):
    """Local file storage backend implementation."""

    supported_backend = "local"

    def __init__(
        self, config: dict[str, object] | None = None, **kwargs: object
    ) -> None:
        """
        Initialize local storage backend.

        Args:
            config: Local storage configuration containing:
                - base_path: Base directory for file storage
                - base_url: Base URL for public file access (optional)
                - create_dirs: Whether to create directories if they don't exist
        """
        self.config = config or {
            "base_path": Settings.LOCAL_STORAGE_PATH,
            "base_url": Settings.LOCAL_STORAGE_BASE_URL,
            "create_dirs": True,
        }
        self.base_path: Path = Path(self.config["base_path"])
        self.base_url: str | None = self.config.get("base_url")
        self.create_dirs: bool = self.config.get("create_dirs", True)

        # Create base directory if it doesn't exist
        if self.create_dirs:
            self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_file_path(self, key: str) -> Path:
        """Get full file path from key."""
        # Sanitize key to prevent directory traversal
        safe_key = key.replace("..", "").lstrip("/")
        return self.base_path / safe_key

    def _ensure_directory_exists(self, file_path: Path) -> None:
        """Ensure parent directory exists."""
        if self.create_dirs:
            file_path.parent.mkdir(parents=True, exist_ok=True)

    def _get_mime_type(self, file_path: Path) -> str:
        """Get MIME type of file."""
        import magic

        mime_detector = magic.Magic(mime=True)
        return mime_detector.from_file(file_path)

    def _get_file_size(self, file_path: Path) -> int:
        """Get size of file."""
        return file_path.stat().st_size

    async def upload_file(
        self,
        file_bytes: BytesIO,
        key: str,
        *,
        content_type: str | None = None,
        metadata: dict[str, object] | None = None,
        **kwargs: object,
    ) -> dict[str, object]:
        """Upload file to local storage."""
        file_path = self._get_file_path(key)
        self._ensure_directory_exists(file_path)

        file_bytes.seek(0)
        file_size = len(file_bytes.getvalue())

        # Write file asynchronously
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(file_bytes.getvalue())

        return {
            "key": key,
            "path": str(file_path),
            "size": file_size,
            "url": self.get_public_url(key) if self.base_url else None,
        }

    async def download_file(self, key: str, **kwargs: object) -> BytesIO:
        """Download file from local storage."""
        file_path = self._get_file_path(key)

        if not file_path.exists():
            raise BaseHTTPException(
                status_code=404,
                error="file_not_found",
                detail="File not found",
            )

        # check if size is bigger than 100mb raise use stream_file
        if file_path.stat().st_size > 100 * 1024 * 1024:
            raise BaseHTTPException(
                status_code=413,
                error="file_too_large",
                detail="File too large to download directly. Please use streaming.",
            )

        async with aiofiles.open(file_path, "rb") as f:
            content = await f.read()
            file_bytes = BytesIO(content)
            file_bytes.seek(0)
            return file_bytes

    async def stream_file(
        self,
        key: str,
        *,
        start: int | None = None,
        end: int | None = None,
        **kwargs: object,
    ) -> AsyncGenerator[bytes]:
        """Stream file from local storage."""
        file_path = self._get_file_path(key)

        if not file_path.exists():
            raise BaseHTTPException(
                status_code=404,
                error="file_not_found",
                detail="File not found",
            )

        start = start or 0
        end = end or file_path.stat().st_size
        chunk_size: int = kwargs.get("chunk_size", 8192)

        async with aiofiles.open(file_path, "rb") as f:
            if start is not None:
                await f.seek(start)

            bytes_read = 0
            max_bytes = end - start + 1

            while True:
                read_size = chunk_size
                remaining = max_bytes - bytes_read
                if remaining <= 0:
                    break
                read_size = min(chunk_size, remaining)

                chunk = await f.read(read_size)
                if not chunk:
                    break

                bytes_read += len(chunk)
                yield chunk

    async def delete_file(self, key: str, **kwargs: object) -> bool:
        """Delete file from local storage."""
        file_path = self._get_file_path(key)
        metadata_path = file_path.with_suffix(file_path.suffix + ".meta")

        try:
            if file_path.exists():
                file_path.unlink()
            if metadata_path.exists():
                metadata_path.unlink()
        except Exception:
            logging.exception("Error deleting file")
            return False

        return True

    async def file_exists(self, key: str, **kwargs: object) -> bool:
        """Check if file exists in local storage."""
        file_path = self._get_file_path(key)
        return file_path.exists()

    async def get_file_info(self, key: str, **kwargs: object) -> dict[str, object]:
        """Get file information from local storage."""
        file_path = self._get_file_path(key)

        if not file_path.exists():
            raise BaseHTTPException(
                status_code=404,
                error="file_not_found",
                detail="File not found",
            )

        stat = file_path.stat()

        # Load metadata if exists
        content_type = self._get_mime_type(file_path)

        return {
            "key": key,
            "size": stat.st_size,
            "content_type": content_type,
            "last_modified": stat.st_mtime,
            "created": stat.st_ctime,
            "path": str(file_path),
        }

    async def copy_file(
        self, source_key: str, destination_key: str, **kwargs: object
    ) -> bool:
        """Copy file within local storage."""
        source_path = self._get_file_path(source_key)
        destination_path = self._get_file_path(destination_key)

        if not source_path.exists():
            return False

        try:
            self._ensure_directory_exists(destination_path)
            shutil.copy2(source_path, destination_path)

        except Exception:
            logging.exception("Error copying file")
            return False
        return True

    async def move_file(
        self, source_key: str, destination_key: str, **kwargs: object
    ) -> bool:
        """Move file within local storage."""
        source_path = self._get_file_path(source_key)
        destination_path = self._get_file_path(destination_key)

        if not source_path.exists():
            return False

        try:
            self._ensure_directory_exists(destination_path)
            shutil.move(source_path, destination_path)

        except Exception:
            logging.exception("Error moving file")
            return False
        return True

    def get_public_url(self, key: str, **kwargs: object) -> str | None:
        """Get public URL for local file."""
        if self.base_url:
            # Ensure key doesn't start with /
            safe_key = key.lstrip("/")
            return f"{self.base_url.rstrip('/')}/{safe_key}"
        return None

    async def list_files(
        self, prefix: str = "", offset: int = 0, limit: int = 1000, **kwargs: object
    ) -> list[dict[str, object]]:
        """List files in local storage."""
        if prefix:
            # Handle prefix as a glob pattern
            safe_prefix = prefix.replace("..", "").lstrip("/")
            self.base_path / safe_prefix

        files = []
        count = 0

        try:
            # Use glob to find files
            pattern = "**/*" if not prefix else f"{prefix}*"
            for file_path in self.base_path.glob(pattern):
                if offset > 0:
                    offset -= 1
                    continue

                if count >= limit:
                    break

                if file_path.is_file():
                    relative_path = file_path.relative_to(self.base_path)
                    stat = file_path.stat()

                    files.append({
                        "key": str(relative_path),
                        "size": stat.st_size,
                        "last_modified": stat.st_mtime,
                        "created": stat.st_ctime,
                        "path": str(file_path),
                        "url": self.get_public_url(str(relative_path)),
                    })
                    count += 1

        except Exception:
            logging.exception("Error listing files")
            return []
        return files

    async def cleanup(self) -> None:
        """Cleanup local storage resources."""
        # No cleanup needed for local storage
        for file_path in self.base_path.glob("**/*"):
            if file_path.is_file():
                file_path.unlink()
            elif file_path.is_dir():
                shutil.rmtree(file_path)
