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
import base64
import contextlib
import logging
import os
import re
import socket
from collections.abc import AsyncIterator
from typing import Any

import httpx

from plugins.contracts import (
    ConnectionFailedError,
    CreateResourceIn,
    HostKeyMismatchError,
    HostKeyUnknownError,
    PluginBackendError,
    Resource,
    ResourceNotFoundError,
    StatusOut,
    UpdateResourceIn,
)
from plugins.sdk import PluginBackend

logger = logging.getLogger(__name__)

RCD_STARTUP_TIMEOUT_SECONDS = 15.0
HOST_KEY_PROBE_TIMEOUT_SECONDS = 10.0
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


def _s3_provider(config: dict[str, Any]) -> str:
    """Pick rclone's S3 provider quirks.

    Directory prefixes (keys with ``/``) only show up as ``IsDir`` folders
    when rclone uses a compatible provider profile. Empirically:

    - Cloudflare R2 → ``Cloudflare``
    - Custom / path-style endpoints (MinIO, Garage, RFS, …) → ``Minio``
    - Plain AWS (no endpoint) → ``AWS``

    ``Other`` is a last resort: on several S3-compatible hosts a root
    ``operations/list`` then returns only files and never CommonPrefixes,
    so import never walks nested keys.
    """
    explicit = (config.get("provider") or "").strip()
    if explicit:
        return explicit
    endpoint = str(config.get("endpoint_url") or "").lower()
    if "r2.cloudflarestorage.com" in endpoint:
        return "Cloudflare"
    if endpoint:
        return "Minio"
    return "AWS"


def _s3_fs(config: dict[str, Any]) -> str:
    if not config.get("access_key_id") or not config.get("secret_access_key"):
        raise ConnectionFailedError("access_key_id and secret_access_key are required")
    if not config.get("bucket"):
        raise ConnectionFailedError("bucket is required")
    provider = _s3_provider(config)
    region = (config.get("region") or "").strip() or None
    # Some UIs store a placeholder like "other-v2-signature"; treat as unset.
    if region and region.lower().startswith("other"):
        region = None
    if not region and provider == "Cloudflare":
        region = "auto"
    params: dict[str, str | None] = {
        "provider": provider,
        "access_key_id": config["access_key_id"],
        "secret_access_key": config["secret_access_key"],
        "region": region,
    }
    if config.get("endpoint_url"):
        params["endpoint"] = config["endpoint_url"]
        params["force_path_style"] = "true"
        # Many S3-compatible hosts (MinIO/Garage/custom gateways) allow
        # PutObject but deny HeadObject. rclone rcat treats the post-write
        # HEAD as failure even when the PUT returned 200 — surface as a
        # fake 403. Skip head/check so uploads succeed.
        params["no_head"] = "true"
        params["no_check_bucket"] = "true"
    return f":s3,{_params(params)}:{config['bucket']}"


def _google_drive_fs(config: dict[str, Any]) -> str:
    if not config.get("token"):
        raise ConnectionFailedError("token is required")
    params: dict[str, str | None] = {
        "token": config["token"],
        # Custom OAuth clients need these on the connection string so rclone
        # can refresh tokens (defaults alone only work with rclone's own
        # client id).
        "client_id": config.get("client_id"),
        "client_secret": config.get("client_secret"),
        "root_folder_id": config.get("root_folder_id"),
    }
    return f":drive,{_params(params)}:"


def _onedrive_fs(config: dict[str, Any]) -> str:
    if not config.get("token"):
        raise ConnectionFailedError("token is required")
    if not config.get("drive_id"):
        raise ConnectionFailedError("drive_id is required")
    params: dict[str, str | None] = {
        "token": config["token"],
        "client_id": config.get("client_id"),
        "client_secret": config.get("client_secret"),
        "drive_id": config["drive_id"],
        "drive_type": config.get("drive_type"),
    }
    return f":onedrive,{_params(params)}:"


def _dropbox_fs(config: dict[str, Any]) -> str:
    if not config.get("token"):
        raise ConnectionFailedError("token is required")
    params: dict[str, str | None] = {
        "token": config["token"],
        "client_id": config.get("client_id"),
        "client_secret": config.get("client_secret"),
    }
    return f":dropbox,{_params(params)}:"


