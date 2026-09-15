"""Phase A inbound sync: complete-scan missing → library trash, reappear
restores the same MediaFile uid, and the interval poller shares that
reconcile with `POST /providers/{uid}/sync`.
"""

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio

from apps.auth.schemas import UserSummary
from apps.media_files.factory import run_inbound_sync
from apps.media_files.schemas import MediaFileRecord
from apps.provider_connections.models import ProviderConnection
from apps.storage_objects.schemas import StorageObjectRecord
from plugins.contracts import Resource as PluginResource
from tests.media_file_helpers import FakeConnection, Harness, build_harness

CONNECTION_ID = "connection-1"
ACTOR_ID = "admin-1"
OTHER_ADMIN_ID = "admin-2"


def _file_listing() -> dict[str | None, list[PluginResource]]:
    return {
        None: [
            PluginResource(
                id="a.txt",
                type="file",
                name="a.txt",
                parent_id=None,
                size=3,
                content_type="text/plain",
            ),
        ],
    }


def _tree_listing() -> dict[str | None, list[PluginResource]]:
    return {
        None: [
            PluginResource(id="docs", type="folder", name="docs", parent_id=None),
            PluginResource(
                id="a.txt",
                type="file",
                name="a.txt",
                parent_id=None,
                size=3,
                content_type="text/plain",
            ),
        ],
        "docs": [
            PluginResource(
                id="docs/b.txt",
                type="file",
                name="b.txt",
                parent_id="docs",
                size=7,
                content_type="text/plain",
            ),
        ],
    }


@pytest_asyncio.fixture
async def harness(tmp_path: Path) -> Harness:
    built = await build_harness(
        tmp_path,
        FakeConnection(
            uid=CONNECTION_ID, name="My library", import_existing=True,
        ),
    )
    yield built
    await built.engine.dispose()


async def _library_file(
    harness: Harness, name: str, *, actor: str = ACTOR_ID,
) -> MediaFileRecord:
    roots = await harness.service.list_children(None, actor_user_id=actor)
    children = await harness.service.list_children(
        roots.items[0].uid, actor_user_id=actor,
    )
    return next(record for record in children.items if record.name == name)


async def _object_by_ref(
    harness: Harness, content_reference: str,
) -> StorageObjectRecord:
    objects = await harness.objects.list(provider_connection_id=CONNECTION_ID)
    return next(
        obj for obj in objects if obj.content_reference == content_reference
    )


# ----------------------------------------------------------------------
# Complete-scan missing
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_walk_marks_unseen_objects_missing_and_trashes_files(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _file_listing()
    first = await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )
    assert first["imported"] == 1
    media = await _library_file(harness, "a.txt")

    harness.plugins.listings[CONNECTION_ID] = {None: []}
    second = await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )

    assert second["missing"] == 1
    assert second["imported"] == 0
    obj = await _object_by_ref(harness, "a.txt")
    assert obj.status == "missing"
    assert obj.is_deleted is False

    gone = await harness.service.list_children(
        (await harness.service.list_children(None, actor_user_id=ACTOR_ID))
        .items[0]
        .uid,
        actor_user_id=ACTOR_ID,
    )
    assert gone.items == []

    trashed = await harness.service.list_children(
        None, actor_user_id=ACTOR_ID, scope="trash",
    )
    assert [item.uid for item in trashed.items] == [media.uid]
    still = await harness.files.get(media.uid)
    assert still is not None
    assert still.is_deleted is True


@pytest.mark.asyncio
async def test_already_missing_objects_are_not_counted_again(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _file_listing()
    await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )
    harness.plugins.listings[CONNECTION_ID] = {None: []}
    await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )

    again = await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )
    assert again["missing"] == 0


@pytest.mark.asyncio
async def test_missing_folder_cascades_trash_to_children(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _tree_listing()
    await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )
    docs = await _library_file(harness, "docs")
    nested = (
        await harness.service.list_children(docs.uid, actor_user_id=ACTOR_ID)
    ).items[0]

    harness.plugins.listings[CONNECTION_ID] = {
        None: [
            PluginResource(
                id="a.txt",
                type="file",
                name="a.txt",
                parent_id=None,
                size=3,
                content_type="text/plain",
            ),
        ],
    }
    result = await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )

    assert result["missing"] == 2  # docs + docs/b.txt
    assert (await _object_by_ref(harness, "docs")).status == "missing"
    assert (await _object_by_ref(harness, "docs/b.txt")).status == "missing"
    assert (await _object_by_ref(harness, "a.txt")).status == "active"

    assert (await harness.files.get(docs.uid)).is_deleted is True
    assert (await harness.files.get(nested.uid)).is_deleted is True


@pytest.mark.asyncio
async def test_incomplete_walk_does_not_mark_missing(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _tree_listing()
    await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )
    nested = await _object_by_ref(harness, "docs/b.txt")
    assert nested.status == "active"

    harness.plugins.fail_list_at.add("docs")
    with pytest.raises(RuntimeError, match="simulated plugin crash on list"):
        await harness.service.import_from_provider(
            CONNECTION_ID, actor_user_id=ACTOR_ID,
        )

    still = await _object_by_ref(harness, "docs/b.txt")
    assert still.status == "active"
    assert still.is_deleted is False
    linked = await harness.files.get_by_storage_object(still.uid)
    assert linked is not None
    assert linked.is_deleted is False


