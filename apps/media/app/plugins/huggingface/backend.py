"""Hugging Face Buckets provider plugin backend.

Buckets are Hugging Face's S3-like, Xet-backed object store
(`hf://buckets/<namespace>/<name>`): mutable, no git history, and
content-addressed, so server-side copies are free (by Xet hash). That makes
them the right Hub target for media storage -- a model/dataset *repo*
would turn every upload, rename, and delete into a git commit.

`huggingface_hub` is synchronous, so every call runs in a worker thread.
Uploads go through `batch_bucket_files`, which uses `hf_xet` for chunked,
deduplicated transfer. Downloads stream the `resolve` URL directly with
httpx (Range passes through), instead of `download_bucket_files`, which
can only write to a local path.

Folders are implicit (object-key prefixes), like S3. An empty folder is
kept alive by a hidden `FOLDER_MARKER` object, never listed.
"""

import asyncio
import os
import re
import tempfile
from collections.abc import AsyncIterator, Callable, Iterable
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

import httpx
from huggingface_hub import BucketFile, BucketFolder, HfApi
from huggingface_hub.errors import HfHubHTTPError

from plugins.contracts import (
    ConnectionFailedError,
    CreateResourceIn,
    PluginBackendError,
    Resource,
    ResourceNotFoundError,
    UpdateResourceIn,
)
from plugins.sdk import PluginBackend

FOLDER_MARKER = ".umedia-folder"
# Not empty: a zero-byte Xet upload is an edge case not worth depending on.
_FOLDER_MARKER_BODY = b"umedia folder marker\n"
READ_CHUNK_SIZE = 64 * 1024
READ_TIMEOUT_SECONDS = 60.0
# Buffer uploads in memory up to this size, then spill to a temp file:
# hf_xet needs a complete source (bytes or a path), and the plugin must
# not hold a multi-GB video in RAM.
UPLOAD_SPOOL_BYTES = 8 * 1024 * 1024
_BUCKET_ID_RE = re.compile(r"^[\w.-]+/[\w.-]+$")

ApiFactory = Callable[..., HfApi]


def _bucket_id(config: dict[str, Any]) -> str:
    raw = str(config.get("bucket") or "").strip().strip("/")
    raw = raw.removeprefix("hf://").removeprefix("buckets/")
    if not raw:
        raise ConnectionFailedError("bucket is required")
    if not _BUCKET_ID_RE.match(raw):
        raise ConnectionFailedError("bucket must look like namespace/name")
    return raw


def _token(config: dict[str, Any]) -> str:
    token = str(config.get("token") or "").strip()
    if not token:
        # Never fall back to an ambient HF_TOKEN / cached login: every
        # connection must use the credential its owner supplied.
        raise ConnectionFailedError("token is required")
    return token


def _check_name(name: str) -> str:
    if not name or name in {".", ".."} or "/" in name or name == FOLDER_MARKER:
        raise ConnectionFailedError(f"invalid name: {name!r}")
    return name


def _check_id(resource_id: str) -> str:
    parts = resource_id.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ResourceNotFoundError(resource_id)
    return resource_id


def _join(parent_id: str | None, name: str) -> str:
    return f"{_check_id(parent_id)}/{name}" if parent_id else name


def _parent(path: str) -> str | None:
    parent = str(PurePosixPath(path).parent)
    return None if parent == "." else parent


def _is_under(path: str, prefix: str) -> bool:
    """Path-component match. The server's `prefix` filter is lexical, so
    listing `logs` also returns `logs_old/...`."""
    return path.startswith(f"{prefix}/")


def _is_hidden(path: str) -> bool:
    return PurePosixPath(path).name == FOLDER_MARKER


