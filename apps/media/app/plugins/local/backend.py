"""Local filesystem provider plugin backend.

Real filesystem I/O (aiofiles, matching `apps/media`'s original
`local_storage.py`) plus the root-path containment safety check from
`apps/api`'s original `providers/local.py` -- a connection's `root_path`
must resolve inside this plugin process's allowed storage volume, and
every resource path must resolve inside *that* connection's own root, so
neither an admin typo nor a `..` in a filename can escape either boundary.
"""

import asyncio
import mimetypes
import os
import re
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import aiofiles

from plugins.contracts import (
    ConnectionFailedError,
    CreateResourceIn,
    Resource,
    ResourceNotFoundError,
    UpdateResourceIn,
)
from plugins.sdk import PluginBackend

DEFAULT_ALLOWED_ROOT = "/storage"
DEFAULT_CONTENT_TYPE = "application/octet-stream"
DIRECTORY_CONTENT_TYPE = "inode/directory"
READ_CHUNK_SIZE = 64 * 1024
_RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)")


def _parse_range(range_header: str | None, size: int) -> tuple[int, int]:
    """Return an inclusive (start, end) byte range for `size` bytes."""
    if not range_header:
        return 0, size - 1
    match = _RANGE_RE.match(range_header)
    if not match:
        return 0, size - 1
    start_str, end_str = match.groups()
    start = int(start_str)
    end = int(end_str) if end_str else size - 1
    return start, min(end, size - 1)


def _get_content_type(path: Path) -> str:
    """MIME for a local path -- same idea as legacy local_storage.

    Prefer libmagic on file bytes; when magic is missing, fails, or only
    returns the generic `application/octet-stream`, fall back to the
    filename extension so tiny/ambiguous files (e.g. a one-byte `.txt`)
    still get a useful type. Folders are `inode/directory`.
    """
    if path.is_dir():
        return DIRECTORY_CONTENT_TYPE

    fallback = mimetypes.guess_type(path.name)[0] or DEFAULT_CONTENT_TYPE
    if path.suffix.lower() in {".md", ".markdown"}:
        fallback = "text/plain"
    try:
        import magic

        detected = magic.Magic(mime=True).from_file(str(path))
    except Exception:
        return fallback
    if not detected or detected == DEFAULT_CONTENT_TYPE:
        return fallback
    if detected.startswith("text/html") and path.suffix.lower() in {
        ".md", ".markdown", ".txt",
    }:
        return fallback
    return detected


