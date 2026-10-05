"""PluginClient timeout defaults for JSON RPC vs content streams."""

import httpx

from plugins.client import CONTENT_STREAM_TIMEOUT, DEFAULT_TIMEOUT_SECONDS


def test_content_stream_timeout_has_unlimited_read() -> None:
    """Large cross-storage copies must not inherit the 10s JSON-RPC read cap."""
    assert DEFAULT_TIMEOUT_SECONDS == 10.0
    assert isinstance(CONTENT_STREAM_TIMEOUT, httpx.Timeout)
    assert CONTENT_STREAM_TIMEOUT.read is None
    assert CONTENT_STREAM_TIMEOUT.connect == DEFAULT_TIMEOUT_SECONDS


def test_write_timeout_lets_slow_uploads_finish() -> None:
    """Uploads wait on the provider's own transfer (Telegram, Dropbox):
    the plugin only answers once the bytes are stored remotely, so the
    10s JSON-RPC read cap killed real 2-6 MB copies (httpx.ReadTimeout)."""
    from plugins.client import CONTENT_WRITE_TIMEOUT

    assert CONTENT_WRITE_TIMEOUT.read is None
    assert CONTENT_WRITE_TIMEOUT.write is None
    assert CONTENT_WRITE_TIMEOUT.connect == DEFAULT_TIMEOUT_SECONDS


async def test_slow_plugin_upload_outlives_the_default_timeout(tmp_path) -> None:  # noqa: ANN001
    """End to end over a real Unix socket: a plugin that takes longer
    than the client's JSON-RPC timeout to store an upload still
    succeeds, for both create and content-overwrite."""
    import asyncio

    from plugins.client import PluginClient
    from plugins.contracts import CreateResourceIn, UpdateResourceIn

    socket_path = tmp_path / "slow.sock"
    body = b'{"id":"r1","type":"file","name":"big.jpg"}'

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        length = next(
            int(line.split(b":", 1)[1])
            for line in head.split(b"\r\n")
            if line.lower().startswith(b"content-length")
        )
        await reader.readexactly(length)
        await asyncio.sleep(0.6)  # > the client's 0.2s timeout below
        writer.write(
            b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n"
            b"content-length: " + str(len(body)).encode() + b"\r\n\r\n" + body,
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_unix_server(handle, path=str(socket_path))
    async with server:
        client = PluginClient(socket_path, timeout=0.2)
        created = await client.create_resource(
            {}, CreateResourceIn(name="big.jpg"), content=b"x" * 1024,
        )
        updated = await client.update_resource(
            {}, "r1", UpdateResourceIn(overwrite_content=True), content=b"y" * 1024,
        )
    assert created.id == "r1"
    assert updated.id == "r1"
