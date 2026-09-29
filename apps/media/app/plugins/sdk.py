"""Plugin-author SDK: scaffold a provider plugin's ASGI app in one call.

A plugin author implements `PluginBackend` -- the actual provider logic,
talking to S3/Telegram/rclone/whatever -- and passes it to
`create_plugin_app()` to get a FastAPI app implementing the full contract
in docs/03-provider-system.md. `plugins/<id>/main.py` then only needs:

    if __name__ == "__main__":
        asyncio.run(run_plugin(MyBackend()))

`run_plugin` reads the socket path PluginProcessManager appended as the
last argv and serves the app over it with uvicorn.
"""

import json
import sys
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .contracts import (
    CreateResourceIn,
    PluginBackendError,
    Resource,
    StatusOut,
    UpdateResourceIn,
    decode_header_json,
    decode_resource_id,
)

CONFIG_HEADER = "X-Umedia-Connection-Config"
METADATA_HEADER = "X-Umedia-Resource-Metadata"


class PluginBackend(ABC):
    """The real provider logic behind the resource-verb contract.

    Everything HTTP-shaped -- routing, config-header extraction, error
    translation -- is handled by `create_plugin_app`; a backend only ever
    sees a plain `config: dict` (the connection's already-decrypted
    config, attached by the core per-call -- never persisted here).
    """

    async def startup(self) -> None:
        """Called once when the plugin process starts.

        Override for long-lived, process-scoped resources shared across
        every connection this process serves -- e.g. `RcloneBackend`
        spawns its own `rclone rcd` child process here, once, rather than
        per request. Default: no-op.
        """
        return

    async def shutdown(self) -> None:
        """Called once when the plugin process is stopping. Default: no-op."""
        return

    @abstractmethod
    async def connect(self, config: dict[str, Any]) -> None:
        """Validate `config` against the real backend.

        Raise `ConnectionFailedError` (or let the real client's own
        exception surface -- the SDK maps anything else to 502) on
        failure.
        """

    async def status(self) -> StatusOut:
        """Plugin-reported backend health. Default: always healthy."""
        return StatusOut(healthy=True)

    @abstractmethod
    async def list_resources(
        self,
        config: dict[str, Any],
        *,
        parent_id: str | None,
    ) -> list[Resource]: ...

    @abstractmethod
    async def get_resource(
        self,
        config: dict[str, Any],
        resource_id: str,
    ) -> Resource:
        """Raise `ResourceNotFoundError` if it doesn't exist."""

    @abstractmethod
    def read_content(
        self,
        config: dict[str, Any],
        resource_id: str,
        *,
        range_header: str | None,
    ) -> AsyncIterator[bytes]:
        """Raise `ResourceNotFoundError` if it doesn't exist."""

    @abstractmethod
    async def create_resource(
        self,
        config: dict[str, Any],
        metadata: CreateResourceIn,
        *,
        content: AsyncIterator[bytes],
    ) -> Resource: ...

    @abstractmethod
    async def update_resource(
        self,
        config: dict[str, Any],
        resource_id: str,
        changes: UpdateResourceIn,
        *,
        content: AsyncIterator[bytes] | None,
    ) -> Resource:
        """Raise `ResourceNotFoundError` if it doesn't exist."""

    @abstractmethod
    async def delete_resource(self, config: dict[str, Any], resource_id: str) -> None:
        """Raise `ResourceNotFoundError` if it doesn't exist."""


class TelegramLoginPluginBackend:
    async def login_start(
        self,
        config: dict[str, Any],
        login_id: str,
        phone: str,
        channel_ref: str,
    ) -> dict[str, str]:
        raise NotImplementedError

    async def login_code(
        self,
        config: dict[str, Any],
        login_id: str,
        code: str,
    ) -> dict[str, str]:
        raise NotImplementedError

    async def login_password(
        self,
        config: dict[str, Any],
        login_id: str,
        password: str,
    ) -> dict[str, str]:
        raise NotImplementedError

    async def login_cancel(self, login_id: str) -> None:
        raise NotImplementedError


def _parse_header_json(request: Request, header: str) -> dict[str, Any]:
    value = request.headers.get(header)
    if not value:
        raise HTTPException(400, f"Missing required header: {header}")
    try:
        return decode_header_json(value)
    except (json.JSONDecodeError, ValueError) as error:
        raise HTTPException(400, f"Invalid JSON in header: {header}") from error


