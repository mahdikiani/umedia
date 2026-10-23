"""A plugin read that fails before its first byte is a real error status,
not a 200 with a cut-off body (GlitchTip UMEDIA-2).

Plugin side: the SDK starts the backend stream before answering, and the
rclone backend reports `rclone cat` failures by exit code, kills rclone
when the reader stops early, and keeps credentials out of the message.
Core side: the client never restarts a read that already yielded bytes and
turns a cut-off body into a `PluginRPCError`.
"""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from plugins import client as client_module
from plugins.client import PluginClient, PluginRPCError
from plugins.contracts import (
    PluginBackendError,
    ResourceNotFoundError,
    TemporaryProviderError,
    encode_header_json,
    encode_resource_id,
)
from plugins.rclone.backend import RcloneBackend
from plugins.sdk import PluginBackend, create_plugin_app
from plugins.streams import prime_stream

# ----------------------------------------------------------------------
# Plugin SDK
# ----------------------------------------------------------------------


class _ReadBackend(PluginBackend):
    def __init__(self, chunks: list[bytes], error: Exception | None = None) -> None:
        self._chunks = chunks
        self._error = error

    async def connect(self, config: dict[str, Any]) -> None: ...

    async def list_resources(self, config: dict, *, parent_id: str | None) -> list:
        return []

    async def get_resource(self, config: dict, resource_id: str) -> Any: ...  # noqa: ANN401

    async def read_content(
        self, config: dict, resource_id: str, *, range_header: str | None,
    ) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk
        if self._error is not None:
            raise self._error

    async def create_resource(
        self, config: dict, metadata: Any, *, content: Any,  # noqa: ANN401
    ) -> Any: ...  # noqa: ANN401

    async def update_resource(
        self, config: dict, resource_id: str, changes: Any, *, content: Any,  # noqa: ANN401
    ) -> Any: ...  # noqa: ANN401

    async def delete_resource(self, config: dict, resource_id: str) -> None: ...


async def _get_content(backend: PluginBackend) -> httpx.Response:
    app = create_plugin_app(backend)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://plugin",
    ) as client:
        return await client.get(
            f"/resources/{encode_resource_id('a/link.md')}/content",
            headers={"X-Umedia-Connection-Config": encode_header_json({})},
        )


async def test_read_failing_before_the_first_byte_is_a_404_not_a_200() -> None:
    response = await _get_content(
        _ReadBackend([], ResourceNotFoundError("a/link.md: directory not found")),
    )
    assert response.status_code == 404
    assert response.json()["code"] == "ResourceNotFoundError"


async def test_read_streams_every_chunk_after_the_first() -> None:
    response = await _get_content(_ReadBackend([b"ab", b"cd", b"ef"]))
    assert response.status_code == 200
    assert response.content == b"abcdef"


async def test_empty_file_is_an_empty_200() -> None:
    response = await _get_content(_ReadBackend([]))
    assert response.status_code == 200
    assert response.content == b""


async def test_prime_stream_closes_the_source_when_the_first_read_fails() -> None:
    closed: list[bool] = []

    async def source() -> AsyncIterator[bytes]:  # noqa: RUF029
        try:
            raise ResourceNotFoundError("gone")
            yield b""  # pragma: no cover
        finally:
            closed.append(True)

    with pytest.raises(ResourceNotFoundError):
        await prime_stream(source())
    assert closed == [True]


# ----------------------------------------------------------------------
# rclone backend: `rclone cat`
# ----------------------------------------------------------------------


class _FakeCat:
    """An `rclone cat` process: stdout bytes, stderr text, an exit code."""

    def __init__(
        self,
        stdout: bytes,
        exit_code: int,
        *,
        stderr: bytes = b"",
        finished: bool = True,
    ) -> None:
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(stdout)
        if finished:
            self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()
        self.returncode: int | None = None
        self._exit_code = exit_code
        self.killed = False

    def kill(self) -> None:
        self.killed = True
        self._exit_code = -9
        self.stdout.feed_eof()

    async def wait(self) -> int:
        self.returncode = self._exit_code
        return self._exit_code


FS = ":sftp,host=mobin,pass='SECRET-PASS':"


async def _drain(stream: AsyncIterator[bytes]) -> list[bytes]:
    return [chunk async for chunk in stream]


