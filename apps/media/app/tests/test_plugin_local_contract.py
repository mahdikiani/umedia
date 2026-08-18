"""Full process+SDK+contract round-trip for the local plugin: spawns the
real `plugins/local/main.py` over a Unix socket and runs the same shared
suite the reference plugin (Phase 2) and every other Phase 3 plugin run.
"""

import sys
from pathlib import Path

import pytest
import pytest_asyncio

from plugins.client import PluginClient
from plugins.process_manager import PluginProcessManager

from .plugin_contract import run_contract_suite


@pytest_asyncio.fixture(loop_scope="function")
async def manager(tmp_path: Path) -> PluginProcessManager:
    allowed_root = tmp_path / "storage"
    allowed_root.mkdir()
    mgr = PluginProcessManager(tmp_path / "sockets")
    await mgr.start(
        "local",
        [sys.executable, "-m", "plugins.local.main"],
        env={"UMEDIA_LOCAL_STORAGE_ROOT": str(allowed_root)},
    )
    yield mgr
    await mgr.stop_all()


@pytest.mark.asyncio
async def test_local_plugin_passes_the_shared_contract_suite(
    manager: PluginProcessManager,
    tmp_path: Path,
) -> None:
    library = tmp_path / "storage" / "library"
    client = PluginClient(manager.socket_path("local"))

    await run_contract_suite(client, config={"root_path": str(library)})


@pytest.mark.asyncio
async def test_connect_rejects_a_root_path_outside_the_allowed_root(
    manager: PluginProcessManager,
    tmp_path: Path,
) -> None:
    from plugins.client import PluginRPCError

    client = PluginClient(manager.socket_path("local"))
    outside = tmp_path / "outside"

    with pytest.raises(PluginRPCError) as excinfo:
        await client.connect({"root_path": str(outside)})

    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_create_resource_accepts_a_persian_filename(
    manager: PluginProcessManager,
    tmp_path: Path,
) -> None:
    """Metadata travels in an HTTP header; non-ASCII names must still
    round-trip through PluginClient → local plugin (Cyberduck upload)."""
    from plugins.contracts import CreateResourceIn

    library = tmp_path / "storage" / "library"
    client = PluginClient(manager.socket_path("local"))
    config = {"root_path": str(library)}
    await client.connect(config)
    name = "سیدیوسف_درسته-fa.pdf"
    created = await client.create_resource(
        config,
        CreateResourceIn(name=name, type="file"),
        content=b"%PDF-persian-name",
    )
    assert created.name == name
    assert (library / name).read_bytes() == b"%PDF-persian-name"