class LocalBackend(PluginBackend):
    """Filesystem-backed resources under one allowed storage root."""

    def __init__(self, *, allowed_root: Path | None = None) -> None:
        self._allowed_root = (
            allowed_root or Path(
                os.environ.get("UMEDIA_LOCAL_STORAGE_ROOT", DEFAULT_ALLOWED_ROOT),
            )
        ).resolve()

    def _connection_root(self, config: dict[str, Any]) -> Path:
        root_path = config.get("root_path")
        if not root_path:
            raise ConnectionFailedError("root_path is required")
        root = Path(str(root_path)).resolve()
        if not root.is_relative_to(self._allowed_root):
            raise ConnectionFailedError(
                f"root_path must be inside {self._allowed_root}",
            )
        return root

    def _resolve(self, config: dict[str, Any], resource_id: str | None) -> Path:
        """`resource_id` is a POSIX path relative to the connection root
        (empty/None means the root itself)."""
        root = self._connection_root(config)
        if not resource_id:
            return root
        candidate = (root / resource_id).resolve()
        if not candidate.is_relative_to(root):
            raise ResourceNotFoundError(resource_id)
        return candidate

    def _to_resource(self, root: Path, path: Path) -> Resource:
        is_root = path == root
        relative = "" if is_root else path.relative_to(root).as_posix()
        stat = path.stat()
        parent = None if is_root or path.parent == root else (
            path.parent.relative_to(root).as_posix()
        )
        return Resource(
            id=relative,
            type="folder" if path.is_dir() else "file",
            name=root.name if is_root else path.name,
            parent_id=parent,
            size=None if path.is_dir() else stat.st_size,
            content_type=_get_content_type(path),
            metadata={"mtime": stat.st_mtime},
        )

    async def connect(self, config: dict[str, Any]) -> None:
        """Validate containment, then read/write access."""
        root = self._connection_root(config)
        await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
        if not root.is_dir():
            raise ConnectionFailedError("root_path is not a directory")
        probe = root / ".umedia-connection-test"
        try:
            probe.write_bytes(b"umedia")
        except OSError as error:
            raise ConnectionFailedError(
                f"root_path is not writable: {error}",
            ) from error
        finally:
            probe.unlink(missing_ok=True)

    async def list_resources(
        self,
        config: dict[str, Any],
        *,
        parent_id: str | None,
    ) -> list[Resource]:
        directory = self._resolve(config, parent_id)
        if not directory.is_dir():
            raise ResourceNotFoundError(parent_id or "")
        root = self._connection_root(config)
        entries = sorted(directory.iterdir(), key=lambda p: p.name)
        return [self._to_resource(root, entry) for entry in entries]

    async def get_resource(self, config: dict[str, Any], resource_id: str) -> Resource:
        path = self._resolve(config, resource_id)
        if not path.exists():
            raise ResourceNotFoundError(resource_id)
        return self._to_resource(self._connection_root(config), path)

    async def read_content(
        self,
        config: dict[str, Any],
        resource_id: str,
        *,
        range_header: str | None,
    ) -> AsyncIterator[bytes]:
        path = self._resolve(config, resource_id)
        if not path.is_file():
            raise ResourceNotFoundError(resource_id)
        start, end = _parse_range(range_header, path.stat().st_size)
        remaining = end - start + 1
        async with aiofiles.open(path, "rb") as handle:
            await handle.seek(start)
            while remaining > 0:
                chunk = await handle.read(min(READ_CHUNK_SIZE, remaining))
                if not chunk:
                    return
                remaining -= len(chunk)
                yield chunk

    async def create_resource(
        self,
        config: dict[str, Any],
        metadata: CreateResourceIn,
        *,
        content: AsyncIterator[bytes],
    ) -> Resource:
        root = self._connection_root(config)
        parent = self._resolve(config, metadata.parent_id)
        target = (parent / metadata.name).resolve()
        if not target.is_relative_to(root):
            raise ConnectionFailedError("name escapes the connection root")

        if metadata.type == "folder":
            await asyncio.to_thread(target.mkdir, parents=True, exist_ok=True)
        else:
            await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
            async with aiofiles.open(target, "wb") as handle:
                async for chunk in content:
                    await handle.write(chunk)
        return self._to_resource(root, target)

    async def update_resource(
        self,
        config: dict[str, Any],
        resource_id: str,
        changes: UpdateResourceIn,
        *,
        content: AsyncIterator[bytes] | None,
    ) -> Resource:
        root = self._connection_root(config)
        path = self._resolve(config, resource_id)
        if not path.exists():
            raise ResourceNotFoundError(resource_id)

        if changes.name is not None or changes.parent_id is not None:
            new_parent = (
                self._resolve(config, changes.parent_id)
                if changes.parent_id is not None
                else path.parent
            )
            new_path = (new_parent / (changes.name or path.name)).resolve()
            if not new_path.is_relative_to(root):
                raise ConnectionFailedError("target escapes the connection root")
            await asyncio.to_thread(new_path.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.move, str(path), str(new_path))
            path = new_path

        if content is not None:
            async with aiofiles.open(path, "wb") as handle:
                async for chunk in content:
                    await handle.write(chunk)

        return self._to_resource(root, path)

    async def delete_resource(self, config: dict[str, Any], resource_id: str) -> None:
        path = self._resolve(config, resource_id)
        if not path.exists():
            raise ResourceNotFoundError(resource_id)
        if path.is_dir():
            await asyncio.to_thread(shutil.rmtree, path)
        else:
            await asyncio.to_thread(path.unlink)
