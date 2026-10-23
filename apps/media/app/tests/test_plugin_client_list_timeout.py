"""Folder listing gets a realistic deadline and is not retried on a read
timeout (GlitchTip UMEDIA-6/3).

The Drive camera folder took longer than the 10s JSON-RPC default; the
client then retried the same listing twice more (3 x 10s + backoff =
31.5s, every poll), each retry starting another rclone listing.

The plugin now logs every failed or slow rc call -- with rclone's error
text, which quotes the connection string, credentials masked.
"""

import asyncio
import json
import logging
import shutil
import socket
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from apps.media_files.services import _is_folder_level_failure
from plugins import client as client_module
from plugins.client import PluginClient, PluginRPCError
from plugins.contracts import (
    PluginBackendError,
    ResourceUnavailableError,
    UpstreamTimeoutError,
)
from plugins.rclone import backend as rclone_backend

LISTING = b'[{"id":"Media/DCIM/Camera/1.jpg","type":"file","name":"1.jpg"}]'


async def _slow_plugin(
    socket_path: Path, delay: float, seen: list[bytes],
) -> asyncio.Server:
    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        seen.append(head.split(b"\r\n", 1)[0])
        await asyncio.sleep(delay)
        try:
            writer.write(
                b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n"
                b"content-length: " + str(len(LISTING)).encode() + b"\r\n\r\n"
                + LISTING,
            )
            await writer.drain()
        except ConnectionError:
            pass  # the client already gave up
        finally:
            writer.close()

    return await asyncio.start_unix_server(handle, path=str(socket_path))


async def test_listing_outlives_the_json_rpc_default_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        client_module, "LIST_TIMEOUT", httpx.Timeout(0.2, read=2.0),
    )
    socket_path = tmp_path / "slow.sock"
    seen: list[bytes] = []
    server = await _slow_plugin(socket_path, 0.5, seen)
    async with server:
        client = PluginClient(socket_path, timeout=0.2)  # JSON-RPC default
        listed = await client.list_resources({}, parent_id="Media/DCIM/Camera")
    assert [item.id for item in listed] == ["Media/DCIM/Camera/1.jpg"]
    assert len(seen) == 1


async def test_a_listing_read_timeout_is_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # `server.server`'s dictConfig disables loggers created before it.
    monkeypatch.setattr(logging.getLogger(client_module.__name__), "disabled", False)
    caplog.set_level(logging.INFO, logger=client_module.__name__)
    monkeypatch.setattr(
        client_module, "LIST_TIMEOUT", httpx.Timeout(0.2, read=0.2),
    )
    socket_path = tmp_path / "slow.sock"
    seen: list[bytes] = []
    server = await _slow_plugin(socket_path, 0.6, seen)
    async with server:
        client = PluginClient(socket_path)
        with pytest.raises(PluginRPCError) as caught:
            await client.list_resources({}, parent_id="Media/DCIM/Camera")

    error = caught.value
    assert len(seen) == 1  # one listing started, not three
    assert error.attempts == 1
    assert error.transport_error == "ReadTimeout"
    assert error.elapsed_seconds is not None
    # The folder is on the error and in the log line, not in the message
    # GlitchTip groups issues by.
    assert error.target == "GET /resources?parent_id=Media%2FDCIM%2FCamera"
    assert "Camera" not in str(error)
    (record,) = [
        record for record in caplog.records if record.name == client_module.__name__
    ]
    assert record.levelno == logging.WARNING
    assert record.args[0] == error.target
    assert record.args[2] == 1  # attempts
    assert record.args[3] == "ReadTimeout"
    # The walk treats a slow folder as that folder's problem, not the
    # connection's: it is skipped and the walk goes on.
    assert _is_folder_level_failure(error)


async def test_an_unreachable_plugin_is_retried_then_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "READ_RETRY_BACKOFF_SECONDS", 0.0)
    client = PluginClient(tmp_path / "no-such-plugin.sock")
    with pytest.raises(PluginRPCError) as caught:
        await client.list_resources({}, parent_id="Documents")

    error = caught.value
    assert error.transport_error == "ConnectError"
    assert error.attempts == client_module.READ_RETRY_ATTEMPTS
    assert not _is_folder_level_failure(error)  # stops the walk


