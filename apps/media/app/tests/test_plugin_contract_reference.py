"""Proves the SDK + shared contract suite work end-to-end over a real Unix
socket, against the in-memory reference plugin (fixtures/reference_plugin.py).

Phase 3's real plugins (local/s3/rclone/telegram) get tested by pointing
this exact pattern -- spawn via PluginProcessManager, run
plugin_contract.run_contract_suite -- at their own backend instead.
"""

import sys
from pathlib import Path

import pytest
import pytest_asyncio

from plugins.client import PluginClient
from plugins.process_manager import PluginProcessManager

from .plugin_contract import run_contract_suite

REFERENCE_PLUGIN = Path(__file__).parent / "fixtures" / "reference_plugin.py"


@pytest_asyncio.fixture(loop_scope="function")
async def manager(tmp_path: Path) -> PluginProcessManager:
    mgr = PluginProcessManager(tmp_path / "sockets")
    await mgr.start("reference", [sys.executable, str(REFERENCE_PLUGIN)])
    yield mgr
    await mgr.stop_all()


@pytest.mark.asyncio
async def test_reference_plugin_passes_the_shared_contract_suite(
    manager: PluginProcessManager,
) -> None:
    client = PluginClient(manager.socket_path("reference"))

    await run_contract_suite(client, config={"accept": True})


@pytest.mark.asyncio
async def test_status_endpoint(manager: PluginProcessManager) -> None:
    client = PluginClient(manager.socket_path("reference"))

    status = await client.status()

    assert status.healthy is True


@pytest.mark.asyncio
async def test_connect_failure_surfaces_as_a_400(
    manager: PluginProcessManager,
) -> None:
    from plugins.client import PluginRPCError

    client = PluginClient(manager.socket_path("reference"))

    with pytest.raises(PluginRPCError) as excinfo:
        await client.connect({"accept": False})

    assert excinfo.value.status_code == 400
