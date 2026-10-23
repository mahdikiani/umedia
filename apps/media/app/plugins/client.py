"""Core-side RPC client for calling one running provider plugin process
over its Unix domain socket.

See docs/03-provider-system.md for the contract and docs/02-architecture.md
"Correctness & consistency" #1/#3 for the timeout/retry rules this
implements: every call has an explicit timeout; reads retry with backoff
only when the request never reached the plugin (connect/pool failures),
writes never auto-retry (to avoid duplicate side effects from retrying a
slow-but-actually-succeeding write). A read that timed out waiting for the
answer is not retried either: the plugin is still working on it, so a
retry only starts the same slow provider call a second time.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from .contracts import (
    CreateResourceIn,
    Resource,
    ResourceNotFoundError,
    StatusOut,
    UpdateResourceIn,
    encode_header_json,
    encode_resource_id,
)
from .tracing import (
    CONNECTION_ID_HEADER,
    REQUEST_ID_HEADER,
    RUN_ID_HEADER,
    current_run_id,
    new_request_id,
)

CONFIG_HEADER = "X-Umedia-Connection-Config"

DEFAULT_TIMEOUT_SECONDS = 10.0
TELEGRAM_LOGIN_TIMEOUT = httpx.Timeout(
    connect=DEFAULT_TIMEOUT_SECONDS,
    read=60.0,
    write=DEFAULT_TIMEOUT_SECONDS,
    pool=DEFAULT_TIMEOUT_SECONDS,
)
READ_RETRY_ATTEMPTS = 3
READ_RETRY_BACKOFF_SECONDS = 0.5
# Transport errors where the request never reached the plugin, so asking
# again cannot duplicate work. A ReadTimeout is deliberately not here.
RETRYABLE_TRANSPORT_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
)

# Listing one folder waits on the provider's paged listing API: thousands
# of entries (a phone camera folder on Google Drive) take far longer than
# the 10s JSON-RPC default, which aborted whole sync walks (UMEDIA-6). The
# rclone plugin gives up on its own rc call at
# `plugins.rclone.backend.RC_LIST_TIMEOUT_SECONDS` (300s) and answers 504;
# this deadline is a backstop just above it.
LIST_READ_TIMEOUT_SECONDS = 330.0
LIST_TIMEOUT = httpx.Timeout(
    connect=DEFAULT_TIMEOUT_SECONDS,
    read=LIST_READ_TIMEOUT_SECONDS,
    write=DEFAULT_TIMEOUT_SECONDS,
    pool=DEFAULT_TIMEOUT_SECONDS,
)
# One resource's metadata (`GET /resources/{id}`) is one rc call, which the
# rclone plugin bounds at `plugins.rclone.backend.RC_CALL_TIMEOUT_SECONDS`
# (30s). A read timeout is not retried, so wait a little longer than that:
# giving up first would turn the post-write check of a slow but successful
# write into a reported failure.
RESOURCE_READ_TIMEOUT_SECONDS = 40.0
RESOURCE_TIMEOUT = httpx.Timeout(
    connect=DEFAULT_TIMEOUT_SECONDS,
    read=RESOURCE_READ_TIMEOUT_SECONDS,
    write=DEFAULT_TIMEOUT_SECONDS,
    pool=DEFAULT_TIMEOUT_SECONDS,
)

# Content streams (cross-storage byte copies) can run for minutes on large
# objects. Keep a short connect/pool timeout for JSON RPC, but do not apply
# the 10s default as a whole-response read deadline on `aiter_bytes`.
CONTENT_STREAM_TIMEOUT = httpx.Timeout(
    connect=DEFAULT_TIMEOUT_SECONDS,
    read=None,
    write=60.0,
    pool=DEFAULT_TIMEOUT_SECONDS,
)

# Uploads (`POST`/`PUT /resources` with a body): the plugin answers only
# after the provider has stored the bytes, so the response can take as
# long as the remote transfer itself. The 10s JSON-RPC read cap killed
# real 2-6 MB Dropbox -> Telegram copies with `httpx.ReadTimeout`; write
# is unbounded too, since a slow provider applies back-pressure while the
# request body streams. Connect/pool stay short so a dead plugin still
# fails fast. Process-level supervision (PluginProcessManager) covers a
# hung plugin.
CONTENT_WRITE_TIMEOUT = httpx.Timeout(
    connect=DEFAULT_TIMEOUT_SECONDS,
    read=None,
    write=None,
    pool=DEFAULT_TIMEOUT_SECONDS,
)
logger = logging.getLogger(__name__)


class PluginRPCError(RuntimeError):
    """A plugin call failed -- connection/timeout, or a non-2xx response."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        data: dict[str, Any] | None = None,
        transport_error: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        # The plugin's error class name and structured `data`, when the
        # plugin SDK produced the error body (see sdk.py's handler).
        self.code = code
        self.data = data or {}
        # The httpx exception class name when no response arrived at all
        # (`ReadTimeout`, `ConnectError`, ...): a slow call and a dead
        # plugin need different handling (see the sync walk).
        self.transport_error = transport_error
        # Set by `PluginClient`: the call with its query
        # (`GET /resources?parent_id=DCIM`), how long it took in total, how
        # many attempts it made, and the ids it was sent with (see
        # plugins/tracing.py) -- the plugin's own log lines carry the same.
        # The query stays out of the message: GlitchTip groups issues by
        # title, so a folder name in it would open one issue per folder.
        self.target: str | None = None
        self.elapsed_seconds: float | None = None
        self.attempts: int | None = None
        self.trace: dict[str, str] = {}


