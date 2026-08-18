"""Full process+rcd+contract round-trip for the rclone plugin.

Runs the real `plugins/rclone/main.py` (spawns a real `rclone rcd` child)
over a Unix socket and proves the *generic* machinery -- rc calls,
cat/rcat streaming, resource mapping -- end-to-end with the shared
contract suite, using rclone's own rock-solid `local` backend rather than
a real S3/Google Drive account.

This deliberately does not prove the `s3`/`google_drive` *connection-string
mapping* end-to-end: `test_plugin_rclone_backend.py` unit-tests those
builders directly, and both were validated by hand against a real
`rclone` binary (list/stat/mkdir/read all confirmed working against a
moto-mocked S3 server; moto's own AWS-SDK-v2 compatibility gap blocked
verifying the *write* path specifically -- see docs/09-tasks.md P3.2).
`remote_type: rclone_local_debug` (see `plugins/rclone/backend.py`) is a
permanent-but-uncataloged remote type kept specifically for this: no
manifest references it, so it's unreachable through
`apps/provider_connections` -- it exists purely so this generic-machinery
test doesn't depend on real S3/Google Drive credentials or a mock
server's own quirks. (A cross-process `monkeypatch` of `_FS_BUILDERS`
doesn't work here -- the plugin runs as a genuinely separate subprocess
with its own imported copy of the module.)
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
    mgr = PluginProcessManager(tmp_path / "sockets")
    await mgr.start(
        "rclone",
        [sys.executable, "-m", "plugins.rclone.main"],
        healthy_timeout=20.0,  # rclone rcd startup is slower than a bare socket
    )
    yield mgr
    await mgr.stop_all()


@pytest.mark.asyncio
async def test_rclone_plugin_passes_the_shared_contract_suite(
    manager: PluginProcessManager,
    tmp_path: Path,
) -> None:
    remote_dir = tmp_path / "rclone-local-remote"
    remote_dir.mkdir()
    client = PluginClient(manager.socket_path("rclone"), timeout=20.0)

    await run_contract_suite(
        client,
        config={"remote_type": "rclone_local_debug", "path": str(remote_dir)},
    )


@pytest.mark.asyncio
async def test_connect_rejects_an_unsupported_remote_type(
    manager: PluginProcessManager,
) -> None:
    from plugins.client import PluginRPCError

    client = PluginClient(manager.socket_path("rclone"), timeout=20.0)

    with pytest.raises(PluginRPCError) as excinfo:
        await client.connect({"remote_type": "not-a-real-backend"})

    assert excinfo.value.status_code == 400
