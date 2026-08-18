"""Tests for PluginProcessManager, against tests/fixtures/fake_plugin.py.

Written before plugins/process_manager.py exists, per
docs/08-implementation-plan.md's TDD process.
"""

import os
import sys
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from plugins.process_manager import (
    PluginProcessManager,
    PluginStartError,
    _minimal_env,
)

FAKE_PLUGIN = Path(__file__).parent / "fixtures" / "fake_plugin.py"


def _command() -> list[str]:
    return [sys.executable, str(FAKE_PLUGIN)]


@pytest_asyncio.fixture(loop_scope="function")
async def manager(tmp_path: Path) -> PluginProcessManager:
    """Function-scoped loop, pinned explicitly.

    pytest.ini's `asyncio_default_fixture_loop_scope = session` would
    otherwise put this fixture's teardown on a *different* event loop than
    the (function-scoped) test that used it -- fine for ordinary async
    fixtures, but `PluginProcessManager` holds real subprocess transports,
    which are bound to whichever loop was running when they were created.
    Tearing them down from another loop fails with "Future attached to a
    different loop"; see docs/02-architecture.md "Correctness &
    consistency" for why loop discipline matters here specifically.
    """
    mgr = PluginProcessManager(tmp_path / "sockets")
    yield mgr
    await mgr.stop_all()


@pytest.mark.asyncio
async def test_start_spawns_a_healthy_plugin(manager: PluginProcessManager) -> None:
    await manager.start("fake", _command())

    assert await manager.is_healthy("fake") is True
    assert manager.socket_path("fake").exists()
    assert manager.running_plugin_ids() == ["fake"]


@pytest.mark.asyncio
async def test_start_is_rejected_for_an_already_running_plugin(
    manager: PluginProcessManager,
) -> None:
    await manager.start("fake", _command())

    with pytest.raises(ValueError, match="already running"):
        await manager.start("fake", _command())


@pytest.mark.asyncio
async def test_start_fails_fast_for_a_too_long_socket_path(
    tmp_path: Path,
) -> None:
    """A too-long socket path must be an immediate, clear error -- not a
    silent 10s health-check timeout. Found this the hard way: `local.sock`
    fit under a very deep tmp path, `telegram.sock`/`rclone.sock` didn't."""
    deeply_nested = tmp_path
    for segment in range(10):
        deeply_nested = deeply_nested / f"a-fairly-long-segment-name-{segment}"
    manager = PluginProcessManager(deeply_nested)

    with pytest.raises(PluginStartError, match="byte"):
        await manager.start("telegram", _command(), healthy_timeout=1.0)


@pytest.mark.asyncio
async def test_start_raises_when_the_plugin_never_becomes_healthy(
    manager: PluginProcessManager,
) -> None:
    # A command that runs but never opens the socket: `_wait_until_healthy`
    # must time out rather than hang, and clean up after itself.
    with pytest.raises(PluginStartError):
        await manager.start(
            "never-healthy",
            [sys.executable, "-c", "import time; time.sleep(30)"],
            healthy_timeout=0.5,
        )

    assert manager.running_plugin_ids() == []


@pytest.mark.asyncio
async def test_is_healthy_is_false_for_an_unknown_plugin(
    manager: PluginProcessManager,
) -> None:
    assert await manager.is_healthy("does-not-exist") is False


@pytest.mark.asyncio
async def test_stop_terminates_the_process_and_cleans_up_the_socket(
    manager: PluginProcessManager,
) -> None:
    await manager.start("fake", _command())
    socket_path = manager.socket_path("fake")

    await manager.stop("fake")

    assert manager.running_plugin_ids() == []
    assert not socket_path.exists()
    assert await manager.is_healthy("fake") is False


async def _fetch(socket_path: Path) -> str:
    transport = httpx.AsyncHTTPTransport(uds=str(socket_path))
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.get("http://plugin/health")
        return response.text


@pytest.mark.asyncio
async def test_plugin_does_not_inherit_the_core_process_environment(
    manager: PluginProcessManager,
) -> None:
    """A plugin subprocess must not see secrets like DATABASE_URL/
    UMEDIA_MASTER_KEY just because they're set in the core's environment --
    see docs/02-architecture.md's isolation model."""
    os.environ["UMEDIA_TEST_SHOULD_NOT_LEAK"] = "leaked-secret"
    try:
        await manager.start("no-leak", _command())  # no `env=` passed

        assert await _fetch(manager.socket_path("no-leak")) == "ok"
    finally:
        del os.environ["UMEDIA_TEST_SHOULD_NOT_LEAK"]


def test_minimal_env_forces_utf8_filesystem_encoding() -> None:
    """Plugins start with a bare PATH. Without a UTF-8 locale, Python
    uses the ascii codec and macOS filenames with U+202F fail PutObject.
    """
    env = _minimal_env(None)
    assert env["LANG"] == "C.UTF-8"
    assert env["LC_ALL"] == "C.UTF-8"
    assert env["PYTHONUTF8"] == "1"
    assert env["PATH"] == os.environ.get("PATH", "")
    assert "UMEDIA_MASTER_KEY" not in env
    assert "DATABASE_URL" not in env


@pytest.mark.asyncio
async def test_explicitly_passed_env_vars_do_reach_the_plugin(
    manager: PluginProcessManager,
) -> None:
    await manager.start("with-env", _command(), env={"UMEDIA_TEST_MARKER": "hello"})

    assert await _fetch(manager.socket_path("with-env")) == "hello"


@pytest.mark.asyncio
async def test_stop_on_an_unknown_plugin_is_a_no_op(
    manager: PluginProcessManager,
) -> None:
    await manager.stop("does-not-exist")


@pytest.mark.asyncio
async def test_crashed_plugin_is_restarted_with_backoff(
    manager: PluginProcessManager,
) -> None:
    await manager.start("flaky", [*_command(), "--crash-after=0.2"])
    pid_before = manager.pid("flaky")
    assert pid_before is not None

    # backoff for the 1st restart is ~1s (RESTART_BACKOFF_BASE); give the
    # crash + backoff + respawn + new-socket-health-check room to finish.
    import asyncio

    for _ in range(40):  # up to ~4s
        await asyncio.sleep(0.1)
        if manager.pid("flaky") not in (None, pid_before) and await (
            manager.is_healthy("flaky")
        ):
            break

    assert manager.pid("flaky") is not None
    assert manager.pid("flaky") != pid_before
    assert await manager.is_healthy("flaky") is True


@pytest.mark.asyncio
async def test_stop_all_stops_every_plugin(manager: PluginProcessManager) -> None:
    await manager.start("one", _command())
    await manager.start("two", _command())

    await manager.stop_all()

    assert manager.running_plugin_ids() == []
