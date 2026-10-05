"""End-to-end FTP / SFTP / WebDAV through the real rclone plugin.

`rclone serve <protocol>` stands up a genuine server over a temp dir, so
the shared contract suite exercises the actual connection-string mapping
(`_ftp_fs`/`_sftp_fs`/`_webdav_fs`), password obscuring, and protocol
quirks -- not just the generic rc machinery that
`test_plugin_rclone_contract.py` covers with `rclone_local_debug`.
"""

import asyncio
import contextlib
import shutil
import socket
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from plugins.client import PluginClient, PluginRPCError
from plugins.process_manager import PluginProcessManager

from .plugin_contract import run_contract_suite

pytestmark = pytest.mark.skipif(
    shutil.which("rclone") is None,
    reason="rclone binary not installed",
)

USER = "umedia"
PASSWORD = "s3cret,with:delims'"  # noqa: S105 -- throwaway test server  # exercises quoting + obscuring together


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _wait_for_port(port: int) -> None:
    async with asyncio.timeout(15.0):
        while True:
            with contextlib.suppress(OSError):
                _, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                return
            await asyncio.sleep(0.1)


@contextlib.asynccontextmanager
async def _rclone_serve(protocol: str, root: Path, *extra: str) -> AsyncIterator[int]:
    port = _free_port()
    process = await asyncio.create_subprocess_exec(
        "rclone",
        "serve",
        protocol,
        str(root),
        f"--addr=127.0.0.1:{port}",
        f"--user={USER}",
        f"--pass={PASSWORD}",
        *extra,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await _wait_for_port(port)
        yield port
    finally:
        if process.returncode is None:
            process.terminate()
        await process.wait()


@pytest_asyncio.fixture(loop_scope="function")
async def client(tmp_path: Path) -> AsyncIterator[PluginClient]:
    mgr = PluginProcessManager(tmp_path / "sockets")
    await mgr.start(
        "rclone",
        [sys.executable, "-m", "plugins.rclone.main"],
        healthy_timeout=20.0,
    )
    yield PluginClient(mgr.socket_path("rclone"), timeout=30.0)
    await mgr.stop_all()


@pytest.fixture
def served_dir(tmp_path: Path) -> Path:
    root = tmp_path / "served"
    root.mkdir()
    return root


@pytest.mark.asyncio
async def test_webdav_passes_the_contract_suite(
    client: PluginClient,
    served_dir: Path,
) -> None:
    async with _rclone_serve("webdav", served_dir) as port:
        await run_contract_suite(
            client,
            config={
                "remote_type": "webdav",
                "url": f"http://127.0.0.1:{port}",
                "vendor": "rclone",
                "user": USER,
                "password": PASSWORD,
            },
        )


@pytest.mark.asyncio
async def test_sftp_passes_the_contract_suite(
    client: PluginClient,
    served_dir: Path,
    host_key: tuple[Path, str],
) -> None:
    key_path, pin = host_key
    async with _rclone_serve("sftp", served_dir, f"--key={key_path}") as port:
        await run_contract_suite(
            client,
            config={
                "remote_type": "sftp",
                "host": "127.0.0.1",
                "port": str(port),
                "user": USER,
                "password": PASSWORD,
                "host_key": pin,
            },
        )


def _sftp_config(port: int, **extra: str) -> dict[str, str]:
    return {
        "remote_type": "sftp",
        "host": "127.0.0.1",
        "port": str(port),
        "user": USER,
        "password": PASSWORD,
        **extra,
    }


def _fingerprint(pin: str) -> str:
    import asyncssh

    return asyncssh.import_public_key(pin).get_fingerprint("sha256")


@pytest.mark.asyncio
async def test_sftp_first_connect_asks_to_trust_the_server_key(
    client: PluginClient,
    served_dir: Path,
    host_key: tuple[Path, str],
) -> None:
    """Like an SSH client's first connect: no pin -> nothing is trusted
    silently; the plugin reports the key the server offered instead."""
    key_path, pin = host_key
    async with _rclone_serve("sftp", served_dir, f"--key={key_path}") as port:
        with pytest.raises(PluginRPCError) as excinfo:
            await client.connect(_sftp_config(port))

    error = excinfo.value
    assert error.status_code == 409
    assert error.code == "HostKeyUnknownError"
    assert error.data["host_key"] == pin
    assert error.data["algorithm"] == "ssh-ed25519"
    assert error.data["fingerprint"] == _fingerprint(pin)
    assert error.data["fingerprint"].startswith("SHA256:")


@pytest.mark.asyncio
async def test_sftp_rejects_a_mismatched_pinned_host_key(
    client: PluginClient,
    served_dir: Path,
    host_key: tuple[Path, str],
) -> None:
    key_path, pin = host_key
    wrong_key = (
        "ssh-ed25519 "
        "AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
    )
    async with _rclone_serve("sftp", served_dir, f"--key={key_path}") as port:
        with pytest.raises(PluginRPCError) as excinfo:
            await client.connect(_sftp_config(port, host_key=wrong_key))

    error = excinfo.value
    assert error.status_code == 409
    assert error.code == "HostKeyMismatchError"
    assert error.data["host_key"] == pin
    assert error.data["fingerprint"] == _fingerprint(pin)
    assert error.data["pinned_fingerprints"] == [_fingerprint(wrong_key)]


@pytest.mark.asyncio
async def test_sftp_malformed_pinned_key_is_a_400(
    client: PluginClient,
    served_dir: Path,
    host_key: tuple[Path, str],
) -> None:
    key_path, _ = host_key
    async with _rclone_serve("sftp", served_dir, f"--key={key_path}") as port:
        with pytest.raises(PluginRPCError) as excinfo:
            await client.connect(_sftp_config(port, host_key="not a key"))
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_sftp_unreachable_host_is_a_400(client: PluginClient) -> None:
    with pytest.raises(PluginRPCError) as excinfo:
        await client.connect(_sftp_config(_free_port()))
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_ftp_passes_the_contract_suite(
    client: PluginClient,
    served_dir: Path,
) -> None:
    async with _rclone_serve("ftp", served_dir) as port:
        await run_contract_suite(
            client,
            config={
                "remote_type": "ftp",
                "host": "127.0.0.1",
                "port": str(port),
                "user": USER,
                "password": PASSWORD,
                "tls": "none",  # `rclone serve ftp` without a cert is plain FTP
            },
        )


@pytest.mark.asyncio
async def test_wrong_password_surfaces_as_a_400(
    client: PluginClient,
    served_dir: Path,
) -> None:
    async with _rclone_serve("webdav", served_dir) as port:
        with pytest.raises(PluginRPCError) as excinfo:
            await client.connect({
                "remote_type": "webdav",
                "url": f"http://127.0.0.1:{port}",
                "user": USER,
                "password": "wrong",
            })
    assert excinfo.value.status_code == 400
