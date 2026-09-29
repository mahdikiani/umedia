"""Telegram MTProto storage connection adapter."""

from typing import Any

from pyrogram import Client
from pyrogram.enums import ChatMemberStatus


class TelegramStorageProvider:
    """Validate a Kurigram session string and target channel."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    async def test_connection(self) -> None:
        """Connect with MTProto and verify channel access."""
        async with Client(
            "umedia-telegram",
            api_id=int(self._config["api_id"]),
            api_hash=str(self._config["api_hash"]),
            session_string=str(self._config["session"]),
            no_updates=True,
        ) as client:
            if await client.get_me() is None:
                raise ValueError("Telegram session is not authorized")
            member = await client.get_chat_member(int(self._config["channel_id"]), "me")
            if member.status not in (
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER,
            ):
                raise ValueError("Telegram session must administer the channel")
