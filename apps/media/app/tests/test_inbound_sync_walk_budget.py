"""One huge or slow provider tree must not hold the poller (2026-10-25).

An SFTP connection rooted at a home directory had ~25k toolchain files
indexed (`.cargo`, `.npm`, `.oh-my-zsh`, `.ssh`) and its walk, ~2.5 s per
folder, ran for days inside one poll tick; every later tick was skipped
("maximum number of running instances reached"), so no other connection
synced. Two fixes:

- hidden entries and dependency/cache folders are not walked or imported,
  and what is already indexed under them is left as it is;
- the poller gives each connection a time budget per tick, then pauses
  the walk and resumes it on the next tick.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio

from apps.auth.schemas import UserSummary
from apps.media_files import import_sync, worker
from apps.media_files.import_sync import ImportWalk, is_ignored_resource
from apps.provider_connections.repository import ProviderConnectionRepository
from plugins.contracts import Resource as PluginResource
from tests.media_file_helpers import FakeConnection, Harness, build_harness

CONNECTION_ID = "connection-1"
ACTOR_ID = "admin-1"


def _file(ref: str, parent: str | None) -> PluginResource:
    return PluginResource(
        id=ref,
        type="file",
        name=ref.rsplit("/", 1)[-1],
        parent_id=parent,
        size=3,
        content_type="text/plain",
    )


def _folder(ref: str, parent: str | None) -> PluginResource:
    return PluginResource(
        id=ref, type="folder", name=ref.rsplit("/", 1)[-1], parent_id=parent,
    )


def _home_listing() -> dict[str | None, list[PluginResource]]:
    return {
        None: [
            _folder(".cargo", None),
            _folder(".ssh", None),
            _file(".bashrc", None),
            _folder("projects", None),
            _file("photo.jpg", None),
        ],
        ".cargo": [_file(".cargo/config.toml", ".cargo")],
        ".ssh": [_file(".ssh/id_ed25519", ".ssh")],
        "projects": [
            _folder("projects/node_modules", "projects"),
            _file("projects/notes.txt", "projects"),
        ],
        "projects/node_modules": [
            _file("projects/node_modules/x.js", "projects/node_modules"),
        ],
    }


@pytest_asyncio.fixture
async def harness(tmp_path: Path) -> Harness:
    built = await build_harness(
        tmp_path,
        FakeConnection(uid=CONNECTION_ID, name="Home", import_existing=True),
    )
    yield built
    await built.engine.dispose()


async def _statuses(harness: Harness) -> dict[str, str]:
    objects = await harness.objects.list(provider_connection_id=CONNECTION_ID)
    return {o.content_reference: o.status for o in objects}


def _listed(harness: Harness) -> list[str | None]:
    return [call[2] for call in harness.plugins.calls if call[0] == "list"]


# ----------------------------------------------------------------------
# Hidden entries and dependency folders
# ----------------------------------------------------------------------


def test_ignored_resource_rule() -> None:
    assert is_ignored_resource(_folder(".cargo", None))
    assert is_ignored_resource(_file(".DS_Store", None))
    assert is_ignored_resource(_folder("a/node_modules", "a"))
    assert is_ignored_resource(_folder("a/__pycache__", "a"))
    assert not is_ignored_resource(_folder("Photos", None))
    assert not is_ignored_resource(_file("a/node_modules", "a"))
    assert not is_ignored_resource(_file("report.v2.pdf", None))


@pytest.mark.asyncio
async def test_hidden_and_dependency_folders_are_not_walked_or_imported(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _home_listing()

    result = await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )

    assert set(await _statuses(harness)) == {
        "projects", "photo.jpg", "projects/notes.txt",
    }
    assert ".cargo" not in _listed(harness)
    assert ".ssh" not in _listed(harness)
    assert "projects/node_modules" not in _listed(harness)
    assert result["ignored"] == 4
    assert result["pending"] == 0


@pytest.mark.asyncio
async def test_rows_already_indexed_under_hidden_folders_are_kept(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _home_listing()
    # Index the tree the way the old code did.
    monkeypatch.setattr(import_sync, "is_ignored_resource", lambda _r: False)
    await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )
    before = await _statuses(harness)
    assert before[".ssh/id_ed25519"] == "active"
    monkeypatch.undo()

    result = await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )

    assert result["missing"] == 0
    assert await _statuses(harness) == before


# ----------------------------------------------------------------------
# Time budget: pause and resume
# ----------------------------------------------------------------------


def _wide_listing() -> dict[str | None, list[PluginResource]]:
    listing: dict[str | None, list[PluginResource]] = {
        None: [_folder(f"d{i}", None) for i in range(3)],
    }
    for i in range(3):
        listing[f"d{i}"] = [_file(f"d{i}/f.txt", f"d{i}")]
    return listing


@pytest.mark.asyncio
async def test_walk_past_its_deadline_pauses_and_resumes_without_marking(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _wide_listing()
    await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )
    # A file really disappears; it may only be marked once the walk ends.
    harness.plugins.listings[CONNECTION_ID]["d0"] = []
    harness.plugins.calls.clear()

    walk = ImportWalk(deadline=0.0)  # always past: one folder per call
    results = []
    while not walk.done:
        results.append(
            await harness.service.import_from_provider(
                CONNECTION_ID, actor_user_id=ACTOR_ID, walk=walk,
            ),
        )
        assert len(results) <= 10

    assert len(results) == 4  # root + three folders
    assert [r["pending"] for r in results] == [3, 2, 1, 0]
    assert all(r["missing"] == 0 for r in results[:-1])
    assert results[-1]["missing"] == 1
    assert _listed(harness) == [None, "d2", "d1", "d0"]
    assert (await _statuses(harness))["d0/f.txt"] == "missing"


@pytest.mark.asyncio
async def test_walk_without_deadline_runs_to_the_end(harness: Harness) -> None:
    harness.plugins.listings[CONNECTION_ID] = _wide_listing()
    result = await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )
    assert result["pending"] == 0
    assert result["imported"] == 6


# ----------------------------------------------------------------------
# Poller: one slow connection cannot starve the others
# ----------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


class _FakeAuth:
    async def list_users(self) -> list[UserSummary]:  # noqa: PLR6301
        return [
            UserSummary(
                uid=ACTOR_ID,
                email="admin@example.com",
                name=None,
                roles=["admin"],
                is_active=True,
            ),
        ]


async def _two_connections(harness: Harness) -> tuple[str, str]:
    repo = ProviderConnectionRepository(harness.session_factory)
    uids = []
    for name in ("slow-home", "drive"):
        created = await repo.create({
            "owner_id": ACTOR_ID,
            "provider_type": "local",
            "name": name,
            "encrypted_config": "x",
            "status": "configured",
            "enabled": True,
            "import_existing": True,
        })
        uids.append(created.uid)
    return uids[0], uids[1]


@pytest.mark.asyncio
async def test_paused_walk_does_not_block_other_connections_and_resumes(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    slow, other = await _two_connections(harness)
    calls: list[tuple[str, ImportWalk]] = []
    slow_runs = {"n": 0}

    async def _fake_run(  # noqa: RUF029
        _state: object, uid: str, _actor: str, *, walk: ImportWalk,
    ) -> dict[str, Any]:
        calls.append((uid, walk))
        if uid == slow:
            slow_runs["n"] += 1
        if uid == slow and slow_runs["n"] == 1:
            walk.stack = [("big", "lib")]  # out of time, folders left
        else:
            walk.stack = []
        return {"seen": 1, "pending": len(walk.stack)}

    clock = _Clock()
    monkeypatch.setattr(worker, "run_inbound_sync", _fake_run)
    monkeypatch.setattr(
        worker, "schedule_content_hash_backfill", lambda _state, _uid: None,
    )
    monkeypatch.setattr(worker, "time", clock)
    state = SimpleNamespace(
        session_factory=harness.session_factory,
        active_syncs=set(),
        auth_service=_FakeAuth(),
    )

    await worker.poll_inbound_syncs(state)
    first = dict(calls)
    assert set(first) == {slow, other}  # the paused one did not block
    paused = first[slow]
    assert paused.deadline == clock.now + worker.POLL_WALK_BUDGET_SECONDS
    assert state.paused_walks == {slow: paused}

    clock.now += 900
    calls.clear()
    await worker.poll_inbound_syncs(state)
    second = dict(calls)
    assert second[slow] is paused  # same walk, resumed
    assert paused.deadline == clock.now + worker.POLL_WALK_BUDGET_SECONDS
    assert second[other] is not first[other]  # finished walks start fresh
    assert state.paused_walks == {}


@pytest.mark.asyncio
async def test_failed_or_stale_walks_start_over(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    slow, _other = await _two_connections(harness)
    walks: list[ImportWalk] = []
    fail = {"next": False}

    async def _fake_run(  # noqa: RUF029
        _state: object, uid: str, _actor: str, *, walk: ImportWalk,
    ) -> dict[str, Any]:
        if uid != slow:
            walk.stack = []
            return {}
        walks.append(walk)
        walk.stack = [("big", "lib")]
        if fail["next"]:
            raise RuntimeError("plugin down")
        return {}

    clock = _Clock()
    monkeypatch.setattr(worker, "run_inbound_sync", _fake_run)
    monkeypatch.setattr(
        worker, "schedule_content_hash_backfill", lambda _state, _uid: None,
    )
    monkeypatch.setattr(worker, "time", clock)
    state = SimpleNamespace(
        session_factory=harness.session_factory,
        active_syncs=set(),
        auth_service=_FakeAuth(),
    )

    await worker.poll_inbound_syncs(state)
    clock.now += worker.POLL_WALK_MAX_AGE_SECONDS + 1
    await worker.poll_inbound_syncs(state)
    assert walks[1] is not walks[0]  # too old: started over

    fail["next"] = True
    clock.now += 900
    await worker.poll_inbound_syncs(state)
    assert walks[2] is walks[1]
    assert slow not in state.paused_walks  # dropped after the failure