@pytest.mark.parametrize(
    ("exit_code", "error_type"),
    [
        (3, ResourceNotFoundError),  # what an unreadable SFTP link gives
        (4, ResourceNotFoundError),
        (5, TemporaryProviderError),
        (1, PluginBackendError),
    ],
)
async def test_cat_failure_is_classified_by_exit_code(
    exit_code: int, error_type: type[Exception],
) -> None:
    process = _FakeCat(
        b"",
        exit_code,
        stderr=(
            f"ERROR : error listing: directory not found for {FS}a\n"
            "NOTICE: Failed to cat with 2 errors"
        ).encode(),
    )
    with (
        patch("plugins.rclone.backend._build_fs", return_value=FS),
        patch(
            "plugins.rclone.backend.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=process),
        ),
        pytest.raises(PluginBackendError) as caught,
    ):
        await _drain(RcloneBackend().read_content({}, "a/link.md", range_header=None))
    assert type(caught.value) is error_type
    assert f"exited {exit_code}" in str(caught.value)
    assert "SECRET-PASS" not in str(caught.value)
    assert "<remote>" in str(caught.value)


async def test_cat_failing_after_partial_output_still_raises() -> None:
    process = _FakeCat(b"partial", 1, stderr=b"read: connection reset")
    with (
        patch("plugins.rclone.backend._build_fs", return_value=FS),
        patch(
            "plugins.rclone.backend.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=process),
        ),
    ):
        chunks = RcloneBackend().read_content({}, "a/big.bin", range_header=None)
        assert await anext(chunks) == b"partial"
        with pytest.raises(PluginBackendError):
            await anext(chunks)


async def test_cat_is_killed_when_the_reader_stops_early() -> None:
    process = _FakeCat(b"first chunk", 0, finished=False)
    with (
        patch("plugins.rclone.backend._build_fs", return_value=FS),
        patch(
            "plugins.rclone.backend.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=process),
        ),
    ):
        chunks = RcloneBackend().read_content({}, "a/video.mp4", range_header=None)
        assert await anext(chunks) == b"first chunk"
        await chunks.aclose()  # client went away / seeked elsewhere
    assert process.killed
    assert process.returncode == -9


# ----------------------------------------------------------------------
# Core client
# ----------------------------------------------------------------------


async def _plugin(socket_path: Path, reply: bytes, seen: list[bytes]) -> asyncio.Server:
    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        seen.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(reply)
        await writer.drain()
        writer.close()

    return await asyncio.start_unix_server(handle, path=str(socket_path))


async def test_a_body_cut_off_mid_stream_raises_and_is_not_restarted(
    tmp_path: Path,
) -> None:
    cut_off = (
        b"HTTP/1.1 200 OK\r\ntransfer-encoding: chunked\r\n\r\n"
        b"5\r\nhello\r\n"  # ...and the connection closes without the last chunk
    )
    socket_path = tmp_path / "plugin.sock"
    seen: list[bytes] = []
    received: list[bytes] = []
    server = await _plugin(socket_path, cut_off, seen)
    async with server:
        client = PluginClient(socket_path)
        with pytest.raises(PluginRPCError) as caught:
            async for chunk in client.read_content({}, "a/big.bin"):
                received.append(chunk)  # noqa: PERF401 -- keep partial chunks

    assert received == [b"hello"]  # never the head of the file twice
    assert len(seen) == 1
    assert caught.value.transport_error == "RemoteProtocolError"
    assert "cut off" in str(caught.value)


async def test_a_404_answer_carries_the_plugin_error_code(tmp_path: Path) -> None:
    body = b'{"code":"ResourceNotFoundError","message":"a/link.md: exited 3"}'
    reply = (
        b"HTTP/1.1 404 Not Found\r\ncontent-type: application/json\r\n"
        b"content-length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    )
    socket_path = tmp_path / "plugin.sock"
    server = await _plugin(socket_path, reply, [])
    async with server:
        with pytest.raises(PluginRPCError) as caught:
            await _drain(PluginClient(socket_path).read_content({}, "a/link.md"))
    assert caught.value.status_code == 404
    assert caught.value.code == "ResourceNotFoundError"
    assert "a/link.md" not in str(caught.value)  # one GlitchTip issue, not one per file


async def test_an_unreachable_plugin_is_retried_before_any_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "READ_RETRY_BACKOFF_SECONDS", 0.0)
    with pytest.raises(PluginRPCError) as caught:
        await _drain(PluginClient(tmp_path / "gone.sock").read_content({}, "a"))
    assert caught.value.transport_error == "ConnectError"
    assert caught.value.attempts == client_module.READ_RETRY_ATTEMPTS
