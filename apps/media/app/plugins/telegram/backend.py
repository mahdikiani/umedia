"""Telegram provider plugin backend.

Stores resources as documents (media attachments) in a Telegram channel,
via MTProto (Telethon) -- matching the original product description
("Store media in a Telegram channel using MTProto") and porting
`apps/api/providers/telegram.py`'s connection/permission check verbatim.

**Not live-verified** (unlike `local`, fully tested, and `rclone`, whose
generic mechanism is fully tested): there is no way to stand up a fake
Telegram MTProto server in this environment, and this plugin needs a real,
already-authorized Telethon session string to connect to anything. Tested
here with a mocked `TelegramClient` (`tests/test_plugin_telegram_backend.py`)
to verify UMedia's own glue logic -- the right Telethon calls, with the
right arguments, mapped into the right `Resource` shape -- not Telethon
itself. Verifying against a real bot/channel is a concrete follow-up
(docs/09-tasks.md P3.4).

Known platform limitations, by design, not oversights:
- Telegram channels are a flat message stream -- no folders. `create()`
  with `type: folder` is rejected.
- Telegram has no in-place rename of a document's filename attribute.
  A pure rename (no content change) downloads the existing bytes, deletes
  the old message, and re-uploads under the new name -- correct, but not
  cheap. `update()`'s `id` therefore always changes, same as it does when
  content changes -- allowed by the contract (docs/03-provider-system.md
  "a resource's id may change on update()").
"""

import io
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeFilename

from plugins.contracts import (
    ConnectionFailedError,
    CreateResourceIn,
    Resource,
    ResourceNotFoundError,
    UpdateResourceIn,
)
from plugins.sdk import PluginBackend

LIST_LIMIT = 200


@asynccontextmanager
async def _connected_client(config: dict[str, Any]) -> AsyncIterator[TelegramClient]:
    """Connect, verify the session is authorized, yield, always disconnect.

    One connection per call -- simple and stateless (matches every other
    method in this backend receiving `config` fresh each time), at the
    cost of reconnecting per request. Pooling connections per connection
    config is a reasonable later optimization, not correctness-critical;
    not done here to avoid holding per-config state a plugin process
    otherwise has no reason to keep.
    """
    client = TelegramClient(
        StringSession(str(config["session"])),
        int(config["api_id"]),
        str(config["api_hash"]),
    )
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise ConnectionFailedError("Telegram session is not authorized")
        yield client
    finally:
        await client.disconnect()


async def _admin_channel(client: TelegramClient, config: dict[str, Any]) -> Any:  # noqa: ANN401
    """The channel entity, after confirming the session administers it."""
    entity = await client.get_entity(int(config["channel_id"]))
    permissions = await client.get_permissions(entity, "me")
    if not (
        getattr(permissions, "is_admin", False)
        or getattr(permissions, "is_creator", False)
    ):
        raise ConnectionFailedError("Telegram session must administer the channel")
    return entity


def _filename(message: Any) -> str:  # noqa: ANN401
    document = getattr(message, "document", None)
    if document is not None:
        for attribute in document.attributes:
            if isinstance(attribute, DocumentAttributeFilename):
                return attribute.file_name
    return f"telegram-{message.id}"


def _to_resource(message: Any) -> Resource:  # noqa: ANN401
    document = getattr(message, "document", None)
    return Resource(
        id=str(message.id),
        type="file",
        name=_filename(message),
        parent_id=None,  # flat: see module docstring
        size=document.size if document is not None else None,
        content_type=document.mime_type if document is not None else None,
    )


def _parse_range(range_header: str | None) -> tuple[int, int | None]:
    """`Range: bytes=start-end` -> (offset, limit) for `iter_download`."""
    if not range_header:
        return 0, None
    import re

    match = re.match(r"bytes=(\d+)-(\d*)", range_header)
    if not match:
        return 0, None
    start_str, end_str = match.groups()
    start = int(start_str)
    limit = int(end_str) - start + 1 if end_str else None
    return start, limit


class TelegramBackend(PluginBackend):
    """Resources are documents (media messages) in one admin-owned channel."""

    async def connect(self, config: dict[str, Any]) -> None:
        async with _connected_client(config) as client:
            await _admin_channel(client, config)

    async def list_resources(
        self, config: dict[str, Any], *, parent_id: str | None,
    ) -> list[Resource]:
        if parent_id:
            # Flat namespace: nothing has ever been "inside" another item.
            raise ResourceNotFoundError(parent_id)
        async with _connected_client(config) as client:
            channel = await _admin_channel(client, config)
            messages = client.iter_messages(channel, limit=LIST_LIMIT)
            return [
                _to_resource(message)
                async for message in messages
                if getattr(message, "document", None)
            ]

    async def get_resource(self, config: dict[str, Any], resource_id: str) -> Resource:
        async with _connected_client(config) as client:
            channel = await _admin_channel(client, config)
            message = await client.get_messages(channel, ids=int(resource_id))
            if message is None or not getattr(message, "document", None):
                raise ResourceNotFoundError(resource_id)
            return _to_resource(message)

    async def read_content(
        self,
        config: dict[str, Any],
        resource_id: str,
        *,
        range_header: str | None,
    ) -> AsyncIterator[bytes]:
        async with _connected_client(config) as client:
            channel = await _admin_channel(client, config)
            message = await client.get_messages(channel, ids=int(resource_id))
            if message is None or not getattr(message, "document", None):
                raise ResourceNotFoundError(resource_id)
            offset, limit = _parse_range(range_header)
            async for chunk in client.iter_download(
                message.media, offset=offset, limit=limit,
            ):
                yield chunk

    async def create_resource(
        self,
        config: dict[str, Any],
        metadata: CreateResourceIn,
        *,
        content: AsyncIterator[bytes],
    ) -> Resource:
        if metadata.type == "folder":
            raise ConnectionFailedError(
                "Telegram channels do not support folders",
            )
        body = b"".join([chunk async for chunk in content])
        async with _connected_client(config) as client:
            channel = await _admin_channel(client, config)
            message = await client.send_file(
                channel,
                file=io.BytesIO(body),
                file_name=metadata.name,
                force_document=True,
            )
            return _to_resource(message)

    async def update_resource(
        self,
        config: dict[str, Any],
        resource_id: str,
        changes: UpdateResourceIn,
        *,
        content: AsyncIterator[bytes] | None,
    ) -> Resource:
        async with _connected_client(config) as client:
            channel = await _admin_channel(client, config)
            message = await client.get_messages(channel, ids=int(resource_id))
            if message is None or not getattr(message, "document", None):
                raise ResourceNotFoundError(resource_id)

            new_name = changes.name or _filename(message)
            if content is not None:
                body = b"".join([chunk async for chunk in content])
            else:
                # No in-place rename (see module docstring): re-upload the
                # existing bytes under the new name.
                buffer = io.BytesIO()
                async for chunk in client.iter_download(message.media):
                    buffer.write(chunk)
                body = buffer.getvalue()

            await client.delete_messages(channel, [message.id])
            new_message = await client.send_file(
                channel,
                file=io.BytesIO(body),
                file_name=new_name,
                force_document=True,
            )
            return _to_resource(new_message)

    async def delete_resource(self, config: dict[str, Any], resource_id: str) -> None:
        async with _connected_client(config) as client:
            channel = await _admin_channel(client, config)
            message = await client.get_messages(channel, ids=int(resource_id))
            if message is None or not getattr(message, "document", None):
                raise ResourceNotFoundError(resource_id)
            await client.delete_messages(channel, [message.id])