class HuggingFaceBackend(PluginBackend):
    """Resources inside one Hugging Face bucket per connection."""

    def __init__(
        self,
        *,
        api_factory: ApiFactory = HfApi,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # Injectable so tests run the real code against an in-memory bucket.
        self._api_factory = api_factory
        self._transport = transport

    def _api(self, config: dict[str, Any]) -> HfApi:
        return self._api_factory(token=_token(config), library_name="umedia")

    @staticmethod
    async def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        """Run a blocking huggingface_hub call off the event loop and map
        its HTTP errors onto the plugin contract."""
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except HfHubHTTPError as error:
            status = error.response.status_code if error.response is not None else None
            if status == 404:
                raise ResourceNotFoundError(str(error)) from error
            if status in {401, 403}:
                raise ConnectionFailedError(str(error)) from error
            raise PluginBackendError(str(error)) from error

    # ------------------------------------------------------------------
    # Bucket helpers
    # ------------------------------------------------------------------
    async def _tree(
        self,
        config: dict[str, Any],
        prefix: str | None,
        *,
        recursive: bool,
    ) -> list[BucketFile | BucketFolder]:
        api = self._api(config)
        entries: Iterable[BucketFile | BucketFolder] = await self._call(
            lambda: list(
                api.list_bucket_tree(_bucket_id(config), prefix, recursive=recursive),
            ),
        )
        if prefix is None:
            return list(entries)
        return [entry for entry in entries if _is_under(entry.path, prefix)]

    async def _files_under(
        self, config: dict[str, Any], prefix: str
    ) -> list[BucketFile]:
        """Every object below `prefix`, markers included."""
        return [
            entry
            for entry in await self._tree(config, prefix, recursive=True)
            if isinstance(entry, BucketFile)
        ]

    async def _file_info(self, config: dict[str, Any], path: str) -> BucketFile | None:
        api = self._api(config)
        found: list[BucketFile] = await self._call(
            lambda: list(api.get_bucket_paths_info(_bucket_id(config), [path])),
        )
        return found[0] if found else None

    async def _batch(self, config: dict[str, Any], **operations: Any) -> None:  # noqa: ANN401
        api = self._api(config)
        await self._call(api.batch_bucket_files, _bucket_id(config), **operations)

    async def _upload_stream(
        self,
        config: dict[str, Any],
        path: str,
        content: AsyncIterator[bytes],
    ) -> None:
        buffer = bytearray()
        handle = None
        try:
            async for chunk in content:
                if handle is None and len(buffer) + len(chunk) <= UPLOAD_SPOOL_BYTES:
                    buffer.extend(chunk)
                    continue
                if handle is None:
                    handle = tempfile.NamedTemporaryFile(  # noqa: SIM115
                        prefix="umedia-hf-",
                        delete=False,
                    )
                    handle.write(buffer)
                    buffer.clear()
                await asyncio.to_thread(handle.write, chunk)
            if handle is None:
                await self._batch(config, add=[(bytes(buffer), path)])
                return
            handle.close()
            await self._batch(config, add=[(handle.name, path)])
        finally:
            if handle is not None:
                handle.close()
                os.unlink(handle.name)

    def _to_resource(self, entry: BucketFile | BucketFolder) -> Resource:
        is_file = isinstance(entry, BucketFile)
        metadata: dict[str, Any] = {}
        if is_file:
            metadata["xet_hash"] = entry.xet_hash
            if entry.mtime:
                metadata["mtime"] = entry.mtime.timestamp()
        return Resource(
            id=entry.path,
            type="file" if is_file else "folder",
            name=PurePosixPath(entry.path).name,
            parent_id=_parent(entry.path),
            size=entry.size if is_file else None,
            metadata=metadata,
        )

    def _folder(self, path: str) -> Resource:
        return self._to_resource(BucketFolder(type="directory", path=path))

    # ------------------------------------------------------------------
    # Contract
    # ------------------------------------------------------------------
    async def connect(self, config: dict[str, Any]) -> None:
        bucket_id = _bucket_id(config)
        api = self._api(config)
        try:
            await self._call(api.bucket_info, bucket_id)
        except PluginBackendError as error:
            raise ConnectionFailedError(
                f"cannot open bucket {bucket_id}: {error}"
            ) from error

    async def list_resources(
        self,
        config: dict[str, Any],
        *,
        parent_id: str | None,
    ) -> list[Resource]:
        if parent_id:
            _check_id(parent_id)
        entries = await self._tree(config, parent_id or None, recursive=False)
        return [
            self._to_resource(entry)
            for entry in entries
            if _parent(entry.path) == (parent_id or None) and not _is_hidden(entry.path)
        ]

    async def get_resource(self, config: dict[str, Any], resource_id: str) -> Resource:
        _check_id(resource_id)
        info = await self._file_info(config, resource_id)
        if info is not None and not _is_hidden(info.path):
            return self._to_resource(info)
        if await self._tree(config, resource_id, recursive=False):
            return self._folder(resource_id)
        raise ResourceNotFoundError(resource_id)

    async def read_content(
        self,
        config: dict[str, Any],
        resource_id: str,
        *,
        range_header: str | None,
    ) -> AsyncIterator[bytes]:
        _check_id(resource_id)
        api = self._api(config)
        url = (
            f"{api.endpoint}/buckets/{_bucket_id(config)}/resolve/{quote(resource_id)}"
        )
        headers = {"Authorization": f"Bearer {_token(config)}"}
        if range_header:
            headers["Range"] = range_header
        # httpx drops Authorization on cross-origin redirects, so the
        # token does not leak to the Xet/CDN host `resolve` redirects to.
        async with (
            httpx.AsyncClient(
                transport=self._transport,
                follow_redirects=True,
                timeout=READ_TIMEOUT_SECONDS,
            ) as client,
            client.stream("GET", url, headers=headers) as response,
        ):
            if response.status_code == 404:
                raise ResourceNotFoundError(resource_id)
            if response.status_code >= 400:
                raise PluginBackendError(
                    f"download failed with HTTP {response.status_code}",
                )
            async for chunk in response.aiter_bytes(READ_CHUNK_SIZE):
                yield chunk

    async def create_resource(
        self,
        config: dict[str, Any],
        metadata: CreateResourceIn,
        *,
        content: AsyncIterator[bytes],
    ) -> Resource:
        path = _join(metadata.parent_id, _check_name(metadata.name))
        if metadata.type == "folder":
            await self._batch(
                config,
                add=[(_FOLDER_MARKER_BODY, f"{path}/{FOLDER_MARKER}")],
            )
            return self._folder(path)
        await self._upload_stream(config, path, content)
        return await self.get_resource(config, path)

    async def update_resource(
        self,
        config: dict[str, Any],
        resource_id: str,
        changes: UpdateResourceIn,
        *,
        content: AsyncIterator[bytes] | None,
    ) -> Resource:
        current = await self.get_resource(config, resource_id)
        new_parent = (
            changes.parent_id if changes.parent_id is not None else current.parent_id
        )
        new_name = _check_name(changes.name) if changes.name else current.name
        new_path = _join(new_parent or None, new_name)

        if new_path != resource_id:
            if _is_under(new_path, resource_id):
                raise ConnectionFailedError("cannot move a folder inside itself")
            await self._move(config, current, new_path)

        if content is not None and current.type == "file":
            await self._upload_stream(config, new_path, content)

        return await self.get_resource(config, new_path)

    async def _move(
        self, config: dict[str, Any], current: Resource, new_path: str
    ) -> None:
        """Server-side copy by Xet hash, then delete the originals.

        Two batches, not one: `batch_bucket_files` is non-transactional, so
        copying first means a failure leaves a duplicate, never data loss.
        """
        if current.type == "file":
            moves = [(current.id, current.metadata["xet_hash"], new_path)]
        else:
            moves = [
                (f.path, f.xet_hash, new_path + f.path[len(current.id) :])
                for f in await self._files_under(config, current.id)
            ]
        bucket_id = _bucket_id(config)
        await self._batch(
            config,
            copy=[("bucket", bucket_id, xet_hash, dst) for _, xet_hash, dst in moves],
        )
        await self._batch(config, delete=[src for src, _, _ in moves])

    async def delete_resource(self, config: dict[str, Any], resource_id: str) -> None:
        resource = await self.get_resource(config, resource_id)  # 404s if missing
        if resource.type == "file":
            paths = [resource_id]
        else:
            paths = [f.path for f in await self._files_under(config, resource_id)]
        await self._batch(config, delete=paths)