def test_the_plugin_gives_up_on_a_listing_before_the_core_does() -> None:
    """So a slow listing ends with the plugin's own 504, not the core
    hanging up while rclone keeps listing."""
    assert (
        rclone_backend.RC_LIST_TIMEOUT_SECONDS
        < client_module.LIST_READ_TIMEOUT_SECONDS
    )
    assert client_module.LIST_TIMEOUT.read == client_module.LIST_READ_TIMEOUT_SECONDS


async def test_rclone_listing_uses_the_list_deadline() -> None:
    backend = rclone_backend.RcloneBackend()
    rc_call = AsyncMock(return_value={"list": []})
    with (
        patch.object(backend, "_rc_call", rc_call),
        patch("plugins.rclone.backend._build_fs", return_value=":drive:"),
    ):
        await backend.list_resources({}, parent_id="Media/DCIM/Camera")
    timeout = rc_call.await_args.kwargs["rc_timeout"]
    assert timeout == rclone_backend.RC_LIST_TIMEOUT_SECONDS


async def test_rclone_rc_timeout_is_a_504_without_credentials_in_it() -> None:
    backend = rclone_backend.RcloneBackend()
    with (
        patch.object(
            httpx.AsyncClient, "post", AsyncMock(side_effect=httpx.ReadTimeout("")),
        ),
        pytest.raises(UpstreamTimeoutError) as caught,
    ):
        await backend._rc_call(
            "/operations/list",
            {"fs": ":drive,token='SECRET-TOKEN':", "remote": "Media/DCIM/Camera"},
            rc_timeout=1.0,
        )
    assert caught.value.http_status == 504
    assert "Media/DCIM/Camera" in str(caught.value)
    assert "SECRET-TOKEN" not in str(caught.value)
    assert _is_folder_level_failure(
        PluginRPCError("returned 504", status_code=504),
    )


# ----------------------------------------------------------------------
# rclone's error text quotes the connection string: never log it
# ----------------------------------------------------------------------

DRIVE_FS = (
    ":drive,client_id=app.example,client_secret=CLIENT-SECRET,"
    "token='{\"access_token\":\"SECRET-TOKEN\",\"expiry\":\"2026-10-22\"}':"
)


@pytest.fixture
def rclone_logs(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
) -> pytest.LogCaptureFixture:
    # `server.server`'s dictConfig disables loggers created before it.
    monkeypatch.setattr(logging.getLogger(rclone_backend.__name__), "disabled", False)
    caplog.set_level(logging.WARNING, logger=rclone_backend.__name__)
    return caplog


async def test_a_failed_rc_call_masks_the_connection_string(
    rclone_logs: pytest.LogCaptureFixture,
) -> None:
    # rclone quotes the remote Go-style (`%q`): inner double quotes escaped.
    rc_answer = httpx.Response(
        500,
        json={
            "error": (
                f"failed to create file system for {json.dumps(DRIVE_FS)}: "
                "googleapi: Error 500: Internal Error"
            ),
            "input": {"fs": DRIVE_FS, "remote": "Media/DCIM/Camera"},
            "path": "operations/list",
            "status": 500,
        },
    )
    backend = rclone_backend.RcloneBackend()
    with (
        patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=rc_answer)),
        pytest.raises(PluginBackendError) as caught,
    ):
        await backend._rc_call(
            "/operations/list",
            {"fs": DRIVE_FS, "remote": "Media/DCIM/Camera"},
        )

    (warning,) = [
        record
        for record in rclone_logs.records
        if "failed after" in record.getMessage()
    ]
    for text in (str(caught.value), warning.getMessage()):
        assert "SECRET" not in text
        assert "<remote>" in text
        assert "googleapi: Error 500" in text
    assert "Media/DCIM/Camera" in warning.getMessage()


