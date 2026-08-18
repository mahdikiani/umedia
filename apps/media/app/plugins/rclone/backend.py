"""Generic rclone-backed provider plugin.

One plugin process serves several remote types (s3, google_drive, and
mechanically the rest of rclone's backends -- see docs/03-provider-system.md).
Runs `rclone rcd` as a child subprocess for the whole plugin process's
lifetime and talks to its JSON `rc` API for metadata operations
(list/stat/mkdir/delete/move/copy/publiclink); `rclone cat`/`rclone rcat`
subprocesses handle streamed content, since `rc` isn't a natural fit for
that. Connection config is translated into an rclone *inline connection
string* remote (`:type,param=value,...:path`), built fresh per call and
never written to rclone's on-disk config file -- credentials never touch
disk here.
"""

import asyncio
import contextlib
import logging
import socket
from collections.abc import AsyncIterator
from typing import Any

import httpx

from plugins.contracts import (
    ConnectionFailedError,
    CreateResourceIn,
    PluginBackendError,
    Resource,
    ResourceNotFoundError,
    StatusOut,
    UpdateResourceIn,
)
from plugins.sdk import PluginBackend

logger = logging.getLogger(__name__)

RCD_STARTUP_TIMEOUT_SECONDS = 15.0
RCD_STOP_TIMEOUT_SECONDS = 5.0
RC_CALL_TIMEOUT_SECONDS = 30.0
READ_CHUNK_SIZE = 64 * 1024