# rclone's fixed "obscure" key (fs/config/obscure/obscure.go). Not
# encryption -- rclone requires `pass` options in obscured form, even on an
# inline connection string, so plaintext would be rejected. Doing it
# in-process avoids an `rclone obscure` subprocess that would put the
# password in argv (visible in `ps`).
_RCLONE_OBSCURE_KEY = bytes.fromhex(
    "9c935b48730a554d6bfd7c63c886a92bd390198eb8128afbf4de162b8b95f638",
)


def _obscure(value: str) -> str:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    iv = os.urandom(16)
    encryptor = Cipher(algorithms.AES(_RCLONE_OBSCURE_KEY), modes.CTR(iv)).encryptor()
    ciphertext = encryptor.update(value.encode()) + encryptor.finalize()
    return base64.urlsafe_b64encode(iv + ciphertext).decode().rstrip("=")


def _require(config: dict[str, Any], *keys: str) -> None:
    for key in keys:
        if not str(config.get(key) or "").strip():
            raise ConnectionFailedError(f"{key} is required")


def _root(config: dict[str, Any]) -> str:
    """Optional base directory on path-addressed servers (ftp/sftp)."""
    return str(config.get("root_path") or "").strip()


_FTP_TLS_MODES = {"explicit", "implicit", "none"}


def _ftp_fs(config: dict[str, Any]) -> str:
    _require(config, "host")
    # Explicit FTPS by default: plain FTP sends the password in cleartext,
    # so it must be an explicit opt-in ("none"), not the silent fallback.
    tls = (config.get("tls") or "explicit").strip().lower()
    if tls not in _FTP_TLS_MODES:
        raise ConnectionFailedError(
            f"tls must be one of {', '.join(sorted(_FTP_TLS_MODES))}",
        )
    password = config.get("password")
    params: dict[str, str | None] = {
        "host": config["host"].strip(),
        "port": config.get("port"),
        "user": config.get("user"),
        "pass": _obscure(password) if password else None,
        "tls": "true" if tls == "implicit" else None,
        "explicit_tls": "true" if tls == "explicit" else None,
    }
    return f":ftp,{_params(params)}:{_root(config)}"


_PEM_RE = re.compile(
    r"(-----BEGIN [A-Z0-9 ]+-----)\s*(.*?)\s*(-----END [A-Z0-9 ]+-----)",
    re.DOTALL,
)


def _single_line_pem(key: str) -> str:
    """rclone's `key_pem` wants one line with literal `\\n` separators.

    Accepts a key with real newlines, with `\\n` already escaped, or with
    newlines stripped entirely (what a single-line `<input>` does to a
    pasted key) -- base64 bodies tolerate any line wrapping, so the body is
    re-joined as one line between the BEGIN/END armor.
    """
    normalized = key.replace("\\n", "\n").strip()
    match = _PEM_RE.search(normalized)
    if not match:
        raise ConnectionFailedError("private_key must be a PEM-encoded key")
    begin, body, end = match.groups()
    return "\\n".join([begin, re.sub(r"\s+", "", body), end])


def _sftp_fs(config: dict[str, Any]) -> str:
    _require(config, "host", "user")
    password = config.get("password")
    private_key = config.get("private_key")
    if not password and not private_key:
        raise ConnectionFailedError("password or private_key is required")
    params: dict[str, str | None] = {
        "host": config["host"].strip(),
        "port": config.get("port"),
        "user": config["user"],
        "pass": _obscure(password) if password else None,
        "key_pem": _single_line_pem(private_key) if private_key else None,
        "key_file_pass": (
            _obscure(config["key_passphrase"]) if config.get("key_passphrase") else None
        ),
        "host_keys": ",".join(_pinned_host_keys(config)),
    }
    return f":sftp,{_params(params)}:{_root(config)}"