def create_plugin_app(backend: PluginBackend) -> FastAPI:  # noqa: C901
    """Build the plugin's ASGI app implementing the full REST contract.

    One function registering many small, single-purpose route handlers --
    each closes over `backend`, so splitting them out doesn't actually
    reduce complexity, just scatters it. `# noqa: C901` is deliberate.
    """

    @asynccontextmanager
    async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
        await backend.startup()
        try:
            yield
        finally:
            await backend.shutdown()

    app = FastAPI(title="UMedia provider plugin", lifespan=_lifespan)

    @app.exception_handler(PluginBackendError)
    def _backend_error(_: Request, error: PluginBackendError) -> JSONResponse:
        return JSONResponse(
            status_code=error.http_status,
            content={"code": type(error).__name__, "message": str(error)},
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Process liveness -- PluginProcessManager polls this, not
        `/status` (which is about the *backend*, not the process)."""
        return {"status": "ok"}

    @app.get("/status", response_model=StatusOut)
    async def status() -> StatusOut:
        return await backend.status()

    @app.post("/connect", status_code=204)
    async def connect(request: Request) -> None:
        config = _parse_header_json(request, CONFIG_HEADER)
        await backend.connect(config)

    @app.get("/resources", response_model=list[Resource])
    async def list_resources(
        request: Request,
        parent_id: str | None = None,
    ) -> list[Resource]:
        config = _parse_header_json(request, CONFIG_HEADER)
        return await backend.list_resources(config, parent_id=parent_id)

    @app.get("/resources/{resource_id}", response_model=Resource)
    async def get_resource(resource_id: str, request: Request) -> Resource:
        config = _parse_header_json(request, CONFIG_HEADER)
        return await backend.get_resource(config, decode_resource_id(resource_id))

    @app.get("/resources/{resource_id}/content")
    async def read_content(resource_id: str, request: Request) -> StreamingResponse:
        config = _parse_header_json(request, CONFIG_HEADER)
        range_header = request.headers.get("Range")
        stream = backend.read_content(
            config,
            decode_resource_id(resource_id),
            range_header=range_header,
        )
        return StreamingResponse(stream)

    @app.post("/resources", response_model=Resource)
    async def create_resource(request: Request) -> Resource:
        config = _parse_header_json(request, CONFIG_HEADER)
        metadata = CreateResourceIn.model_validate(
            _parse_header_json(request, METADATA_HEADER),
        )
        return await backend.create_resource(
            config,
            metadata,
            content=request.stream(),
        )

    @app.put("/resources/{resource_id}", response_model=Resource)
    async def update_resource(resource_id: str, request: Request) -> Resource:
        config = _parse_header_json(request, CONFIG_HEADER)
        changes = UpdateResourceIn.model_validate(
            _parse_header_json(request, METADATA_HEADER),
        )
        content = request.stream() if changes.overwrite_content else None
        return await backend.update_resource(
            config,
            decode_resource_id(resource_id),
            changes,
            content=content,
        )

    @app.delete("/resources/{resource_id}", status_code=204)
    async def delete_resource(resource_id: str, request: Request) -> Response:
        config = _parse_header_json(request, CONFIG_HEADER)
        await backend.delete_resource(config, decode_resource_id(resource_id))
        return Response(status_code=204)

    if isinstance(backend, TelegramLoginPluginBackend):

        @app.post("/auth/start")
        async def login_start(request: Request) -> dict[str, str]:
            config = _parse_header_json(request, CONFIG_HEADER)
            data = await request.json()
            return await backend.login_start(
                config,
                str(data["login_id"]),
                str(data["phone"]),
                str(data["channel_ref"]),
            )

        @app.post("/auth/{login_id}/code")
        async def login_code(login_id: str, request: Request) -> dict[str, str]:
            config = _parse_header_json(request, CONFIG_HEADER)
            data = await request.json()
            return await backend.login_code(config, login_id, str(data["code"]))

        @app.post("/auth/{login_id}/password")
        async def login_password(login_id: str, request: Request) -> dict[str, str]:
            config = _parse_header_json(request, CONFIG_HEADER)
            data = await request.json()
            return await backend.login_password(
                config,
                login_id,
                str(data["password"]),
            )

        @app.delete("/auth/{login_id}", status_code=204)
        async def login_cancel(login_id: str) -> Response:
            await backend.login_cancel(login_id)
            return Response(status_code=204)

    return app


async def run_plugin(backend: PluginBackend) -> None:
    """Serve `backend` over the Unix socket PluginProcessManager assigned.

    The socket path is always the last argv entry -- see
    `plugins/process_manager.py`'s `_spawn`.
    """
    socket_path = sys.argv[-1]
    app = create_plugin_app(backend)
    config = uvicorn.Config(app, uds=socket_path, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()