# ----------------------------------------------------------------------
# Reappear
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reappear_restores_the_same_media_file_uid_and_star(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _file_listing()
    await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )
    media = await _library_file(harness, "a.txt")
    await harness.service.set_starred(
        media.uid, actor_user_id=ACTOR_ID, starred=True,
    )
    await harness.service.set_public_permission(
        media.uid, "read", actor_user_id=ACTOR_ID,
    )
    original_uid = media.uid

    harness.plugins.listings[CONNECTION_ID] = {None: []}
    await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )

    harness.plugins.listings[CONNECTION_ID] = _file_listing()
    result = await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )

    assert result["restored"] == 1
    assert result["imported"] == 0
    restored = await _library_file(harness, "a.txt")
    assert restored.uid == original_uid
    assert restored.is_deleted is False
    assert restored.starred is True
    assert restored.public_permission == "read"
    assert (await _object_by_ref(harness, "a.txt")).status == "active"

    objects = await harness.objects.list(provider_connection_id=CONNECTION_ID)
    assert len(objects) == 1
    linked = await harness.files.get_by_storage_object(objects[0].uid)
    assert linked is not None
    assert linked.uid == original_uid


@pytest.mark.asyncio
async def test_user_trash_while_object_is_active_is_not_auto_restored(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _file_listing()
    await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )
    media = await _library_file(harness, "a.txt")
    await harness.service.soft_delete(media.uid, actor_user_id=ACTOR_ID)

    result = await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )

    assert result["restored"] == 0
    assert result["imported"] == 0
    still = await harness.files.get(media.uid)
    assert still is not None
    assert still.is_deleted is True
    obj = await _object_by_ref(harness, "a.txt")
    assert obj.status == "active"
    linked = await harness.files.get_by_storage_object(obj.uid)
    assert linked is not None
    assert linked.uid == media.uid


@pytest.mark.asyncio
async def test_import_root_lookup_ignores_owner_so_polling_does_not_fork(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _file_listing()
    await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )

    owned = await harness.files.find_import_root(
        provider_connection_id=CONNECTION_ID,
        owner_id=ACTOR_ID,
    )
    any_owner = await harness.files.find_import_root(
        provider_connection_id=CONNECTION_ID,
    )
    assert owned is not None
    assert any_owner is not None
    assert any_owner.uid == owned.uid
    assert any_owner.owner_id == ACTOR_ID

    second = await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=OTHER_ADMIN_ID,
    )
    assert second["imported"] == 0
    other_owned = await harness.files.find_import_root(
        provider_connection_id=CONNECTION_ID,
        owner_id=OTHER_ADMIN_ID,
    )
    assert other_owned is None
    still = await harness.files.find_import_root(
        provider_connection_id=CONNECTION_ID,
    )
    assert still is not None
    assert still.uid == owned.uid


# ----------------------------------------------------------------------
# Shared runner
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_inbound_sync_delegates_to_import_from_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: dict[str, object] = {}

    class _FakeService:
        async def import_from_provider(
            self, uid: str, *, actor_user_id: str,
        ) -> dict[str, int]:
            called["uid"] = uid
            called["actor"] = actor_user_id
            return {
                "imported": 0,
                "updated": 0,
                "pushed": 0,
                "seen": 0,
                "missing": 0,
                "restored": 0,
            }

    monkeypatch.setattr(
        "apps.media_files.factory.build_media_file_service_from_state",
        lambda _state: _FakeService(),
    )
    result = await run_inbound_sync(SimpleNamespace(), "conn-9", "admin-9")
    assert called == {"uid": "conn-9", "actor": "admin-9"}
    assert result["missing"] == 0
    assert result["restored"] == 0


# ----------------------------------------------------------------------
# Poller
# ----------------------------------------------------------------------


class _FakeAuth:
    def __init__(self, users: list[UserSummary]) -> None:
        self._users = users

    async def list_users(self) -> list[UserSummary]:
        return list(self._users)


def _admin(uid: str, *, active: bool = True) -> UserSummary:
    return UserSummary(
        uid=uid,
        email=f"{uid}@example.com",
        name=None,
        roles=["admin"],
        is_active=active,
    )


_IDLE_SYNC = {
    "imported": 0, "updated": 0, "pushed": 0, "seen": 0,
    "missing": 0, "restored": 0,
}


async def _persist_connection(
    harness: Harness, **overrides: object,
) -> ProviderConnection:
    from apps.provider_connections.repository import ProviderConnectionRepository

    payload: dict[str, Any] = {
        "owner_id": ACTOR_ID,
        "provider_type": "local",
        "name": "Library",
        "encrypted_config": "cipher-text",
        "status": "configured",
        "enabled": True,
        "import_existing": True,
    }
    payload.update(overrides)
    return await ProviderConnectionRepository(harness.session_factory).create(
        payload,
    )


