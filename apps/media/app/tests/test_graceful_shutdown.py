"""SIGTERM shuts the server down gracefully (GlitchTip UMEDIA-5), and every
error event names the code version that raised it.

main.py used to install its own signal handler that cancelled the uvicorn
task right away, cutting off Starlette's lifespan in the middle of its
shutdown (a CancelledError traceback at deploy time).
"""

import asyncio
import os
import signal
import socket
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import sentry_sdk
from fastapi_mongo_base.monitoring.sentry import setup_sentry

from server import config

APP_DIR = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _wait_healthy(port: int, process: asyncio.subprocess.Process) -> None:
    async with httpx.AsyncClient() as client:
        for _ in range(600):  # up to 60s: four plugin processes start first
            if process.returncode is not None:
                return
            try:
                response = await client.get(f"http://127.0.0.1:{port}/api/v1/health")
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
    pytest.fail("server did not become healthy within 60s")


@pytest.fixture
def socket_dir() -> Iterator[str]:
    # Not under tmp_path: a Unix socket path must stay under ~107 bytes.
    with tempfile.TemporaryDirectory(prefix="umedia-sock-") as path:
        yield path


async def test_sigterm_runs_the_whole_lifespan_shutdown_without_a_traceback(
    tmp_path: Path, socket_dir: str,
) -> None:
    port = _free_port()
    env = {
        **os.environ,
        "UMEDIA_LISTEN_PORT": str(port),
        "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'umedia.sqlite3'}",
        "UMEDIA_DATA_DIR": str(tmp_path / "data"),
        "UMEDIA_PLUGIN_SOCKET_DIR": socket_dir,
        "SENTRY_DSN": "",
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "main.py",
        cwd=APP_DIR,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        await _wait_healthy(port, process)
        assert process.returncode is None, "server exited during startup"
        process.send_signal(signal.SIGTERM)
        output, _ = await asyncio.wait_for(process.communicate(), timeout=45)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()

    text = output.decode(errors="replace")
    # uvicorn re-raises the captured SIGTERM once it has shut down cleanly.
    assert process.returncode in {0, -signal.SIGTERM}, text[-3000:]
    assert "Shutdown complete" in text  # the lifespan's last line ran
    assert "CancelledError" not in text, text[-3000:]
    assert "Traceback" not in text, text[-3000:]


# ----------------------------------------------------------------------
# Release tag
# ----------------------------------------------------------------------


def test_release_names_the_built_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SENTRY_RELEASE", raising=False)
    monkeypatch.setenv("UMEDIA_GIT_SHA", "22842ce0a1b2c3d4e5f6")
    version = config._project_version()
    assert version != "0.0.0"
    assert config.sentry_release() == f"umedia-media@{version}+22842ce0a1b2"
    # Settings is a process singleton: check the factory it is built with.
    field = config.Settings.__dataclass_fields__["sentry_release"]
    assert field.default_factory() == config.sentry_release()


def test_release_without_a_built_commit_is_the_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SENTRY_RELEASE", raising=False)
    monkeypatch.delenv("UMEDIA_GIT_SHA", raising=False)
    assert config.sentry_release() == f"umedia-media@{config._project_version()}"


def test_explicit_sentry_release_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_RELEASE", "umedia@hotfix-1")
    monkeypatch.setenv("UMEDIA_GIT_SHA", "22842ce")
    assert config.sentry_release() == "umedia@hotfix-1"


def test_the_release_reaches_sentry_init(monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end through fastapi_mongo_base's Sentry setup, which reads
    `settings.sentry_release` (create_app calls it with our Settings)."""
    # The process singleton, built at import from the release factory.
    settings = config.Settings()
    monkeypatch.setattr(settings, "sentry_dsn", "https://key@glitchtip.invalid/1")
    with patch.object(sentry_sdk, "init") as init:
        assert setup_sentry(settings)
    assert init.call_args.kwargs["release"] == settings.sentry_release
    assert settings.sentry_release == config.sentry_release()
