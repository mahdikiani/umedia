"""Core-side RPC client for calling one running provider plugin process
over its Unix domain socket.

See docs/03-provider-system.md for the contract and docs/02-architecture.md
"Correctness & consistency" #1/#3 for the timeout/retry rules this
implements: every call has an explicit timeout; reads may retry with
backoff on timeout, writes never auto-retry (to avoid duplicate side
effects from retrying a slow-but-actually-succeeding write).
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

from .contracts import (
    CreateResourceIn,
    Resource,
    StatusOut,
    UpdateResourceIn,
    encode_header_json,
    encode_resource_id,
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
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        # The plugin's error class name and structured `data`, when the
        # plugin SDK produced the error body (see sdk.py's handler).
        self.code = code
        self.data = data or {}


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


class PluginClient:
    """Calls one plugin process, identified by its Unix socket path."""

    def __init__(
        self,
        socket_path: Path,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._socket_path = socket_path
        self._timeout = timeout

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
        attempts = READ_RETRY_ATTEMPTS if retry else 1

        last_error: Exception | None = None
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
                    if attempt + 1 < attempts:
                        await asyncio.sleep(READ_RETRY_BACKOFF_SECONDS * (2**attempt))
                        continue
                    raise PluginRPCError(
                        f"Plugin call {method} {path} failed: {error}",
                    ) from error
                if response.status_code >= 400:
                    code, data = _error_body(response)
                    raise PluginRPCError(
                        f"Plugin call {method} {path} returned "
                        f"{response.status_code}: {response.text}",
                        status_code=response.status_code,
                        code=code,
                        data=data,
                    )
                return response
        # Unreachable: the loop above always either returns or raises.
        raise PluginRPCError(f"Plugin call {method} {path} failed") from last_error

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
        """`GET /resources?parent_id=`. Retried (idempotent read)."""
        params = {"parent_id": parent_id} if parent_id is not None else {}
        response = await self._request(
            "GET",
            "/resources",
            retry=True,
            config=config,
            params=params,
        )
        return [Resource.model_validate(item) for item in response.json()]

    async def get_resource(self, config: dict[str, Any], resource_id: str) -> Resource:
        """`GET /resources/{id}`. Retried (idempotent read)."""
        response = await self._request(
            "GET",
            f"/resources/{encode_resource_id(resource_id)}",
            retry=True,
            config=config,
        )
        return Resource.model_validate(response.json())

    async def read_content(
        self,
        config: dict[str, Any],
        resource_id: str,
        *,
        range_header: str | None = None,
    ) -> AsyncIterator[bytes]:
        """`GET /resources/{id}/content`, streamed. Retried (idempotent).

        Uses `CONTENT_STREAM_TIMEOUT` so large S3/object reads are not killed
        by the JSON-RPC default (10s). Connect still fails fast.
        """
        headers = _config_headers(config)
        if range_header:
            headers["Range"] = range_header
        last_error: Exception | None = None
        for attempt in range(READ_RETRY_ATTEMPTS):
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
                        raise PluginRPCError(
                            f"Plugin content read returned {response.status_code}",
                            status_code=response.status_code,
                        )
                    async for chunk in response.aiter_bytes():
                        yield chunk
                    return
            except (httpx.TimeoutException, httpx.ConnectError) as error:
                last_error = error
                if attempt + 1 < READ_RETRY_ATTEMPTS:
                    await asyncio.sleep(READ_RETRY_BACKOFF_SECONDS * (2**attempt))
                    continue
                raise PluginRPCError(
                    f"Plugin content read failed: {error}",
                ) from error
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