#: A successful call slower than this is logged at INFO (others at DEBUG:
#: one sync walk makes thousands of listing calls).
SLOW_CALL_SECONDS = 10.0


def _trace_text(trace: dict[str, str]) -> str:
    return " ".join(f"{key}={value}" for key, value in trace.items())


def _note_success(
    target: str, started: float, status: int, trace: dict[str, str],
) -> None:
    elapsed = time.monotonic() - started
    logger.log(
        logging.INFO if elapsed >= SLOW_CALL_SECONDS else logging.DEBUG,
        "Plugin call %s -> %d in %.0fms [%s]",
        target,
        status,
        elapsed * 1000,
        _trace_text(trace),
        extra={"plugin_call": target, "duration_ms": round(elapsed * 1000), **trace},
    )


#: Content-read answers that mean the bytes are not there, or refused for
#: good (OneDrive's Personal Vault answers 422): asking again soon will not
#: help. Both download layers answer 404 for them, and the hash backfill
#: records them instead of retrying.
PERMANENT_READ_STATUSES = frozenset({404, 422})


def is_permanent_read_failure(error: BaseException) -> bool:
    """Whether a failed content read is the file's problem for good (a
    404 to the client) rather than the provider's right now (a 502)."""
    if isinstance(error, ResourceNotFoundError):
        return True
    return (
        isinstance(error, PluginRPCError)
        and error.status_code in PERMANENT_READ_STATUSES
    )


def _note_failure(
    error: PluginRPCError,
    target: str,
    started: float,
    attempts: int,
    trace: dict[str, str] | None = None,
) -> None:
    """Attach timing and ids to a failed call and log it -- httpx only logs
    calls that got a response, so a timeout used to leave no trace at all."""
    error.target = target
    error.elapsed_seconds = round(time.monotonic() - started, 3)
    error.attempts = attempts
    error.trace = dict(trace or {})
    # A 4xx is the plugin's considered answer (not found, bad config) and
    # often expected by the caller; no answer or a 5xx is worth a warning.
    expected = error.status_code is not None and error.status_code < 500
    logger.log(
        logging.INFO if expected else logging.WARNING,
        "Plugin call %s failed after %.1fs (%d attempt(s)) [%s]: %s",
        target,
        error.elapsed_seconds,
        attempts,
        _trace_text(error.trace),
        error.transport_error or error.status_code,
        extra={
            "plugin_call": target,
            "duration_ms": round(error.elapsed_seconds * 1000),
            "attempts": attempts,
            **error.trace,
        },
    )


