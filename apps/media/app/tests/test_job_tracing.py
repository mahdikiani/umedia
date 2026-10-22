"""Background jobs are traceable: one trace and one durable record per
sync walk / hash pass, the connection and run ids on every plugin call,
and the plugin's own log lines carrying the same ids.

Before, every background event in a process shared one trace_id, a failed
plugin call was never logged, and plugin tracebacks arrived as ~60 INFO
lines with nothing tying them to the core call.
"""

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import pytest_asyncio
import sentry_sdk
from sentry_sdk.transport import Transport

from apps.media_files import worker
from apps.media_files.factory import run_inbound_sync
from apps.media_files.jobs import JobRunRepository, job_run
from plugins.client import PluginClient, PluginRPCError
from plugins.contracts import Resource as PluginResource
from plugins.contracts import ResourceUnavailableError, encode_header_json
from plugins.process_manager import PluginProcessManager, log_plugin_line
from plugins.sdk import JsonLineFormatter, PluginBackend, create_plugin_app
from plugins.tracing import current_run_id
from tests.media_file_helpers import FakeConnection, Harness, build_harness

CONNECTION_ID = "connection-1"
ACTOR_ID = "admin-1"


@pytest.fixture(autouse=True)
def _loggers_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """`server.server` configures logging with `dictConfig`, which disables
    every logger created before it (these included). In production uvicorn's
    own logging config, applied right after, enables them again; under
    pytest nothing does, so caplog would see nothing."""
    for name in ("plugins.client", "plugins.process_manager", "plugins.sdk"):
        monkeypatch.setattr(logging.getLogger(name), "disabled", False)


@pytest_asyncio.fixture
async def harness(tmp_path: Path) -> Harness:
    built = await build_harness(
        tmp_path,
        FakeConnection(uid=CONNECTION_ID, name="My OneDrive", import_existing=True),
    )
    yield built
    await built.engine.dispose()


# ----------------------------------------------------------------------
# Core -> plugin: ids on every call
# ----------------------------------------------------------------------