def test_redact_remotes_masks_credentials_rclone_rewrote() -> None:
    """A remote quoted in another form than the one it was given still
    loses its credential values; the other parameters stay readable."""
    onedrive = ":onedrive,token='{\"access_token\":\"SECRET-1\"}',drive_id=b!abc:"
    sftp = (
        ":sftp,host=files.example,user=mahdi,pass=SECRET-2,"
        "key_pem=-----BEGIN OPENSSH PRIVATE KEY-----\\nSECRET-3\\n"
        "-----END OPENSSH PRIVATE KEY-----,key_file_pass=SECRET-4:home"
    )
    # Quotes escaped along the way (e.g. Python's repr of the remote).
    escaped = ":dropbox,token=\\'{\"access_token\":\"SECRET-5\"}\\':"
    text = rclone_backend.redact_remotes(
        f"couldn't connect {onedrive} or {sftp} or {escaped}",
        ":drive,token=other:",  # not the remote in the text
    )
    assert "SECRET" not in text
    assert "drive_id=b!abc" in text
    assert "host=files.example,user=mahdi" in text


async def test_a_failed_rcat_masks_the_connection_string() -> None:
    target = DRIVE_FS + "Media/new.jpg"
    process = AsyncMock(returncode=1)
    process.communicate.return_value = (
        b"",
        # rclone quotes the target's directory Go-style (`%q`), not the
        # whole argument.
        "Failed to rcat: failed to create file system for "
        f"{json.dumps(DRIVE_FS + 'Media/')}: couldn't connect".encode(),
    )
    backend = rclone_backend.RcloneBackend()
    with (
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)),
        pytest.raises(PluginBackendError) as caught,
    ):
        await backend._run_subprocess(
            "rcat", target, fs=DRIVE_FS, stdin_bytes=b"x",
        )
    assert "SECRET" not in str(caught.value)
    assert "<remote>Media/" in str(caught.value)


class _FailedCat:
    """An `rclone cat` that printed nothing and exited with `stderr`."""

    def __init__(self, stderr: bytes, returncode: int = 1) -> None:
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()
        self.returncode = returncode

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None: ...


async def test_a_failed_cat_masks_the_connection_string() -> None:
    """`rclone cat` quotes the remote's directory in its error, and
    read_content raises that text inside the streaming response: the
    plugin logs it with a traceback that reaches the core's log."""
    process = _FailedCat(
        "Failed to create file system for "
        f"{json.dumps(DRIVE_FS + 'Media/')}: couldn't connect".encode(),
    )
    backend = rclone_backend.RcloneBackend()
    with (
        patch("plugins.rclone.backend._build_fs", return_value=DRIVE_FS),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)),
        pytest.raises(PluginBackendError) as caught,
    ):
        async for _ in backend.read_content({}, "Media/1.jpg", range_header=None):
            pass
    assert "SECRET" not in str(caught.value)
    assert "<remote>Media/" in str(caught.value)


async def test_rc_errors_are_classified_before_redaction() -> None:
    """Masking a credential that runs up to the next delimiter must not
    also swallow the marker that makes the error a 422 or a 401."""
    rc_answer = httpx.Response(
        500,
        json={"error": "GET https://h/x?access_token=S4&y=1 invalidResourceId"},
    )
    backend = rclone_backend.RcloneBackend()
    with (
        patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=rc_answer)),
        pytest.raises(ResourceUnavailableError) as caught,
    ):
        await backend._rc_call(
            "/operations/list", {"fs": ":onedrive:", "remote": "Personal Vault"},
        )
    assert caught.value.http_status == 422
    assert "S4" not in str(caught.value)


def test_redact_remotes_masks_a_password_inside_a_url() -> None:
    text = rclone_backend.redact_remotes(
        "couldn't connect :webdav,url='https://probe:URL-SECRET@dav.example/x':",
    )
    assert "URL-SECRET" not in text
    assert "https://probe:<redacted>@dav.example/x" in text
    plain = "Get http://dav.example:8080/x: connection refused"
    assert rclone_backend.redact_remotes(plain) == plain