def _error_body(response: httpx.Response) -> tuple[str | None, dict[str, Any]]:
    try:
        body = response.json()
    except ValueError:
        return None, {}
    if not isinstance(body, dict):
        return None, {}
    data = body.get("data")
    return body.get("code"), data if isinstance(data, dict) else {}


def _config_headers(config: dict[str, Any]) -> dict[str, str]:
    """Attach the already-decrypted connection config to one request.

    Sent as a header, not persisted or logged by the plugin -- see
    docs/02-architecture.md's isolation model. This is local IPC over a
    Unix socket, not a network hop, so a plain JSON header is fine; there's
    no on-the-wire eavesdropping surface to defend against here the way
    there would be over TCP.
    """
    return {CONFIG_HEADER: encode_header_json(config)}


def _trace_headers(trace: dict[str, str]) -> dict[str, str]:
    names = {
        "request_id": REQUEST_ID_HEADER,
        "connection_id": CONNECTION_ID_HEADER,
        "run_id": RUN_ID_HEADER,
    }
    return {names[key]: value for key, value in trace.items()}


class PluginClient:
    """Calls one plugin process, identified by its Unix socket path."""

    def __init__(
        self,
        socket_path: Path,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        connection_id: str | None = None,
    ) -> None:
        self._socket_path = socket_path
        self._timeout = timeout
        # Which provider connection the calls are for: sent to the plugin
        # and put on every log line about the call.
        self._connection_id = connection_id

    def _trace(self) -> dict[str, str]:
        """Fresh ids for one call (see plugins/tracing.py)."""
        trace = {"request_id": new_request_id()}
        if self._connection_id:
            trace["connection_id"] = self._connection_id
        run_id = current_run_id.get()
        if run_id:
            trace["run_id"] = run_id
        return trace

    def _http_client(self) -> httpx.AsyncClient:
        transport = httpx.AsyncHTTPTransport(uds=str(self._socket_path))
        return httpx.AsyncClient(
            transport=transport,
            base_url="http://plugin",
            timeout=self._timeout,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        retry: bool,
        config: dict[str, Any] | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> httpx.Response:
        headers = _config_headers(config) if config is not None else {}
        headers.update(kwargs.pop("headers", None) or {})
        trace = self._trace()
        headers.update(_trace_headers(trace))
        attempts = READ_RETRY_ATTEMPTS if retry else 1
        # Which folder/object, not just which route: logged and kept on the
        # error as `target`.
        route = f"{method} {path}"
        target = route
        if kwargs.get("params"):
            target += f"?{urlencode(kwargs['params'])}"

        last_error: Exception | None = None
        started = time.monotonic()
        async with self._http_client() as client:
            for attempt in range(attempts):
                try:
                    response = await client.request(
                        method, path, headers=headers, **kwargs
                    )
                except (httpx.TimeoutException, httpx.ConnectError) as error:
                    last_error = error
                    if path.startswith("/auth/"):
                        logger.warning(
                            "Telegram login plugin RPC transport failed",
                            extra={
                                "phase": path.rsplit("/", 1)[-1],
                                "outcome": "transport_failure",
                                "reason": type(error).__name__,
                            },
                        )
                    retryable = isinstance(error, RETRYABLE_TRANSPORT_ERRORS)
                    if retryable and attempt + 1 < attempts:
                        await asyncio.sleep(READ_RETRY_BACKOFF_SECONDS * (2**attempt))
                        continue
                    # Timing and the query stay out of the message:
                    # GlitchTip groups issues by title, and a changing
                    # number or folder would open a new issue per event.
                    rpc_error = PluginRPCError(
                        f"Plugin call {route} failed: "
                        f"{type(error).__name__}: {error}",
                        transport_error=type(error).__name__,
                    )
                    _note_failure(rpc_error, target, started, attempt + 1, trace)
                    raise rpc_error from error
                if response.status_code >= 400:
                    code, data = _error_body(response)
                    rpc_error = PluginRPCError(
                        f"Plugin call {route} returned "
                        f"{response.status_code}: {response.text}",
                        status_code=response.status_code,
                        code=code,
                        data=data,
                    )
                    _note_failure(rpc_error, target, started, attempt + 1, trace)
                    raise rpc_error
                _note_success(target, started, response.status_code, trace)
                return response
        # Unreachable: the loop above always either returns or raises.
        raise PluginRPCError(f"Plugin call {route} failed") from last_error

    async def connect(self, config: dict[str, Any]) -> None:
        """`POST /connect` -- validate a config against the real backend.

        Not retried: a slow-but-real connection attempt (e.g. a network
        timeout against the actual provider) shouldn't be silently retried
        by us on top of whatever retry policy the plugin itself applies.
        """
        await self._request("POST", "/connect", retry=False, config=config)

    async def telegram_login_start(
        self,
        config: dict[str, Any],
        *,
        login_id: str,
        phone: str,
        channel_ref: str,
    ) -> dict[str, str]:
        response = await self._request(
            "POST",
            "/auth/start",
            retry=False,
            timeout=TELEGRAM_LOGIN_TIMEOUT,
            config=config,
            json={"login_id": login_id, "phone": phone, "channel_ref": channel_ref},
        )
        return response.json()

    async def telegram_login_step(
        self,
        config: dict[str, Any],
        *,
        login_id: str,
        step: str,
        value: str,
    ) -> dict[str, str]:
        response = await self._request(
            "POST",
            f"/auth/{login_id}/{step}",
            retry=False,
            timeout=TELEGRAM_LOGIN_TIMEOUT,
            config=config,
            json={step: value},
        )
        return response.json()

    async def telegram_login_cancel(self, login_id: str) -> None:
        await self._request(
            "DELETE",
            f"/auth/{login_id}",
            retry=False,
        )

    async def status(self) -> StatusOut:
        """`GET /status` -- plugin-reported backend health. Retried."""
        response = await self._request("GET", "/status", retry=True)
        return StatusOut.model_validate(response.json())

    async def list_resources(
        self,
        config: dict[str, Any],
        *,
        parent_id: str | None = None,
    ) -> list[Resource]:
        """`GET /resources?parent_id=`. Retried only when the plugin was
        unreachable; waits up to `LIST_TIMEOUT` for a slow listing."""
        params = {"parent_id": parent_id} if parent_id is not None else {}
        response = await self._request(
            "GET",
            "/resources",
            retry=True,
            config=config,
            params=params,
            timeout=LIST_TIMEOUT,
        )
        return [Resource.model_validate(item) for item in response.json()]

    async def get_resource(self, config: dict[str, Any], resource_id: str) -> Resource:
        """`GET /resources/{id}`. Retried only when the plugin was
        unreachable; waits up to `RESOURCE_TIMEOUT` for the answer."""
        response = await self._request(
            "GET",
            f"/resources/{encode_resource_id(resource_id)}",
            retry=True,
            config=config,
            timeout=RESOURCE_TIMEOUT,
        )
        return Resource.model_validate(response.json())

    async def read_content(
        self,
        config: dict[str, Any],
        resource_id: str,
        *,
        range_header: str | None = None,
    ) -> AsyncIterator[bytes]:
        """`GET /resources/{id}/content`, streamed.

        Uses `CONTENT_STREAM_TIMEOUT` so large S3/object reads are not killed
        by the JSON-RPC default (10s). Connect still fails fast, and only a
        failure to reach the plugin is retried -- never once a byte has been
        yielded: starting over from byte 0 would hand the consumer the head
        of the file twice (and hash garbage). A body the plugin cuts off
        raises `PluginRPCError` instead of ending like a complete file.

        Error messages name no file: GlitchTip groups issues by title, and
        callers log the reference themselves.
        """
        headers = _config_headers(config)
        if range_header:
            headers["Range"] = range_header
        trace = self._trace()
        headers.update(_trace_headers(trace))
        started = time.monotonic()
        # For log lines only (the error messages stay file-agnostic).
        target = f"GET /resources/{resource_id}/content"
        last_error: Exception | None = None
        for attempt in range(READ_RETRY_ATTEMPTS):
            yielded = False
            try:
                async with (
                    self._http_client() as client,
                    client.stream(
                        "GET",
                        f"/resources/{encode_resource_id(resource_id)}/content",
                        headers=headers,
                        timeout=CONTENT_STREAM_TIMEOUT,
                    ) as response,
                ):
                    if response.status_code >= 400:
                        await response.aread()
                        code, data = _error_body(response)
                        rpc_error = PluginRPCError(
                            f"Plugin content read returned {response.status_code}"
                            + (f" ({code})" if code else ""),
                            status_code=response.status_code,
                            code=code,
                            data=data,
                        )
                        _note_failure(rpc_error, target, started, attempt + 1, trace)
                        raise rpc_error
                    async for chunk in response.aiter_bytes():
                        yielded = True
                        yield chunk
                    _note_success(target, started, response.status_code, trace)
                    return
            except httpx.HTTPError as error:
                last_error = error
                retryable = isinstance(error, RETRYABLE_TRANSPORT_ERRORS)
                if retryable and not yielded and attempt + 1 < READ_RETRY_ATTEMPTS:
                    await asyncio.sleep(READ_RETRY_BACKOFF_SECONDS * (2**attempt))
                    continue
                what = "was cut off" if yielded else "failed"
                rpc_error = PluginRPCError(
                    f"Plugin content read {what}: {type(error).__name__}: {error}",
                    transport_error=type(error).__name__,
                )
                _note_failure(rpc_error, target, started, attempt + 1, trace)
                raise rpc_error from error
        raise PluginRPCError("Plugin content read failed") from last_error

    async def create_resource(
        self,
        config: dict[str, Any],
        metadata: CreateResourceIn,
        *,
        content: AsyncIterator[bytes] | bytes | None = None,
    ) -> Resource:
        """`POST /resources`. Never retried (would duplicate the write)."""
        headers = _config_headers(config)
        headers["X-Umedia-Resource-Metadata"] = encode_header_json(
            metadata.model_dump(),
        )
        response = await self._request(
            "POST",
            "/resources",
            retry=False,
            headers=headers,
            content=content,
            timeout=CONTENT_WRITE_TIMEOUT,
        )
        return Resource.model_validate(response.json())

    async def update_resource(
        self,
        config: dict[str, Any],
        resource_id: str,
        changes: UpdateResourceIn,
        *,
        content: AsyncIterator[bytes] | bytes | None = None,
    ) -> Resource:
        """`PUT /resources/{id}`. Never retried (would duplicate the write)."""
        headers = _config_headers(config)
        headers["X-Umedia-Resource-Metadata"] = encode_header_json(
            changes.model_dump(),
        )
        response = await self._request(
            "PUT",
            f"/resources/{encode_resource_id(resource_id)}",
            retry=False,
            headers=headers,
            content=content,
            timeout=CONTENT_WRITE_TIMEOUT,
        )
        return Resource.model_validate(response.json())

    async def delete_resource(self, config: dict[str, Any], resource_id: str) -> None:
        """`DELETE /resources/{id}`. Never retried (would duplicate the
        write -- harmless for a single delete, but not for a plugin that
        interprets "delete" as "delete-and-recreate-a-tombstone" or similar)."""
        await self._request(
            "DELETE",
            f"/resources/{encode_resource_id(resource_id)}",
            retry=False,
            config=config,
        )