def _free_local_port() -> int:
    """Pick an unused loopback TCP port for this process's `rclone rcd`."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _quote(value: str) -> str:
    """rclone inline connection-string quoting.

    A value containing a delimiter (`:`, `,`) or a quote itself must be
    wrapped in single quotes, with internal single quotes doubled --
    verified empirically against a real `rclone` binary (an S3 endpoint
    URL like `http://host:port` breaks unquoted, since `:` is also the
    remote-spec delimiter).
    """
    if any(ch in value for ch in ":,'"):
        return "'" + value.replace("'", "''") + "'"
    return value


def _params(pairs: dict[str, str | None]) -> str:
    return ",".join(f"{key}={_quote(value)}" for key, value in pairs.items() if value)


def _s3_fs(config: dict[str, Any]) -> str:
    if not config.get("access_key_id") or not config.get("secret_access_key"):
        raise ConnectionFailedError("access_key_id and secret_access_key are required")
    if not config.get("bucket"):
        raise ConnectionFailedError("bucket is required")
    params: dict[str, str | None] = {
        "provider": config.get("provider", "Other"),
        "access_key_id": config["access_key_id"],
        "secret_access_key": config["secret_access_key"],
        "region": config.get("region"),
    }
    if config.get("endpoint_url"):
        params["endpoint"] = config["endpoint_url"]
        params["force_path_style"] = "true"
    return f":s3,{_params(params)}:{config['bucket']}"


def _google_drive_fs(config: dict[str, Any]) -> str:
    if not config.get("token"):
        raise ConnectionFailedError("token is required")
    params: dict[str, str | None] = {
        "token": config["token"],
        "root_folder_id": config.get("root_folder_id"),
    }
    return f":drive,{_params(params)}:"


def _local_debug_fs(config: dict[str, Any]) -> str:
    """rclone's own `local` backend -- not a cataloged provider (UMedia
    already has a native `local` plugin, Phase 3.1). Kept as a permanent,
    credential-free `remote_type` purely so the generic rc/cat/rcat
    machinery has a fully reliable end-to-end regression test
    (`test_plugin_rclone_contract.py`) that doesn't depend on real S3/
    Google Drive credentials or a mock server's own quirks. No manifest
    references it, so it's unreachable through `apps/provider_connections`.
    """
    if not config.get("path"):
        raise ConnectionFailedError("path is required")
    return f":local:{config['path']}"


# One builder per supported remote_type. Adding a WebDAV/FTP/SFTP/Nextcloud/
# OneDrive/Dropbox entry here (docs/09-tasks.md backlog) is a config-mapping
# function, not new plumbing -- everything else in this file is generic.
_FS_BUILDERS = {
    "s3": _s3_fs,
    "google_drive": _google_drive_fs,
    "rclone_local_debug": _local_debug_fs,
}


def _build_fs(config: dict[str, Any]) -> str:
    remote_type = config.get("remote_type")
    builder = _FS_BUILDERS.get(str(remote_type))
    if builder is None:
        raise ConnectionFailedError(f"Unsupported rclone remote_type: {remote_type!r}")
    return builder(config)


class RcloneBackend(PluginBackend):
    """Resources backed by any rclone remote type in `_FS_BUILDERS`."""

    def __init__(self) -> None:
        self._port = _free_local_port()
        self._rc_base_url = f"http://127.0.0.1:{self._port}"
        self._rcd_process: asyncio.subprocess.Process | None = None
        self._drain_tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------
    # Process lifecycle
    # ------------------------------------------------------------------
    async def startup(self) -> None:
        self._rcd_process = await asyncio.create_subprocess_exec(
            "rclone",
            "rcd",
            f"--rc-addr=127.0.0.1:{self._port}",
            "--rc-no-auth",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._drain_tasks = [
            asyncio.create_task(self._drain(self._rcd_process.stdout, "stdout")),
            asyncio.create_task(self._drain(self._rcd_process.stderr, "stderr")),
        ]
        async with asyncio.timeout(RCD_STARTUP_TIMEOUT_SECONDS):
            await self._wait_until_rcd_ready()

    async def shutdown(self) -> None:
        if self._rcd_process is not None and self._rcd_process.returncode is None:
            self._rcd_process.terminate()
            try:
                await asyncio.wait_for(
                    self._rcd_process.wait(), timeout=RCD_STOP_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                self._rcd_process.kill()
                await self._rcd_process.wait()
        for task in self._drain_tasks:
            task.cancel()
        for task in self._drain_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @staticmethod
    async def _drain(stream: asyncio.StreamReader | None, name: str) -> None:
        if stream is None:
            return
        with contextlib.suppress(asyncio.CancelledError):
            while True:
                line = await stream.readline()
                if not line:
                    return
                text = line.decode(errors="replace").rstrip()
                logger.info("[rclone rcd:%s] %s", name, text)

    async def _wait_until_rcd_ready(self) -> None:
        async with httpx.AsyncClient(timeout=2.0) as client:
            while True:
                with contextlib.suppress(httpx.HTTPError):
                    response = await client.post(f"{self._rc_base_url}/core/pid")
                    if response.status_code == 200:
                        return
                await asyncio.sleep(0.05)

    # ------------------------------------------------------------------
    # rc API + cat/rcat helpers
    # ------------------------------------------------------------------
    async def _rc_call(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=RC_CALL_TIMEOUT_SECONDS) as client:
            try:
                response = await client.post(f"{self._rc_base_url}{path}", json=payload)
            except httpx.HTTPError as error:
                raise PluginBackendError(f"rclone rc call failed: {error}") from error
        data: dict[str, Any] = response.json() if response.content else {}
        if response.status_code >= 400:
            raise PluginBackendError(data.get("error", f"rc {path} failed"))
        return data

    async def _run_subprocess(
        self, *args: str, stdin_bytes: bytes | None = None,
    ) -> bytes:
        process = await asyncio.create_subprocess_exec(
            "rclone",
            *args,
            stdin=asyncio.subprocess.PIPE if stdin_bytes is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate(stdin_bytes)
        if process.returncode != 0:
            raise PluginBackendError(
                f"rclone {args[0]} failed: {stderr.decode(errors='replace')}",
            )
        return stdout

    @staticmethod
    def _target(fs: str, remote: str) -> str:
        """`fs` + `remote` joined into one rclone remote argument, for
        `rclone cat`/`rcat` -- these read a single positional
        `remote:path`, unlike the rc API's separate `fs`/`remote` fields."""
        return f"{fs}{remote}" if fs.endswith(":") else f"{fs}/{remote}"

    def _to_resource(self, item: dict[str, Any]) -> Resource:
        path = item["Path"]
        parent = "/".join(path.split("/")[:-1]) or None
        return Resource(
            id=path,
            type="folder" if item.get("IsDir") else "file",
            name=item["Name"],
            parent_id=parent,
            size=None if item.get("IsDir") else item.get("Size"),
            content_type=item.get("MimeType"),
        )

    # ------------------------------------------------------------------
    # Contract
    # ------------------------------------------------------------------
    async def connect(self, config: dict[str, Any]) -> None:
        fs = _build_fs(config)
        try:
            await self._rc_call("/operations/list", {"fs": fs, "remote": ""})
        except PluginBackendError as error:
            raise ConnectionFailedError(str(error)) from error

    async def status(self) -> StatusOut:
        if self._rcd_process is None or self._rcd_process.returncode is not None:
            return StatusOut(healthy=False, detail="rclone rcd is not running")
        return StatusOut(healthy=True)

    async def list_resources(
        self, config: dict[str, Any], *, parent_id: str | None,
    ) -> list[Resource]:
        fs = _build_fs(config)
        result = await self._rc_call(
            "/operations/list", {"fs": fs, "remote": parent_id or ""},
        )
        return [self._to_resource(item) for item in result.get("list", [])]

    async def get_resource(self, config: dict[str, Any], resource_id: str) -> Resource:
        fs = _build_fs(config)
        result = await self._rc_call(
            "/operations/stat", {"fs": fs, "remote": resource_id},
        )
        item = result.get("item")
        if item is None:
            raise ResourceNotFoundError(resource_id)
        return self._to_resource(item)

    async def read_content(
        self,
        config: dict[str, Any],
        resource_id: str,
        *,
        range_header: str | None,
    ) -> AsyncIterator[bytes]:
        fs = _build_fs(config)
        args = ["cat"]
        if range_header:
            start, count = _parse_range_for_rclone(range_header)
            if start is not None:
                args += ["--offset", str(start)]
            if count is not None:
                args += ["--count", str(count)]
        args.append(self._target(fs, resource_id))
        process = await asyncio.create_subprocess_exec(
            "rclone", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None  # noqa: S101 -- PIPE was requested above
        found_output = False
        while True:
            chunk = await process.stdout.read(READ_CHUNK_SIZE)
            if not chunk:
                break
            found_output = True
            yield chunk
        returncode = await process.wait()
        if returncode != 0 and not found_output:
            stderr = (await process.stderr.read()) if process.stderr else b""
            detail = stderr.decode(errors="replace")
            raise ResourceNotFoundError(f"{resource_id}: {detail}")

    async def create_resource(
        self,
        config: dict[str, Any],
        metadata: CreateResourceIn,
        *,
        content: AsyncIterator[bytes],
    ) -> Resource:
        fs = _build_fs(config)
        remote = (
            f"{metadata.parent_id}/{metadata.name}" if metadata.parent_id
            else metadata.name
        )
        if metadata.type == "folder":
            await self._rc_call("/operations/mkdir", {"fs": fs, "remote": remote})
        else:
            body = b"".join([chunk async for chunk in content])
            await self._run_subprocess(
                "rcat", self._target(fs, remote), stdin_bytes=body,
            )
        return await self.get_resource(config, remote)

    async def update_resource(
        self,
        config: dict[str, Any],
        resource_id: str,
        changes: UpdateResourceIn,
        *,
        content: AsyncIterator[bytes] | None,
    ) -> Resource:
        fs = _build_fs(config)
        current = await self.get_resource(config, resource_id)
        new_parent = (
            changes.parent_id if changes.parent_id is not None
            else current.parent_id
        )
        new_name = changes.name or current.name
        new_remote = f"{new_parent}/{new_name}" if new_parent else new_name

        if new_remote != resource_id:
            await self._rc_call(
                "/operations/movefile",
                {
                    "srcFs": fs, "srcRemote": resource_id,
                    "dstFs": fs, "dstRemote": new_remote,
                },
            )

        if content is not None:
            body = b"".join([chunk async for chunk in content])
            await self._run_subprocess(
                "rcat", self._target(fs, new_remote), stdin_bytes=body,
            )

        return await self.get_resource(config, new_remote)

    async def delete_resource(self, config: dict[str, Any], resource_id: str) -> None:
        fs = _build_fs(config)
        resource = await self.get_resource(config, resource_id)  # 404s if missing
        endpoint = (
            "/operations/purge" if resource.type == "folder"
            else "/operations/deletefile"
        )
        await self._rc_call(endpoint, {"fs": fs, "remote": resource_id})


def _parse_range_for_rclone(range_header: str) -> tuple[int | None, int | None]:
    """`Range: bytes=start-end` -> (`--offset`, `--count`) for `rclone cat`."""
    import re

    match = re.match(r"bytes=(\d+)-(\d*)", range_header)
    if not match:
        return None, None
    start_str, end_str = match.groups()
    start = int(start_str)
    count = int(end_str) - start + 1 if end_str else None
    return start, count
