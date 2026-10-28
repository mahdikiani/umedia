"""The Telegram plugin shares one client per session and backs off on flood.

It used to open a new client (connect + get_me) for every request: ~60
thumbnail requests opened ~60 MTProto connections, Telegram answered
"Transport flood" and Kurigram reconnected forever (2026-10-25, 16 h;
again 2026-10-28 when the poller reached the connection).
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from pyrogram.errors import FloodWait

from plugins.telegram import backend as tg
from plugins.telegram.backend import TelegramBackend
from tests.test_plugin_telegram_backend import (
    CONFIG,
    FakeTelegramClient,
    _FakeDocument,
    _FakeMessage,
)


class _CountingClient(FakeTelegramClient):
    """Counts connects and how many document reads run at once."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.connects = 0
        self.active = 0
        self.peak = 0
        self.release = asyncio.Event()
        self.messages[1] = _FakeMessage(1, _FakeDocument(b"abc", "a.jpg"))

    async def connect(self) -> None:
        self.connects += 1
        await super().connect()

    async def stream_media(self, message, *, offset=0, limit=0):  # noqa: ANN001, ANN202
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await self.release.wait()
            async for chunk in super().stream_media(
                message, offset=offset, limit=limit,
            ):
                yield chunk
        finally:
            self.active -= 1


async def _read(backend: TelegramBackend) -> bytes:
    return b"".join([
        chunk
        async for chunk in backend.read_content(CONFIG, "1", range_header=None)
    ])


@pytest.mark.asyncio
async def test_many_reads_share_one_client_and_run_a_few_at_a_time() -> None:
    client = _CountingClient()
    backend = TelegramBackend()
    with patch("plugins.telegram.backend.Client", return_value=client) as made:
        reads = [asyncio.create_task(_read(backend)) for _ in range(20)]
        for _ in range(50):
            await asyncio.sleep(0)
        assert client.active == tg.MAX_CONCURRENT_CALLS
        client.release.set()
        results = await asyncio.gather(*reads)

    assert results == [b"abc"] * 20
    assert made.call_count == 1
    assert client.connects == 1
    assert client.peak == tg.MAX_CONCURRENT_CALLS


@pytest.mark.asyncio
async def test_admin_check_is_reused_between_calls() -> None:
    client = _CountingClient()
    client.release.set()
    calls = 0
    original = client.get_chat_member

    async def _counting(*args: object):  # noqa: ANN202
        nonlocal calls
        calls += 1
        return await original(*args)

    client.get_chat_member = _counting  # type: ignore[method-assign]
    backend = TelegramBackend()
    with patch("plugins.telegram.backend.Client", return_value=client):
        for _ in range(5):
            await backend.get_resource(CONFIG, "1")
        await backend.connect(CONFIG)  # connect always re-checks
    assert calls == 2


@pytest.mark.asyncio
async def test_flood_wait_pauses_the_session_and_closes_the_client() -> None:
    client = _CountingClient()
    client.release.set()

    async def _flood(*_args: object, **_kwargs: object) -> None:
        raise FloodWait(value=120)

    backend = TelegramBackend()
    with patch("plugins.telegram.backend.Client", return_value=client):
        await backend.get_resource(CONFIG, "1")
        client.get_messages = _flood  # type: ignore[method-assign]

        with pytest.raises(tg._TelegramBusyError) as first:
            await backend.get_resource(CONFIG, "1")
        assert first.value.http_status == 503
        assert client.connected is False  # flood closes the shared client

        # While paused, calls fail fast without reconnecting.
        with pytest.raises(tg._TelegramBusyError):
            await backend.get_resource(CONFIG, "1")
        assert client.connects == 1


@pytest.mark.asyncio
async def test_a_hung_call_pauses_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _CountingClient()
    client.release.set()
    monkeypatch.setattr(tg, "CALL_TIMEOUT_SECONDS", 0.01)

    async def _hang(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(10)

    backend = TelegramBackend()
    with patch("plugins.telegram.backend.Client", return_value=client):
        client.get_messages = _hang  # type: ignore[method-assign]
        with pytest.raises(tg._TelegramBusyError):
            await backend.get_resource(CONFIG, "1")
        assert client.connected is False
        with pytest.raises(tg._TelegramBusyError):
            await backend.get_resource(CONFIG, "1")
    assert client.connects == 1


@pytest.mark.asyncio
async def test_session_reconnects_after_the_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _CountingClient()
    client.release.set()
    now = [1000.0]
    monkeypatch.setattr(tg, "_now", lambda: now[0])

    async def _flood(*_args: object, **_kwargs: object) -> None:
        raise FloodWait(value=5)

    backend = TelegramBackend()
    original = client.get_messages
    with patch("plugins.telegram.backend.Client", return_value=client):
        client.get_messages = _flood  # type: ignore[method-assign]
        with pytest.raises(tg._TelegramBusyError):
            await backend.get_resource(CONFIG, "1")
        client.get_messages = original  # type: ignore[method-assign]
        now[0] += tg.FLOOD_COOLDOWN_SECONDS + 1
        resource = await backend.get_resource(CONFIG, "1")
    assert resource.name == "a.jpg"
    assert client.connects == 2


@pytest.mark.asyncio
async def test_idle_clients_are_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _CountingClient()
    client.release.set()
    now = [1000.0]
    monkeypatch.setattr(tg, "_now", lambda: now[0])
    backend = TelegramBackend()
    with patch("plugins.telegram.backend.Client", return_value=client):
        await backend.get_resource(CONFIG, "1")
        await backend._pool.close_idle()
        assert client.connected is True
        now[0] += tg.IDLE_CLOSE_SECONDS + 1
        await backend._pool.close_idle()
    assert client.connected is False


@pytest.mark.asyncio
async def test_unauthorized_session_is_not_kept() -> None:
    client = _CountingClient(authorized=False)
    backend = TelegramBackend()
    with patch("plugins.telegram.backend.Client", return_value=client):
        with pytest.raises(tg.ConnectionFailedError):
            await backend.connect(CONFIG)
        with pytest.raises(tg.ConnectionFailedError):
            await backend.connect(CONFIG)
    assert client.connected is False
    assert client.connects == 2
