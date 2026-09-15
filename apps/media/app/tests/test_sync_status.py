"""Sync status endpoint — Storage UI polls this while a background job runs.

`GET /providers/{uid}/sync` reads `app.state.active_syncs`, the same set
the interval poller and `POST /providers/{uid}/sync` share, so a poll tick
and a manual click cannot look idle while the other is walking.
"""

import inspect

from apps.media_files.api_schemas import SyncStatusOut
from apps.media_files.routes import _schedule_sync


def test_sync_status_out_accepts_idle_and_running() -> None:
    idle = SyncStatusOut(connection_id="conn-1", status="idle")
    running = SyncStatusOut(connection_id="conn-1", status="running")
    assert idle.status == "idle"
    assert running.status == "running"
    assert idle.model_dump() == {
        "status": "idle",
        "connection_id": "conn-1",
    }


def test_manual_sync_shares_the_inbound_runner() -> None:
    assert "run_inbound_sync" in inspect.getsource(_schedule_sync)
