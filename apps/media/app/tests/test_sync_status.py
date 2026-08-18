"""Sync status endpoint — Storage UI polls this while a background job runs."""

from apps.media_files.api_schemas import SyncStatusOut


def test_sync_status_out_accepts_idle_and_running() -> None:
    idle = SyncStatusOut(connection_id="conn-1", status="idle")
    running = SyncStatusOut(connection_id="conn-1", status="running")
    assert idle.status == "idle"
    assert running.status == "running"
    assert idle.model_dump() == {
        "status": "idle",
        "connection_id": "conn-1",
    }
