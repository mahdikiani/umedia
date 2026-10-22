"""Inbound sync walk vs. failing folder listings (GlitchTip UMEDIA-4/6).

One unlistable folder (OneDrive's Personal Vault, a Drive camera folder
that times out) used to abort the whole walk on every poll, forever: no
deletions reflected, no hash backfill, nothing shown to the user. Now a
folder failure skips that subtree and the walk goes on, while failures
that are about the connection still stop it. Whatever a pass did not see
must never be marked missing, and a pass that would trash an unusually
large share of the library waits for the owner to confirm the count.
"""

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio

from apps.media_files import services
from apps.media_files.factory import run_inbound_sync
from apps.media_files.notifications import NotificationRepository
from apps.provider_connections.repository import ProviderConnectionRepository
from apps.storage_objects.schemas import StorageObjectRecord
from plugins.client import PluginRPCError
from plugins.contracts import Resource as PluginResource
from tests.media_file_helpers import FakeConnection, Harness, build_harness

CONNECTION_ID = "connection-1"
ACTOR_ID = "admin-1"


def _folder(ref: str) -> PluginResource:
    parent = ref.rsplit("/", 1)[0] if "/" in ref else None
    name = ref.rsplit("/", 1)[-1]
    return PluginResource(id=ref, type="folder", name=name, parent_id=parent)


def _file(ref: str, size: int = 3) -> PluginResource:
    parent = ref.rsplit("/", 1)[0] if "/" in ref else None
    return PluginResource(
        id=ref,
        type="file",
        name=ref.rsplit("/", 1)[-1],
        parent_id=parent,
        size=size,
        content_type="text/plain",
    )


def _onedrive_like() -> dict[str | None, list[PluginResource]]:
    """Root order matters: the walk is LIFO, so the vault is listed first."""
    return {
        None: [_folder("Documents"), _file("a.txt"), _folder("Personal Vault")],
        "Documents": [_file("Documents/b.txt")],
        "Personal Vault": [_file("Personal Vault/secret.txt")],
    }


def _rpc(
    status: int | None,
    *,
    code: str | None = None,
    transport: str | None = None,
) -> PluginRPCError:
    return PluginRPCError(
        f"Plugin call GET /resources returned {status}",
        status_code=status,
        code=code,
        transport_error=transport,
    )


VAULT_REFUSED = _rpc(422, code="ResourceUnavailableError")


@pytest_asyncio.fixture
async def harness(tmp_path: Path) -> Harness:
    built = await build_harness(
        tmp_path,
        FakeConnection(uid=CONNECTION_ID, name="My OneDrive", import_existing=True),
    )
    yield built
    await built.engine.dispose()


async def _sync(harness: Harness, **options: Any) -> dict[str, Any]:  # noqa: ANN401
    return await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID, **options,
    )


async def _objects(harness: Harness) -> dict[str, StorageObjectRecord]:
    rows = await harness.objects.list(provider_connection_id=CONNECTION_ID)
    return {row.content_reference: row for row in rows}


def _listed(harness: Harness, parent: str | None) -> int:
    return sum(1 for call in harness.plugins.plugin_calls("list") if call[2] == parent)


# ----------------------------------------------------------------------
# One bad folder no longer aborts the walk
# ----------------------------------------------------------------------


