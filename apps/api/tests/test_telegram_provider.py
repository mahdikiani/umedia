from __future__ import annotations

from types import SimpleNamespace

import pytest
from pyrogram.enums import ChatMemberStatus

from providers import telegram
from providers.telegram import TelegramStorageProvider


class FakeClient:
    def __init__(self, *, authorized: bool, status: ChatMemberStatus) -> None:
        self.authorized = authorized
        self.status = status

    async def __aenter__(self) -> FakeClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get_me(self) -> str | None:
        return "user" if self.authorized else None

    async def get_chat_member(self, _channel_id: int, _user_id: str) -> SimpleNamespace:
        return SimpleNamespace(status=self.status)


@pytest.mark.asyncio
async def test_telegram_provider_accepts_admin_kurigram_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(authorized=True, status=ChatMemberStatus.ADMINISTRATOR)
    monkeypatch.setattr(telegram, "Client", lambda *_args, **_kwargs: client)
    provider = TelegramStorageProvider(
        {
            "session": "kurigram-session",
            "api_id": "1",
            "api_hash": "hash",
            "channel_id": "-100",
        },
    )

    await provider.test_connection()


@pytest.mark.asyncio
async def test_telegram_provider_rejects_unauthorized_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(authorized=False, status=ChatMemberStatus.MEMBER)
    monkeypatch.setattr(telegram, "Client", lambda *_args, **_kwargs: client)
    provider = TelegramStorageProvider(
        {
            "session": "kurigram-session",
            "api_id": "1",
            "api_hash": "hash",
            "channel_id": "-100",
        },
    )

    with pytest.raises(ValueError, match="not authorized"):
        await provider.test_connection()


@pytest.mark.asyncio
async def test_telegram_provider_rejects_non_admin_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(authorized=True, status=ChatMemberStatus.MEMBER)
    monkeypatch.setattr(telegram, "Client", lambda *_args, **_kwargs: client)
    provider = TelegramStorageProvider(
        {
            "session": "kurigram-session",
            "api_id": "1",
            "api_hash": "hash",
            "channel_id": "-100",
        },
    )

    with pytest.raises(ValueError, match="must administer"):
        await provider.test_connection()
