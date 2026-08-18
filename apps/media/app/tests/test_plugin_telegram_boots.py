"""Proves the telegram plugin process actually boots and serves the SDK's
`/health` endpoint -- this is the part that *can* be verified without a
real Telegram session (import wiring, manifest entrypoint, uvicorn/UDS
plumbing). The resource contract itself is mocked-tested in
test_plugin_telegram_backend.py; see that file's module-level docstring
in plugins/telegram/backend.py for why a live run isn't possible here.
"""

import sys
from pathlib import Path

import pytest
import pytest_asyncio

from plugins.process_manager import PluginProcessManager


@pytest_asyncio.fixture(loop_scope="function")
async def manager(tmp_path: Path) -> PluginProcessManager:
    mgr = PluginProcessManager(tmp_path / "sockets")
    await mgr.start("telegram", [sys.executable, "-m", "plugins.telegram.main"])
    yield mgr
    await mgr.stop_all()


@pytest.mark.asyncio
async def test_telegram_plugin_process_boots_and_is_healthy(
    manager: PluginProcessManager,
) -> None:
    assert await manager.is_healthy("telegram") is True