async def test_unlistable_folder_is_skipped_and_the_rest_is_imported(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _onedrive_like()
    harness.plugins.list_errors["Personal Vault"] = VAULT_REFUSED

    result = await _sync(harness)

    objects = await _objects(harness)
    assert {"Documents", "Documents/b.txt", "a.txt", "Personal Vault"} <= set(objects)
    assert result["imported"] == 4
    assert result["failed_folders"] == 1
    assert result["missing"] == 0
    assert result["failures"][0]["folder"] == "Personal Vault"
    assert result["failures"][0]["status"] == 422
    vault = objects["Personal Vault"]
    assert vault.list_error_status == 422
    assert vault.list_error_at is not None


async def test_refused_folder_is_not_asked_again_until_the_retry_window_passes(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _onedrive_like()
    harness.plugins.list_errors["Personal Vault"] = VAULT_REFUSED
    await _sync(harness)
    assert _listed(harness, "Personal Vault") == 1

    second = await _sync(harness)
    assert _listed(harness, "Personal Vault") == 1  # skipped, not asked
    assert second["skipped_folders"] == 1
    assert second["failed_folders"] == 0
    assert second["missing"] == 0

    vault = (await _objects(harness))["Personal Vault"]
    retry_after = services.UNLISTABLE_FOLDER_RETRY_AFTER
    expired = datetime.now() - retry_after - timedelta(hours=1)
    await harness.objects.update(vault.uid, {"list_error_at": expired})
    await _sync(harness)
    assert _listed(harness, "Personal Vault") == 2


async def test_transient_folder_failure_is_retried_and_cleared_on_success(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _onedrive_like()
    harness.plugins.list_errors["Documents"] = _rpc(None, transport="ReadTimeout")
    first = await _sync(harness)
    assert first["failed_folders"] == 1
    documents = (await _objects(harness))["Documents"]
    assert documents.list_error is not None  # recorded for Storage browse
    assert documents.list_error_status is None  # but not a reason to skip

    harness.plugins.list_errors.clear()
    second = await _sync(harness)
    assert second["failed_folders"] == 0
    assert _listed(harness, "Documents") == 2
    documents = (await _objects(harness))["Documents"]
    assert documents.list_error is None
    assert documents.list_error_at is None
    assert "Documents/b.txt" in await _objects(harness)


# ----------------------------------------------------------------------
# A partial walk never marks what it did not see
# ----------------------------------------------------------------------


async def test_partial_walk_marks_missing_only_outside_unlisted_folders(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _onedrive_like()
    await _sync(harness)
    assert (await _objects(harness))["Personal Vault/secret.txt"].status == "active"

    # The vault starts failing (provider error) and a.txt is deleted remotely.
    harness.plugins.listings[CONNECTION_ID][None] = [
        _folder("Documents"), _folder("Personal Vault"),
    ]
    harness.plugins.list_errors["Personal Vault"] = _rpc(
        502, code="PluginBackendError",
    )
    result = await _sync(harness)

    objects = await _objects(harness)
    assert result["missing"] == 1
    assert objects["a.txt"].status == "missing"
    secret = objects["Personal Vault/secret.txt"]
    assert secret.status == "active"
    linked = await harness.files.get_by_storage_object(secret.uid)
    assert linked is not None
    assert linked.is_deleted is False


async def test_unlisted_folder_scope_matches_whole_path_segments(
    harness: Harness,
) -> None:
    """`Work/Hacker Space 2` is not inside `Work/Hacker Space`."""
    harness.plugins.listings[CONNECTION_ID] = {
        None: [_folder("Work")],
        "Work": [_folder("Work/Hacker Space"), _folder("Work/Hacker Space 2")],
        "Work/Hacker Space": [_file("Work/Hacker Space/x.txt")],
        "Work/Hacker Space 2": [_file("Work/Hacker Space 2/y.txt")],
    }
    await _sync(harness)

    harness.plugins.listings[CONNECTION_ID]["Work/Hacker Space 2"] = []
    harness.plugins.list_errors["Work/Hacker Space"] = _rpc(
        None, transport="ReadTimeout",
    )
    result = await _sync(harness)

    objects = await _objects(harness)
    assert objects["Work/Hacker Space/x.txt"].status == "active"
    assert objects["Work/Hacker Space 2/y.txt"].status == "missing"
    assert result["missing"] == 1


async def test_descendants_of_an_unlisted_folder_are_protected_at_any_depth(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = {
        None: [_folder("Media")],
        "Media": [_folder("Media/DCIM")],
        "Media/DCIM": [_folder("Media/DCIM/Camera")],
        "Media/DCIM/Camera": [_file("Media/DCIM/Camera/1.jpg")],
    }
    await _sync(harness)

    harness.plugins.list_errors["Media"] = _rpc(None, transport="ReadTimeout")
    result = await _sync(harness)

    assert result["missing"] == 0
    objects = await _objects(harness)
    assert all(row.status == "active" for row in objects.values())


# ----------------------------------------------------------------------
# Connection-level failures still stop the walk
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        _rpc(401, code="ProviderAuthError"),
        _rpc(400, code="ConnectionFailedError"),
        _rpc(403),
        _rpc(None, transport="ConnectError"),
        _rpc(None, transport="PoolTimeout"),
        RuntimeError("plugin crashed"),
    ],
    ids=["401", "400", "403", "connect-error", "pool-timeout", "unexpected"],
)
async def test_connection_level_failure_in_a_folder_stops_the_walk(
    harness: Harness, error: Exception,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _onedrive_like()
    await _sync(harness)

    harness.plugins.listings[CONNECTION_ID][None] = [_folder("Documents")]
    harness.plugins.list_errors["Documents"] = error
    with pytest.raises(type(error)):
        await _sync(harness)

    objects = await _objects(harness)
    assert objects["a.txt"].status == "active"  # removed remotely, not marked
    assert objects["Personal Vault"].status == "active"


async def test_root_listing_failure_stops_the_walk(harness: Harness) -> None:
    harness.plugins.listings[CONNECTION_ID] = _onedrive_like()
    await _sync(harness)

    harness.plugins.list_errors[None] = _rpc(None, transport="ReadTimeout")
    with pytest.raises(PluginRPCError):
        await _sync(harness)
    assert all(row.status == "active" for row in (await _objects(harness)).values())


async def test_consecutive_folder_failures_trip_the_circuit_breaker(
    harness: Harness,
) -> None:
    count = services.WALK_MAX_CONSECUTIVE_FOLDER_FAILURES + 2
    folders = [_folder(f"f{index}") for index in range(count)]
    harness.plugins.listings[CONNECTION_ID] = {None: [*folders, _file("a.txt")]}
    await _sync(harness)

    harness.plugins.listings[CONNECTION_ID] = {None: folders}  # a.txt removed
    for folder in folders:
        harness.plugins.list_errors[folder.id] = _rpc(502)
    with pytest.raises(services.SyncWalkAbortedError, match="in a row"):
        await _sync(harness)

    assert len(harness.plugins.plugin_calls("list")) == (
        1 + count  # first pass: root + every folder
        + 1 + services.WALK_MAX_CONSECUTIVE_FOLDER_FAILURES
    )
    assert (await _objects(harness))["a.txt"].status == "active"


async def test_a_success_resets_the_consecutive_failure_count(harness: Harness) -> None:
    limit = services.WALK_MAX_CONSECUTIVE_FOLDER_FAILURES
    # LIFO walk: listed in reverse, so good/bad folders alternate.
    folders = [_folder(f"f{index:02d}") for index in range(2 * limit)]
    harness.plugins.listings[CONNECTION_ID] = {None: folders}
    for folder in folders[::2]:
        harness.plugins.list_errors[folder.id] = _rpc(502)

    result = await _sync(harness)
    assert result["failed_folders"] == limit


# ----------------------------------------------------------------------
# Mass missing waits for the owner
# ----------------------------------------------------------------------


async def test_mass_missing_is_held_until_the_owner_confirms_the_count(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(services, "MISSING_HOLD_MIN_COUNT", 3)
    files = [_file(f"old/{index}.txt") for index in range(5)]
    harness.plugins.listings[CONNECTION_ID] = {
        None: [_folder("old"), _file("keep.txt")],
        "old": files,
    }
    await _sync(harness)

    # The whole `old` tree disappears at the provider: 6 rows would go.
    harness.plugins.listings[CONNECTION_ID] = {None: [_file("keep.txt")]}
    held = await _sync(harness)
    assert held["missing"] == 0
    assert held["missing_held"] == 6
    objects = await _objects(harness)
    assert all(row.status == "active" for row in objects.values())
    for row in objects.values():
        linked = await harness.files.get_by_storage_object(row.uid)
        assert linked is not None
        assert linked.is_deleted is False

    too_small = await _sync(harness, confirm_missing=5)
    assert too_small["missing"] == 0
    assert too_small["missing_held"] == 6

    confirmed = await _sync(harness, confirm_missing=6)
    assert confirmed["missing"] == 6
    assert confirmed["missing_held"] == 0
    objects = await _objects(harness)
    assert objects["old/0.txt"].status == "missing"
    assert objects["keep.txt"].status == "active"


async def test_missing_below_the_hold_limit_is_applied_without_confirmation(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _onedrive_like()
    await _sync(harness)
    harness.plugins.listings[CONNECTION_ID][None] = [
        _folder("Documents"), _folder("Personal Vault"),
    ]
    result = await _sync(harness)
    assert result["missing"] == 1
    assert result["missing_held"] == 0


# ----------------------------------------------------------------------
# The outcome is visible: last_error, notification, hash backfill
# ----------------------------------------------------------------------


async def _persist_connection(harness: Harness) -> None:
    await ProviderConnectionRepository(harness.session_factory).create({
        "uid": CONNECTION_ID,
        "owner_id": ACTOR_ID,
        "provider_type": "onedrive",
        "name": "My OneDrive",
        "encrypted_config": "cipher-text",
        "status": "configured",
        "enabled": True,
        "import_existing": True,
    })


def _state(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setattr(
        "apps.media_files.factory.build_media_file_service_from_state",
        lambda _state: harness.service,
    )
    return SimpleNamespace(session_factory=harness.session_factory)


async def _last_error(harness: Harness) -> str | None:
    connection = await ProviderConnectionRepository(harness.session_factory).get(
        CONNECTION_ID,
    )
    return connection.last_error


async def _notifications(harness: Harness) -> list[Any]:
    repository = NotificationRepository(harness.session_factory)
    return await repository.list_for_owner(ACTOR_ID)


async def test_incomplete_sync_sets_last_error_and_notifies_once_per_problem(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _persist_connection(harness)
    state = _state(harness, monkeypatch)
    harness.plugins.listings[CONNECTION_ID] = _onedrive_like()
    harness.plugins.list_errors["Personal Vault"] = VAULT_REFUSED

    await run_inbound_sync(state, CONNECTION_ID, ACTOR_ID, trigger="poll")
    await run_inbound_sync(state, CONNECTION_ID, ACTOR_ID, trigger="poll")

    last_error = await _last_error(harness)
    assert last_error is not None
    assert "Sync incomplete" in last_error
    assert "'Personal Vault'" in last_error
    notifications = await _notifications(harness)
    assert len(notifications) == 1
    assert notifications[0].operation == "sync"
    assert notifications[0].item_name == "My OneDrive"

    # A manual run reports every time it is asked.
    await run_inbound_sync(state, CONNECTION_ID, ACTOR_ID, trigger="manual")
    assert len(await _notifications(harness)) == 2

    harness.plugins.list_errors.clear()
    await harness.objects.update(
        (await _objects(harness))["Personal Vault"].uid,
        {"list_error": None, "list_error_status": None, "list_error_at": None},
    )
    await run_inbound_sync(state, CONNECTION_ID, ACTOR_ID, trigger="poll")
    assert await _last_error(harness) is None


async def test_failed_sync_sets_last_error_and_still_raises(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _persist_connection(harness)
    state = _state(harness, monkeypatch)
    harness.plugins.list_errors[None] = _rpc(401, code="ProviderAuthError")

    with pytest.raises(PluginRPCError):
        await run_inbound_sync(state, CONNECTION_ID, ACTOR_ID, trigger="poll")

    last_error = await _last_error(harness)
    assert last_error is not None
    assert last_error.startswith("Sync failed: PluginRPCError")
    assert len(await _notifications(harness)) == 1


async def test_held_missing_is_reported_with_the_count_to_confirm(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(services, "MISSING_HOLD_MIN_COUNT", 1)
    await _persist_connection(harness)
    state = _state(harness, monkeypatch)
    harness.plugins.listings[CONNECTION_ID] = _onedrive_like()
    await run_inbound_sync(state, CONNECTION_ID, ACTOR_ID, trigger="poll")

    harness.plugins.listings[CONNECTION_ID] = {None: []}
    result = await run_inbound_sync(state, CONNECTION_ID, ACTOR_ID, trigger="poll")

    assert result["missing_held"] == 5
    assert "confirm_missing=5" in (await _last_error(harness))


async def test_poller_still_schedules_hash_backfill_after_an_incomplete_walk(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.media_files import worker

    await _persist_connection(harness)
    state = _state(harness, monkeypatch)
    state.active_syncs = set()
    state.auth_service = SimpleNamespace(list_users=_no_users)
    backfills: list[str] = []
    monkeypatch.setattr(
        worker,
        "schedule_content_hash_backfill",
        lambda _state, uid: backfills.append(uid),
    )
    harness.plugins.listings[CONNECTION_ID] = _onedrive_like()
    harness.plugins.list_errors["Personal Vault"] = VAULT_REFUSED
    await harness.service.import_from_provider(CONNECTION_ID, actor_user_id=ACTOR_ID)

    assert await worker.poll_inbound_syncs(state) == 1
    assert backfills == [CONNECTION_ID]


async def _no_users() -> list[Any]:  # noqa: RUF029 -- matches AuthService
    return []