def _pinned_host_keys(config: dict[str, Any]) -> list[str]:
    """The connection's pinned server keys, as rclone `host_keys` entries.

    Required: without a pin rclone accepts *any* server key. The first
    connect gets the key from `probe_host_key` and asks the user to trust
    it, the way an SSH client does (see `RcloneBackend.connect`).
    """
    raw = str(config.get("host_key") or "").strip()
    if not raw:
        raise ConnectionFailedError("host_key is required")
    import asyncssh

    pins = []
    for entry in raw.replace("\n", ",").split(","):
        if not entry.strip():
            continue
        try:
            key = asyncssh.import_public_key(entry.strip())
        except (asyncssh.KeyImportError, ValueError) as error:
            raise ConnectionFailedError(
                f"host_key is not a valid SSH key: {error}",
            ) from error
        pins.append(_openssh_key(key))
    return pins


def _openssh_key(key: Any) -> str:  # noqa: ANN401 -- asyncssh.SSHKey
    """`algo base64` -- the known_hosts form, without a comment."""
    algorithm, blob, *_ = key.export_public_key("openssh").decode().split()
    return f"{algorithm} {blob}"


def _host_key_data(key: Any, host: str, port: int) -> dict[str, Any]:  # noqa: ANN401
    return {
        "host": host,
        "port": port,
        "algorithm": key.get_algorithm(),
        "fingerprint": key.get_fingerprint("sha256"),
        "host_key": _openssh_key(key),
    }


async def probe_host_key(
    host: str,
    port: int,
    *,
    algorithms: list[str] | None = None,
) -> Any:  # noqa: ANN401 -- asyncssh.SSHKey
    """The server's host key, from the SSH key exchange only -- no login,
    so no credential is sent to a server the user has not trusted yet."""
    import asyncssh

    options: dict[str, Any] = {}
    if algorithms:
        options["server_host_key_algs"] = algorithms
    try:
        async with asyncio.timeout(HOST_KEY_PROBE_TIMEOUT_SECONDS):
            key = await asyncssh.get_server_host_key(host, port, **options)
    except (OSError, TimeoutError, asyncssh.Error) as error:
        raise ConnectionFailedError(
            f"cannot reach SSH server {host}:{port}: {error}",
        ) from error
    if key is None:
        raise ConnectionFailedError(f"SSH server {host}:{port} offered no host key")
    return key


def _sftp_port(config: dict[str, Any]) -> int:
    raw = str(config.get("port") or "").strip()
    if not raw:
        return 22
    if not raw.isdigit() or not 0 < int(raw) < 65536:
        raise ConnectionFailedError("port must be a number between 1 and 65535")
    return int(raw)


def _is_host_key_mismatch(message: str) -> bool:
    return "host key mismatch" in message


_WEBDAV_VENDORS = {
    "fastmail", "nextcloud", "owncloud", "infinitescale",
    "sharepoint", "sharepoint-ntlm", "rclone", "other",
}


def _webdav_fs(config: dict[str, Any]) -> str:
    _require(config, "url")
    vendor = (config.get("vendor") or "other").strip().lower()
    if vendor not in _WEBDAV_VENDORS:
        raise ConnectionFailedError(
            f"vendor must be one of {', '.join(sorted(_WEBDAV_VENDORS))}",
        )
    password = config.get("password")
    params: dict[str, str | None] = {
        "url": config["url"].strip(),
        "vendor": vendor,
        "user": config.get("user"),
        "pass": _obscure(password) if password else None,
        "bearer_token": config.get("bearer_token"),
    }
    return f":webdav,{_params(params)}:"


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


