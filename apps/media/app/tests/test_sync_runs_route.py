"""`GET /providers/{uid}/sync/runs`: a manual sync and the hash pass it
schedules each leave a run record the Storage UI can show (real app, real
`local` plugin subprocess)."""

import asyncio
import os
from pathlib import Path

import httpx

ADMIN_CREDENTIALS = {
    "email": "admin@example.com",
    "password": "a secure first password",
}


async def _authenticated(client: httpx.AsyncClient) -> None:
    state = (await client.get("/auth/state")).json()
    if state["configured"]:
        response = await client.post("/auth/sessions", json=ADMIN_CREDENTIALS)
    else:
        response = await client.post("/auth/setup", json=ADMIN_CREDENTIALS)
    assert response.status_code == 201, response.text


async def test_manual_sync_and_its_hash_pass_are_listed_as_runs(
    client: httpx.AsyncClient,
) -> None:
    await _authenticated(client)
    library = Path(os.environ["UMEDIA_DATA_DIR"]) / "storage" / "sync-runs-library"
    (library / "docs").mkdir(parents=True, exist_ok=True)
    (library / "docs" / "a.txt").write_bytes(b"abc")
    created = await client.post(
        "/providers",
        json={
            "provider_type": "local",
            "name": "sync-runs-library",
            "config": {"root_path": str(library)},
        },
    )
    assert created.status_code == 201, created.text
    uid = created.json()["uid"]

    assert (await client.post(f"/providers/{uid}/sync")).status_code == 202
    runs: list[dict] = []
    for _ in range(200):
        await asyncio.sleep(0.05)
        response = await client.get(f"/providers/{uid}/sync/runs")
        assert response.status_code == 200, response.text
        runs = response.json()
        # The hash pass starts once the sync has finished.
        if [run["kind"] for run in runs] == ["hash", "sync"] and all(
            run["status"] != "running" for run in runs
        ):
            break
    # Newest first; connecting without `import_existing` recorded nothing.
    assert [run["kind"] for run in runs] == ["hash", "sync"], runs
    by_kind = {run["kind"]: run for run in runs}
    assert by_kind["sync"]["status"] == "ok"
    assert by_kind["sync"]["trigger"] == "manual"
    assert by_kind["sync"]["result"]["imported"] == 2  # docs + docs/a.txt
    assert by_kind["sync"]["duration_ms"] is not None
    assert by_kind["hash"]["status"] == "ok"
    assert by_kind["hash"]["result"]["hashed"] == 1

    only_sync = await client.get(f"/providers/{uid}/sync/runs", params={"kind": "sync"})
    assert [run["kind"] for run in only_sync.json()] == ["sync"]

    listed = (await client.get("/providers")).json()
    connection = next(item for item in listed if item["uid"] == uid)
    assert connection["last_error"] is None
