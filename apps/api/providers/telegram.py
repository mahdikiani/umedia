"""Telegram MTProto storage connection adapter."""

from typing import Any

from telethon import TelegramClient
from telethon.sessions import StringSession


class TelegramStorageProvider:
    """Validate an encrypted Telethon session and target channel."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    async def test_connection(self) -> None:
        """Connect with MTProto and verify channel access."""
        client = TelegramClient(
            StringSession(str(self._config["session"])),
            int(self._config["api_id"]),
            str(self._config["api_hash"]),
        )
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise ValueError("Telegram session is not authorized")
            entity = await client.get_entity(int(self._config["channel_id"]))
            permissions = await client.get_permissions(entity, "me")
            if not (
                getattr(permissions, "is_admin", False)
                or getattr(permissions, "is_creator", False)
            ):
                raise ValueError("Telegram session must administer the channel")
        finally:
            await client.disconnect()