async def test_plugin_calls_carry_connection_run_and_request_ids(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    seen: list[bytes] = []
    body = b'{"code":"PluginBackendError","message":"provider down"}'

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        seen.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(
            b"HTTP/1.1 502 Bad Gateway\r\ncontent-type: application/json\r\n"
            b"content-length: " + str(len(body)).encode() + b"\r\n\r\n" + body,
        )
        await writer.drain()
        writer.close()

    socket_path = tmp_path / "plugin.sock"
    token = current_run_id.set("run-123")
    try:
        server = await asyncio.start_unix_server(handle, path=str(socket_path))
        async with server:
            client = PluginClient(socket_path, connection_id=CONNECTION_ID)
            with (
                caplog.at_level(logging.INFO, logger="plugins.client"),
                pytest.raises(PluginRPCError) as caught,
            ):
                await client.list_resources({}, parent_id="Documents")
    finally:
        current_run_id.reset(token)

    head = seen[0].decode().lower()
    assert f"x-umedia-connection-id: {CONNECTION_ID}" in head
    assert "x-umedia-run-id: run-123" in head
    request_id = caught.value.trace["request_id"]
    assert f"x-umedia-request-id: {request_id}" in head
    assert caught.value.trace["connection_id"] == CONNECTION_ID
    assert caught.value.trace["run_id"] == "run-123"

    failure = next(r for r in caplog.records if "failed after" in r.getMessage())
    assert failure.levelno == logging.WARNING
    assert failure.connection_id == CONNECTION_ID
    assert failure.request_id == request_id
    assert failure.duration_ms >= 0


# ----------------------------------------------------------------------
# Plugin side: its log lines carry the ids
# ----------------------------------------------------------------------


class _VaultBackend(PluginBackend):
    async def connect(self, config: dict) -> None: ...

    async def list_resources(self, config: dict, *, parent_id: str | None) -> list:
        logging.getLogger("plugins.test").warning("listing %s", parent_id)
        raise ResourceUnavailableError("invalidResourceId: ObjectHandle is Invalid")

    async def get_resource(self, config: dict, resource_id: str) -> PluginResource: ...

    async def read_content(
        self, config: dict, resource_id: str, *, range_header: str | None,
    ) -> Any:  # noqa: ANN401
        yield b""

    async def create_resource(
        self, config: dict, metadata: Any, *, content: Any,  # noqa: ANN401
    ) -> PluginResource: ...

    async def update_resource(
        self, config: dict, resource_id: str, changes: Any, *, content: Any,  # noqa: ANN401
    ) -> PluginResource: ...

    async def delete_resource(self, config: dict, resource_id: str) -> None: ...


class _Lines(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.setFormatter(JsonLineFormatter())
        self.lines: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(json.loads(self.format(record)))


async def test_plugin_log_lines_carry_the_core_ids() -> None:
    lines = _Lines()
    plugins_logger = logging.getLogger("plugins")
    plugins_logger.addHandler(lines)
    previous_level = plugins_logger.level
    plugins_logger.setLevel(logging.INFO)
    try:
        app = create_plugin_app(_VaultBackend())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://plugin",
        ) as client:
            response = await client.get(
                "/resources",
                params={"parent_id": "Personal Vault"},
                headers={
                    "X-Umedia-Connection-Config": encode_header_json({}),
                    "X-Umedia-Request-Id": "req-1",
                    "X-Umedia-Connection-Id": CONNECTION_ID,
                    "X-Umedia-Run-Id": "run-9",
                },
            )
    finally:
        plugins_logger.removeHandler(lines)
        plugins_logger.setLevel(previous_level)

    assert response.status_code == 422
    assert {line["message"] for line in lines.lines} >= {"listing Personal Vault"}
    sdk_line = next(line for line in lines.lines if "-> 422" in line["message"])
    for line in (lines.lines[0], sdk_line):
        assert line["request_id"] == "req-1"
        assert line["connection_id"] == CONNECTION_ID
        assert line["run_id"] == "run-9"
        assert line["level"] == "WARNING"


def test_core_relogs_plugin_json_lines_with_level_ids_and_one_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    line = json.dumps({
        "level": "ERROR",
        "logger": "uvicorn.error",
        "message": "GET /resources/x/content failed",
        "exc": "Traceback (most recent call last):\n  ...\nRuntimeError: boom",
        "request_id": "req-7",
        "connection_id": CONNECTION_ID,
    }).encode()
    with caplog.at_level(logging.DEBUG, logger="plugins.process_manager"):
        log_plugin_line("rclone", "stderr", line + b"\n")
        log_plugin_line("telegram", "stderr", b"TgCrypto is missing!\n")

    structured, plain = caplog.records[-2:]
    # A plugin ERROR is context for the core's own error, not a second event.
    assert structured.levelno == logging.WARNING
    assert "RuntimeError: boom" in structured.getMessage()
    assert "request_id=req-7" in structured.getMessage()
    assert structured.connection_id == CONNECTION_ID
    assert plain.levelno == logging.INFO
    assert "TgCrypto is missing!" in plain.getMessage()


async def test_an_overlong_plugin_line_does_not_stop_the_drain(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stream = asyncio.StreamReader(limit=128)
    stream.feed_data(b"x" * 1000 + b"\n" + b"after the long line\n")
    stream.feed_eof()
    with caplog.at_level(logging.INFO, logger="plugins.process_manager"):
        await asyncio.wait_for(
            PluginProcessManager._drain_stream("rclone", stream, "stderr"),
            timeout=5,
        )
    assert any("after the long line" in r.getMessage() for r in caplog.records)


# ----------------------------------------------------------------------
# One trace and one record per run
# ----------------------------------------------------------------------


class _Events:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, event: dict[str, Any]) -> None:
        self.events.append(event)


@pytest.fixture
def sentry_events() -> _Events:
    events = _Events()

    class _Transport(Transport):
        def capture_envelope(self, envelope: Any) -> None:  # noqa: ANN401
            for item in envelope.items:
                if item.type == "event":
                    events(item.payload.json)

    previous = sentry_sdk.get_client()
    sentry_sdk.init(
        dsn="https://key@sentry.invalid/1",
        transport=_Transport,
        default_integrations=False,
        auto_enabling_integrations=False,
    )
    yield events
    sentry_sdk.get_client().close()
    # Back to the client from before. A bare `sentry_sdk.init()` would
    # install the default integrations (threading, logging, ...) for the
    # rest of the session.
    sentry_sdk.get_global_scope().set_client(previous)


async def test_each_run_gets_its_own_trace_tags_and_run_id(
    harness: Harness, sentry_events: _Events,
) -> None:
    state = SimpleNamespace(session_factory=harness.session_factory)
    seen_run_ids: list[str | None] = []
    for trigger in ("poll", "manual"):
        async with job_run(
            state, kind="sync", connection_id=CONNECTION_ID, trigger=trigger,
        ) as run:
            seen_run_ids.append(current_run_id.get())
            assert seen_run_ids[-1] == run.run_id
            sentry_sdk.capture_message(f"walk problem ({trigger})")
    assert current_run_id.get() is None

    first, second = sentry_events.events
    assert first["tags"]["job.run_id"] == seen_run_ids[0]
    assert second["tags"]["job.run_id"] == seen_run_ids[1]
    assert first["tags"]["connection_id"] == CONNECTION_ID
    assert first["tags"]["job.kind"] == "sync"
    assert second["tags"]["job.trigger"] == "manual"
    assert (
        first["contexts"]["trace"]["trace_id"]
        != second["contexts"]["trace"]["trace_id"]
    )


async def test_runs_are_recorded_with_status_result_and_error(
    harness: Harness,
) -> None:
    state = SimpleNamespace(session_factory=harness.session_factory)
    async with job_run(
        state, kind="sync", connection_id=CONNECTION_ID, trigger="poll",
    ) as run:
        run.result = {"seen": 3, "failed_folders": 1}
        run.partial = True
    with pytest.raises(RuntimeError, match="token expired"):
        async with job_run(
            state, kind="sync", connection_id=CONNECTION_ID, trigger="poll",
        ):
            raise RuntimeError("token expired")
    async with job_run(state, kind="hash", connection_id=CONNECTION_ID, trigger="x"):
        pass

    runs = await JobRunRepository(harness.session_factory).list_for_connection(
        CONNECTION_ID, kind="sync",
    )
    failed, partial = runs  # newest first
    assert failed.status == "failed"
    assert failed.error == "RuntimeError: token expired"
    assert failed.finished_at is not None
    assert partial.status == "partial"
    assert partial.result == {"seen": 3, "failed_folders": 1}
    assert partial.duration_ms is not None
    assert len(await JobRunRepository(harness.session_factory).list_for_connection(
        CONNECTION_ID,
    )) == 3


async def test_a_sync_walk_is_one_run_and_its_plugin_calls_carry_its_id(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "apps.media_files.factory.build_media_file_service_from_state",
        lambda _state: harness.service,
    )
    listed_under: list[str | None] = []
    original = harness.plugins.list_resources

    async def list_resources(*args: Any, **kwargs: Any) -> list[PluginResource]:  # noqa: ANN401
        listed_under.append(current_run_id.get())
        return await original(*args, **kwargs)

    harness.plugins.list_resources = list_resources
    harness.plugins.listings[CONNECTION_ID] = {
        None: [
            PluginResource(id="Personal Vault", type="folder", name="Personal Vault"),
        ],
    }
    harness.plugins.list_errors["Personal Vault"] = PluginRPCError(
        "returned 422", status_code=422, code="ResourceUnavailableError",
    )
    state = SimpleNamespace(session_factory=harness.session_factory)

    result = await run_inbound_sync(
        state, CONNECTION_ID, ACTOR_ID, trigger="poll", run_id="run-abc",
    )

    assert result["failed_folders"] == 1
    assert listed_under == ["run-abc", "run-abc"]
    (run,) = await JobRunRepository(harness.session_factory).list_for_connection(
        CONNECTION_ID,
    )
    assert run.uid == "run-abc"
    assert run.kind == "sync"
    assert run.trigger == "poll"
    assert run.status == "partial"
    assert run.result["failures"][0]["folder"] == "Personal Vault"


async def test_a_hash_pass_is_recorded_as_its_own_run(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = {
        None: [PluginResource(id="a.txt", type="file", name="a.txt", size=3)],
    }
    harness.plugins.store["a.txt"] = b"abc"
    await harness.service.import_from_provider(CONNECTION_ID, actor_user_id=ACTOR_ID)
    monkeypatch.setattr(
        worker, "build_media_file_service_from_state", lambda _state: harness.service,
    )
    state = SimpleNamespace(session_factory=harness.session_factory, hash_tasks={})

    worker.schedule_content_hash_backfill(state, CONNECTION_ID)
    await state.hash_tasks[CONNECTION_ID]

    (run,) = await JobRunRepository(harness.session_factory).list_for_connection(
        CONNECTION_ID, kind="hash",
    )
    assert run.status == "ok"
    assert run.result == {"hashed": 1, "failed": 0, "in_backoff": 0, "unreadable": 0}