def _recording_run(
    sink: list[str] | dict[str, str],
    *,
    fail_uid: str | None = None,
) -> Callable[..., Any]:
    async def _fake_run(  # noqa: RUF029 -- matches run_inbound_sync
        _state: object,
        connection_uid: str,
        actor_user_id: str,
    ) -> dict[str, int]:
        if isinstance(sink, list):
            sink.append(connection_uid)
        else:
            sink[connection_uid] = actor_user_id
        if fail_uid is not None and connection_uid == fail_uid:
            raise RuntimeError("plugin down")
        return dict(_IDLE_SYNC)

    return _fake_run


@pytest.mark.asyncio
async def test_poller_skips_disabled_and_already_running_connections(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.media_files import worker

    enabled = await _persist_connection(harness, name="On")
    disabled = await _persist_connection(harness, name="Off", enabled=False)
    busy = await _persist_connection(harness, name="Busy")

    calls: list[str] = []
    monkeypatch.setattr(worker, "run_inbound_sync", _recording_run(calls))

    state = SimpleNamespace(
        session_factory=harness.session_factory,
        active_syncs={busy.uid},
        auth_service=_FakeAuth([_admin(ACTOR_ID)]),
    )
    counted = await worker.poll_inbound_syncs(state)

    assert counted == 1
    assert calls == [enabled.uid]
    assert disabled.uid not in calls
    assert busy.uid not in calls
    assert busy.uid in state.active_syncs
    assert enabled.uid not in state.active_syncs


@pytest.mark.asyncio
async def test_poller_uses_import_root_owner_else_first_active_admin(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.media_files import worker

    connection = await _persist_connection(harness)
    await harness.files.create({
        "owner_id": ACTOR_ID,
        "type": "folder",
        "name": connection.name,
        "parent_id": None,
        "metadata": {"import_root": connection.uid},
        "status": "completed",
        "error": None,
        "public_permission": "none",
        "permissions": [],
        "workspace_id": None,
    })

    actors: dict[str, str] = {}
    monkeypatch.setattr(worker, "run_inbound_sync", _recording_run(actors))

    state = SimpleNamespace(
        session_factory=harness.session_factory,
        active_syncs=set(),
        auth_service=_FakeAuth([
            _admin("other-admin"),
            _admin(ACTOR_ID),
        ]),
    )
    assert await worker.poll_inbound_syncs(state) == 1
    assert actors == {connection.uid: ACTOR_ID}

    other = await _persist_connection(harness, name="No root yet")
    actors.clear()
    state.active_syncs.clear()
    await worker.poll_inbound_syncs(state)
    assert actors[connection.uid] == ACTOR_ID
    assert actors[other.uid] == "other-admin"


@pytest.mark.asyncio
async def test_poller_continues_after_a_failed_connection(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.media_files import worker

    first = await _persist_connection(harness, name="Bad")
    second = await _persist_connection(harness, name="Good")
    seen: list[str] = []
    monkeypatch.setattr(
        worker, "run_inbound_sync", _recording_run(seen, fail_uid=first.uid),
    )

    state = SimpleNamespace(
        session_factory=harness.session_factory,
        active_syncs=set(),
        auth_service=_FakeAuth([_admin(ACTOR_ID)]),
    )
    counted = await worker.poll_inbound_syncs(state)

    assert counted == 1
    assert set(seen) == {first.uid, second.uid}
    assert state.active_syncs == set()


@pytest.mark.asyncio
async def test_poller_mutates_the_existing_empty_active_syncs_set(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty `set()` is falsy — the poller must not replace it with a
    new set, or GET/POST sync status would miss an in-flight poll."""
    from apps.media_files import worker

    connection = await _persist_connection(harness)
    held: list[set[str]] = []

    async def _fake_run(state: object, connection_uid: str, _actor: str) -> dict:
        snapshot = set(getattr(state, "active_syncs"))
        held.append(snapshot)
        if connection_uid not in snapshot:
            raise AssertionError(
                f"{connection_uid} missing from shared active_syncs {snapshot}",
            )
        return dict(_IDLE_SYNC)

    monkeypatch.setattr(worker, "run_inbound_sync", _fake_run)
    active: set[str] = set()
    state = SimpleNamespace(
        session_factory=harness.session_factory,
        active_syncs=active,
        auth_service=_FakeAuth([_admin(ACTOR_ID)]),
    )

    assert await worker.poll_inbound_syncs(state) == 1
    assert held == [{connection.uid}]
    assert state.active_syncs is active
    assert state.active_syncs == set()


def _sync_poll_interval_factory() -> Callable[[], int]:
    from server.config import Settings

    return Settings.__dataclass_fields__[
        "sync_poll_interval_seconds"
    ].default_factory


def test_sync_poll_interval_defaults_to_900(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("UMEDIA_SYNC_POLL_INTERVAL_SECONDS", raising=False)
    assert _sync_poll_interval_factory()() == 900


def test_sync_poll_interval_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UMEDIA_SYNC_POLL_INTERVAL_SECONDS", "30")
    assert _sync_poll_interval_factory()() == 30