_FS_BUILDERS = {
    "s3": _s3_fs,
    "google_drive": _google_drive_fs,
    "onedrive": _onedrive_fs,
    "dropbox": _dropbox_fs,
    "ftp": _ftp_fs,
    "sftp": _sftp_fs,
    "webdav": _webdav_fs,
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
                    self._rcd_process.wait(),
                    timeout=RCD_STOP_TIMEOUT_SECONDS,
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
        self,
        *args: str,
        stdin_bytes: bytes | None = None,
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
        if config.get("remote_type") == "sftp" and not config.get("host_key"):
            await self._ask_to_trust_host_key(config)
        fs = _build_fs(config)
        try:
            await self._rc_call("/operations/list", {"fs": fs, "remote": ""})
        except PluginBackendError as error:
            # File operations after connect stay blocked by rclone itself on
            # a mismatch; only connect needs the structured answer the UI
            # shows to the user.
            is_sftp = config.get("remote_type") == "sftp"
            if is_sftp and _is_host_key_mismatch(str(error)):
                raise await self._host_key_mismatch(config) from error
            raise ConnectionFailedError(str(error)) from error

    async def _ask_to_trust_host_key(self, config: dict[str, Any]) -> None:
        """First connect to an SFTP server: never trust silently. Report
        the offered key so the user can verify its fingerprint, then
        reconnect with it pinned -- OpenSSH's "Are you sure you want to
        continue connecting?" / WinSCP's "Continue connecting and add host
        key to the cache?"."""
        _require(config, "host")
        host, port = config["host"].strip(), _sftp_port(config)
        key = await probe_host_key(host, port)
        raise HostKeyUnknownError(
            f"The authenticity of {host}:{port} can't be established",
            data=_host_key_data(key, host, port),
        )

    async def _host_key_mismatch(
        self, config: dict[str, Any],
    ) -> HostKeyMismatchError:
        host, port = config["host"].strip(), _sftp_port(config)
        import asyncssh

        pinned = [asyncssh.import_public_key(pin) for pin in _pinned_host_keys(config)]
        # Ask for a key of the pinned algorithm(s), so the fingerprint shown
        # is the one that actually replaced the pinned key.
        algorithms = sorted({key.get_algorithm() for key in pinned})
        try:
            offered = await probe_host_key(host, port, algorithms=algorithms)
        except ConnectionFailedError:
            offered = await probe_host_key(host, port)
        return HostKeyMismatchError(
            f"The host key for {host}:{port} has changed",
            data={
                **_host_key_data(offered, host, port),
                "pinned_fingerprints": [
                    key.get_fingerprint("sha256") for key in pinned
                ],
            },
        )

    async def status(self) -> StatusOut:
        if self._rcd_process is None or self._rcd_process.returncode is not None:
            return StatusOut(healthy=False, detail="rclone rcd is not running")
        return StatusOut(healthy=True)

    async def list_resources(
        self,
        config: dict[str, Any],
        *,
        parent_id: str | None,
    ) -> list[Resource]:
        fs = _build_fs(config)
        result = await self._rc_call(
            "/operations/list",
            {"fs": fs, "remote": parent_id or ""},
        )
        return [self._to_resource(item) for item in result.get("list", [])]

    async def get_resource(self, config: dict[str, Any], resource_id: str) -> Resource:
        fs = _build_fs(config)
        result = await self._rc_call(
            "/operations/stat",
            {"fs": fs, "remote": resource_id},
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
            "rclone",
            *args,
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
            f"{metadata.parent_id}/{metadata.name}"
            if metadata.parent_id
            else metadata.name
        )
        if metadata.type == "folder":
            await self._rc_call("/operations/mkdir", {"fs": fs, "remote": remote})
        else:
            body = b"".join([chunk async for chunk in content])
            await self._run_subprocess(
                "rcat",
                self._target(fs, remote),
                stdin_bytes=body,
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
            changes.parent_id if changes.parent_id is not None else current.parent_id
        )
        new_name = changes.name or current.name
        new_remote = f"{new_parent}/{new_name}" if new_parent else new_name

        if new_remote != resource_id:
            await self._rc_call(
                "/operations/movefile",
                {
                    "srcFs": fs,
                    "srcRemote": resource_id,
                    "dstFs": fs,
                    "dstRemote": new_remote,
                },
            )

        if content is not None:
            body = b"".join([chunk async for chunk in content])
            await self._run_subprocess(
                "rcat",
                self._target(fs, new_remote),
                stdin_bytes=body,
            )

        return await self.get_resource(config, new_remote)

    async def delete_resource(self, config: dict[str, Any], resource_id: str) -> None:
        fs = _build_fs(config)
        resource = await self.get_resource(config, resource_id)  # 404s if missing
        endpoint = (
            "/operations/purge"
            if resource.type == "folder"
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