def test_a_resource_stat_outwaits_the_plugins_rc_call() -> None:
    """A stat's read timeout is not retried, so the core waits longer
    than the plugin's own rc deadline instead of giving up first."""
    assert (
        rclone_backend.RC_CALL_TIMEOUT_SECONDS
        < client_module.RESOURCE_READ_TIMEOUT_SECONDS
    )
    assert (
        client_module.RESOURCE_TIMEOUT.read
        == client_module.RESOURCE_READ_TIMEOUT_SECONDS
    )


async def test_get_resource_uses_the_resource_deadline() -> None:
    client = PluginClient(Path("/nonexistent.sock"))
    response = httpx.Response(
        200, json={"id": "a.jpg", "type": "file", "name": "a.jpg"},
    )
    request = AsyncMock(return_value=response)
    with patch.object(httpx.AsyncClient, "request", request):
        await client.get_resource({}, "a.jpg")
    assert request.await_args.kwargs["timeout"] is client_module.RESOURCE_TIMEOUT


@pytest.mark.parametrize("error", [httpx.ConnectTimeout(""), httpx.PoolTimeout("")])
async def test_a_request_that_never_reached_the_plugin_is_retried(
    error: Exception, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "READ_RETRY_BACKOFF_SECONDS", 0.0)
    request = AsyncMock(side_effect=[error, httpx.Response(200, content=LISTING)])
    with patch.object(httpx.AsyncClient, "request", request):
        listed = await PluginClient(Path("/nonexistent.sock")).list_resources(
            {}, parent_id="Media/DCIM/Camera",
        )
    assert [item.id for item in listed] == ["Media/DCIM/Camera/1.jpg"]
    assert request.await_count == 2


# ----------------------------------------------------------------------
# The same, against the real rclone binary and what it really prints
# ----------------------------------------------------------------------


def _unreachable_sftp_config() -> dict:
    import asyncssh

    with socket.socket() as sock:  # a port nothing listens on
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    key = asyncssh.generate_private_key("ssh-ed25519")
    host = asyncssh.generate_private_key("ssh-ed25519")
    return {
        "remote_type": "sftp",
        "host": "127.0.0.1",
        "port": str(port),
        "user": "probe",
        "password": "SECRET-PASSWORD",
        "private_key": key.export_private_key("openssh").decode(),
        "host_key": host.export_public_key("openssh").decode().strip(),
    }


def _credential_values(fs: str) -> list[str]:
    """The obscured password and the private key's body, as they appear
    on the connection string."""
    values = []
    for part in fs.split(","):
        name, _, value = part.partition("=")
        if name == "pass":
            values.append(value)
        if name == "key_pem":
            values.extend(line for line in value.split("\\n")[1:-1] if line)
    assert values
    return values


needs_rclone = pytest.mark.skipif(
    shutil.which("rclone") is None, reason="rclone binary not installed",
)


@needs_rclone
async def test_real_rclone_cat_and_rcat_errors_carry_no_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One dial instead of ten (about 11s) before rclone gives up.
    monkeypatch.setenv("RCLONE_LOW_LEVEL_RETRIES", "1")
    config = _unreachable_sftp_config()
    fs = rclone_backend._build_fs(config)
    backend = rclone_backend.RcloneBackend()

    with pytest.raises(PluginBackendError) as cat_failed:
        async for _ in backend.read_content(config, "dir/x.jpg", range_header=None):
            pass
    with pytest.raises(PluginBackendError) as rcat_failed:
        await backend._run_subprocess(
            "rcat", backend._target(fs, "dir/new.jpg"), fs=fs, stdin_bytes=b"x",
        )

    # rclone quotes the target (cat) or its directory (rcat), masked.
    assert "<remote>dir/x.jpg" in str(cat_failed.value)
    assert "<remote>dir/" in str(rcat_failed.value)
    for caught in (cat_failed, rcat_failed):
        text = str(caught.value)
        assert not [value for value in _credential_values(fs) if value in text]
