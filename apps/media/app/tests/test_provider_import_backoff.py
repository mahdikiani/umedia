"""A provider that keeps failing must not abort every import or report a
new error event on every poll (GlitchTip UMEDIA-4 / UMEDIA-6).

- UMEDIA-4: OneDrive's "Personal Vault" folder cannot be listed
  (`invalidResourceId: ObjectHandle is Invalid`), which aborted the whole
  walk at that folder on every 15-minute poll.
- UMEDIA-6: a Google Drive photo folder took longer than the 10s RPC
  read timeout on every attempt.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
import pytest_asyncio

from apps.auth.schemas import UserSummary
from apps.media_files import factory, import_sync, worker
from apps.provider_connections.models import ProviderConnection
from apps.provider_connections.repository import ProviderConnectionRepository
from plugins.client import LIST_TIMEOUT, PluginClient, PluginRPCError
from plugins.contracts import Resource as PluginResource
from plugins.rclone.backend import RC_LIST_TIMEOUT_SECONDS, RcloneBackend
from tests.media_file_helpers import FakeConnection, Harness, build_harness

CONNECTION_ID = "connection-1"
ACTOR_ID = "admin-1"

VAULT_ERROR = PluginRPCError(
    'Plugin call GET /resources returned 502: {"code":"PluginBackendError",'
    '"message":"error in ListJSON: couldn\'t list files: invalidRequest: '
    'invalidResourceId: ObjectHandle is Invalid"}',
    status_code=502,
    code="PluginBackendError",
)
TIMEOUT_ERROR = PluginRPCError("Plugin call GET /resources failed: ReadTimeout: ")


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


def _listing() -> dict[str | None, list[PluginResource]]:
    return {
        None: [
            _folder("Personal Vault", None),
            _folder("Photos", None),
            _file("a.txt", None),
        ],
        "Personal Vault": [
            _folder("Personal Vault/deep", "Personal Vault"),
        ],
        "Personal Vault/deep": [
            _file("Personal Vault/deep/secret.txt", "Personal Vault/deep"),
        ],
        "Photos": [_file("Photos/p.jpg", "Photos")],
    }


@pytest_asyncio.fixture
async def harness(tmp_path: Path) -> Harness:
    built = await build_harness(
        tmp_path,
        FakeConnection(uid=CONNECTION_ID, name="Drive", import_existing=True),
    )
    yield built
    await built.engine.dispose()


async def _status(harness: Harness, ref: str) -> str:
    objects = await harness.objects.list(provider_connection_id=CONNECTION_ID)
    return next(o.status for o in objects if o.content_reference == ref)


# ----------------------------------------------------------------------
# Walk: one unlistable sub-folder is skipped, not fatal
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unlistable_subfolder_is_skipped_and_the_walk_goes_on(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = Mock()
    monkeypatch.setattr(import_sync, "logger", log)
    listing = _listing()
    harness.plugins.listings[CONNECTION_ID] = listing
    harness.plugins.list_errors["Personal Vault"] = VAULT_ERROR
    # A file added after the failing folder must still be imported.
    listing["Photos"].append(_file("Photos/new.jpg", "Photos"))

    result = await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )

    assert result["skipped"] == 1
    assert await _status(harness, "Photos/new.jpg") == "active"
    assert await _status(harness, "Personal Vault") == "active"
    log.exception.assert_not_called()
    log.error.assert_not_called()
    log.warning.assert_called_once()
    assert "Personal Vault" in log.warning.call_args.args


@pytest.mark.asyncio
async def test_objects_under_a_skipped_folder_are_not_marked_missing(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _listing()
    await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )
    assert await _status(harness, "Personal Vault/deep/secret.txt") == "active"

    # The vault becomes unlistable, and a regular file really disappears.
    harness.plugins.list_errors["Personal Vault"] = VAULT_ERROR
    harness.plugins.listings[CONNECTION_ID]["Photos"] = []
    result = await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )

    assert result["skipped"] == 1
    assert result["missing"] == 1
    assert await _status(harness, "Photos/p.jpg") == "missing"
    assert await _status(harness, "Personal Vault/deep") == "active"
    assert await _status(harness, "Personal Vault/deep/secret.txt") == "active"


@pytest.mark.asyncio
async def test_root_listing_failure_still_aborts_without_marking_missing(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _listing()
    await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )
    harness.plugins.list_errors[None] = VAULT_ERROR

    with pytest.raises(PluginRPCError):
        await harness.service.import_from_provider(
            CONNECTION_ID, actor_user_id=ACTOR_ID,
        )
    assert await _status(harness, "a.txt") == "active"


# ----------------------------------------------------------------------
# Listing timeouts
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_lists_with_the_long_read_timeout(tmp_path: Path) -> None:
    client = PluginClient(tmp_path / "plugin.sock")
    response = SimpleNamespace(json=list)
    request = AsyncMock(return_value=response)
    with patch.object(client, "_request", request):
        await client.list_resources({}, parent_id="Photos")

    assert request.await_args.kwargs["timeout"] is LIST_TIMEOUT
    assert request.await_args.kwargs["retry"] is True
    assert LIST_TIMEOUT.read is not None
    assert LIST_TIMEOUT.read > RC_LIST_TIMEOUT_SECONDS
    assert LIST_TIMEOUT.connect == 10.0


@pytest.mark.asyncio
async def test_rclone_list_uses_its_own_longer_rc_timeout() -> None:
    backend = RcloneBackend()
    rc_call = AsyncMock(return_value={"list": []})
    with (
        patch("plugins.rclone.backend._build_fs", return_value="remote:"),
        patch.object(backend, "_rc_call", rc_call),
    ):
        await backend.list_resources({"remote_type": "drive"}, parent_id="x")

    assert rc_call.await_args.kwargs["http_timeout"] == RC_LIST_TIMEOUT_SECONDS


# ----------------------------------------------------------------------
# Connection health: the owner sees "Needs attention"
# ----------------------------------------------------------------------


async def _persist_connection(harness: Harness) -> ProviderConnection:
    return await ProviderConnectionRepository(harness.session_factory).create({
        "owner_id": ACTOR_ID,
        "provider_type": "onedrive",
        "name": "OneDrive",
        "encrypted_config": "cipher-text",
        "status": "configured",
        "enabled": True,
        "import_existing": True,
    })


def _service(error: Exception | None) -> SimpleNamespace:
    async def _import(_uid: str, *, actor_user_id: str) -> dict[str, int]:  # noqa: RUF029
        del actor_user_id
        if error is not None:
            raise error
        return {"seen": 0}

    return SimpleNamespace(import_from_provider=_import)


@pytest.mark.asyncio
async def test_failed_sync_marks_connection_and_success_clears_it(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = await _persist_connection(harness)
    repo = ProviderConnectionRepository(harness.session_factory)
    state = SimpleNamespace(
        session_factory=harness.session_factory,
        sync_failures={connection.uid: worker.SyncFailure()},
    )

    monkeypatch.setattr(
        factory, "build_media_file_service_from_state",
        lambda _state: _service(VAULT_ERROR),
    )
    with pytest.raises(PluginRPCError):
        await factory.run_inbound_sync(state, connection.uid, ACTOR_ID)
    broken = await repo.get(connection.uid)
    assert broken.status == factory.SYNC_ERROR_STATUS
    assert "ObjectHandle is Invalid" in broken.last_error

    monkeypatch.setattr(
        factory, "build_media_file_service_from_state",
        lambda _state: _service(None),
    )
    await factory.run_inbound_sync(state, connection.uid, ACTOR_ID)
    healed = await repo.get(connection.uid)
    assert healed.status == "configured"
    assert healed.last_error is None
    assert state.sync_failures == {}


@pytest.mark.asyncio
async def test_non_plugin_errors_do_not_mark_the_connection(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = await _persist_connection(harness)
    monkeypatch.setattr(
        factory, "build_media_file_service_from_state",
        lambda _state: _service(RuntimeError("bug in our code")),
    )
    state = SimpleNamespace(session_factory=harness.session_factory)
    with pytest.raises(RuntimeError):
        await factory.run_inbound_sync(state, connection.uid, ACTOR_ID)
    current = await ProviderConnectionRepository(harness.session_factory).get(
        connection.uid,
    )
    assert current.status == "configured"


# ----------------------------------------------------------------------
# Poller backoff: one error event, then warnings
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


def _poll_setup(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    outcomes: list[Exception | None],
) -> tuple[SimpleNamespace, _Clock, list[str]]:
    calls: list[str] = []

    async def _fake_run(  # noqa: RUF029
        _state: object, connection_uid: str, _actor: str,
    ) -> dict[str, Any]:
        calls.append(connection_uid)
        outcome = outcomes.pop(0)
        if outcome is not None:
            raise outcome
        return {"seen": 1}

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
    return state, clock, calls


@pytest.mark.asyncio
async def test_poller_backs_off_and_reports_a_permanent_error_once(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = await _persist_connection(harness)
    state, clock, calls = _poll_setup(
        harness, monkeypatch, [VAULT_ERROR, VAULT_ERROR, None],
    )
    log = Mock()
    monkeypatch.setattr(worker, "logger", log)

    assert await worker.poll_inbound_syncs(state) == 0
    assert log.exception.call_count == 1

    # Next tick (15 min later) is inside the 30 min backoff: skipped.
    clock.now += 900
    await worker.poll_inbound_syncs(state)
    assert calls == [connection.uid]

    # After the backoff it retries; the same error is only a warning and
    # the next wait doubles.
    clock.now += 1000
    await worker.poll_inbound_syncs(state)
    assert calls == [connection.uid, connection.uid]
    assert log.exception.call_count == 1
    assert log.warning.call_count == 1
    failure = state.sync_failures[connection.uid]
    assert failure.attempts == 2
    assert failure.retry_at == clock.now + 2 * worker.SYNC_RETRY_BASE_SECONDS

    # A success clears the backoff.
    clock.now += 2 * worker.SYNC_RETRY_BASE_SECONDS
    assert await worker.poll_inbound_syncs(state) == 1
    assert state.sync_failures == {}


@pytest.mark.asyncio
async def test_poller_reports_a_timeout_only_after_repeated_failures(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _persist_connection(harness)
    state, clock, _calls = _poll_setup(
        harness, monkeypatch, [TIMEOUT_ERROR] * 4,
    )
    log = Mock()
    monkeypatch.setattr(worker, "logger", log)

    for _ in range(worker.TRANSIENT_REPORT_AFTER - 1):
        await worker.poll_inbound_syncs(state)
        clock.now += worker.SYNC_RETRY_MAX_SECONDS
    log.exception.assert_not_called()
    assert log.warning.call_count == worker.TRANSIENT_REPORT_AFTER - 1

    await worker.poll_inbound_syncs(state)
    assert log.exception.call_count == 1

    clock.now += worker.SYNC_RETRY_MAX_SECONDS
    await worker.poll_inbound_syncs(state)
    assert log.exception.call_count == 1


def test_backoff_is_capped_at_a_day() -> None:
    failures: dict[str, worker.SyncFailure] = {}
    for _ in range(20):
        failure, _report = worker._note_sync_failure(
            failures, "c", VAULT_ERROR, 0.0,
        )
    assert failure.retry_at == worker.SYNC_RETRY_MAX_SECONDS
