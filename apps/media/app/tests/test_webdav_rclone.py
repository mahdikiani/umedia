"""rclone `webdav` remote against a live listener on the real app."""

import asyncio
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import uvicorn

from apps.user_access_keys.factory import build_user_access_key_service_from_state
from server.config import Settings
from server.server import app as fastapi_app
from tests.test_s3_api import _authenticated

RCLONE = shutil.which("rclone")
pytestmark = pytest.mark.skipif(RCLONE is None, reason="rclone not installed")


@pytest.fixture(scope="module")
def live_port(client: httpx.AsyncClient) -> Iterator[int]:
    del client
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(
        uvicorn.Config(
            fastapi_app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            lifespan="off",
            access_log=False,
        ),
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.05)
    else:
        raise RuntimeError("test server did not start")
    yield port
    server.should_exit = True
    thread.join(timeout=5)


def _obscure(secret: str) -> str:
    return subprocess.run(  # noqa: S603
        [RCLONE, "obscure", secret],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest_asyncio.fixture(scope="module")
async def remote(client: httpx.AsyncClient, live_port: int) -> str:
    user_id = await _authenticated(client)
    root = Path(Settings().data_dir) / "storage" / "webdav-rclone"
    made = await client.post(
        "/providers",
        json={
            "provider_type": "local",
            "name": "webdav-rclone-library",
            "config": {"root_path": str(root)},
        },
    )
    assert made.status_code == 201, made.text
    keys = build_user_access_key_service_from_state(fastapi_app.state)
    key = await keys.ensure_default_key(user_id)
    obscured = await asyncio.to_thread(_obscure, keys.decrypt_secret_str(key))
    return (
        f":webdav,url='http://127.0.0.1:{live_port}{Settings.WEBDAV_PREFIX}',"
        f"vendor=other,user='{key.access_key_id}',pass='{obscured}':"
    )


def _rclone(*args: str, stdin: bytes | None = None) -> str:
    done = subprocess.run(  # noqa: S603
        [RCLONE, "--config", "/dev/null", *args],
        input=stdin,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr.decode()
    return done.stdout.decode()


def test_rclone_round_trip(remote: str, tmp_path: Path) -> None:
    source = tmp_path / "نامه.txt"
    source.write_bytes(b"rclone through webdav\n" * 1000)
    _rclone("mkdir", f"{remote}/inbox")
    _rclone("copyto", str(source), f"{remote}/inbox/نامه.txt")

    assert "نامه.txt" in _rclone("lsf", f"{remote}/inbox")
    assert _rclone("cat", f"{remote}/inbox/نامه.txt").startswith("rclone through")

    _rclone("moveto", f"{remote}/inbox/نامه.txt", f"{remote}/inbox/moved.txt")
    assert _rclone("lsf", f"{remote}/inbox").strip() == "moved.txt"
    _rclone("copyto", f"{remote}/inbox/moved.txt", f"{remote}/inbox/copied.txt")
    _rclone("deletefile", f"{remote}/inbox/moved.txt")
    assert _rclone("lsf", f"{remote}/inbox").strip() == "copied.txt"
    _rclone("rcat", f"{remote}/inbox/streamed.bin", stdin=b"x" * 100_000)
    assert "streamed.bin" in _rclone("lsf", f"{remote}/inbox")
    _rclone("purge", f"{remote}/inbox")
    assert "inbox" not in _rclone("lsf", f"{remote}/")
