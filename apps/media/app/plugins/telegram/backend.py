"""Telegram provider plugin backend using Kurigram's Pyrogram-compatible API.

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

import asyncio
import io
import math
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from pyrogram import Client
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import (
    PasswordHashInvalid,
    PhoneCodeExpired,
    PhoneCodeInvalid,
    SessionPasswordNeeded,
)

from plugins.contracts import (
    ConnectionFailedError,
    CreateResourceIn,
    Resource,
    ResourceNotFoundError,
    UpdateResourceIn,
)
from plugins.sdk import PluginBackend, TelegramLoginPluginBackend

LIST_LIMIT = 200
CHUNK_SIZE = 1024 * 1024
LOGIN_TTL_SECONDS = 300


class _LoginAttempt:
    def __init__(
        self,
        client: Client,
        phone: str,
        code_hash: str,
        channel_ref: str,
    ) -> None:
        self.client = client
        self.phone = phone
        self.code_hash = code_hash
        self.channel_ref = channel_ref
        self.expires_at = time.monotonic() + LOGIN_TTL_SECONDS
        self.failed_attempts = 0


class _TelegramChannelResolutionError(ConnectionFailedError):
    http_status = 422


@asynccontextmanager
async def _connected_client(config: dict[str, Any]) -> AsyncIterator[Client]:
    """Connect using a Kurigram session string and always stop the client.

    One connection per call -- simple and stateless (matches every other
    method in this backend receiving `config` fresh each time), at the
    cost of reconnecting per request. Pooling connections per connection
    config is a reasonable later optimization, not correctness-critical;
    not done here to avoid holding per-config state a plugin process
    otherwise has no reason to keep.
    """
    async with Client(
        "umedia-telegram",
        api_id=int(config["api_id"]),
        api_hash=str(config["api_hash"]),
        session_string=str(config["session"]),
        no_updates=True,
    ) as client:
        if await client.get_me() is None:
            raise ConnectionFailedError("Telegram session is not authorized")
        yield client


async def _admin_channel(client: Client, config: dict[str, Any]) -> int:
    """Return the configured channel after checking the current user's role."""
    channel_id = int(config["channel_id"])
    member = await client.get_chat_member(channel_id, "me")
    if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
        raise ConnectionFailedError("Telegram session must administer the channel")
    return channel_id


async def _resolve_channel(client: Client, channel_ref: str) -> int:
    reference = channel_ref.strip()
    candidate: str | int = reference
    if reference.lstrip("-").isdigit():
        candidate = int(reference)
    elif reference.startswith("@"):
        candidate = reference[1:]

    try:
        chat = await client.get_chat(candidate)
        return int(chat.id)
    except Exception as direct_error:
        matches: list[int] = []
        async for dialog in client.get_dialogs():
            chat = dialog.chat
            title = str(getattr(chat, "title", "")).strip().casefold()
            if title == reference.casefold():
                matches.append(int(chat.id))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise _TelegramChannelResolutionError(
                "Multiple Telegram channels have that name; use the channel @username",
            ) from direct_error
        raise _TelegramChannelResolutionError(
            "Telegram channel was not found; check its name or @username and "
            "make sure you have joined it",
        ) from direct_error


def _filename(message: Any) -> str:  # noqa: ANN401
    document = getattr(message, "document", None)
    if document is not None and document.file_name:
        return document.file_name
    return f"telegram-{message.id}"


def _to_resource(message: Any) -> Resource:  # noqa: ANN401
    document = getattr(message, "document", None)
    return Resource(
        id=str(message.id),
        type="file",
        name=_filename(message),
        parent_id=None,  # flat: see module docstring
        size=document.file_size if document is not None else None,
        content_type=document.mime_type if document is not None else None,
    )


def _parse_range(range_header: str | None) -> tuple[int, int | None]:
    """Parse an HTTP byte range as offset and byte count."""
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


class TelegramBackend(PluginBackend, TelegramLoginPluginBackend):
    """Resources are documents (media messages) in one admin-owned channel."""

    def __init__(self) -> None:
        self._login_attempts: dict[str, _LoginAttempt] = {}
        self._login_sweeper: asyncio.Task[None] | None = None

    async def startup(self) -> None:
        self._login_sweeper = asyncio.create_task(self._sweep_login_attempts())

    async def shutdown(self) -> None:
        if self._login_sweeper is not None:
            self._login_sweeper.cancel()
            with suppress(asyncio.CancelledError):
                await self._login_sweeper
        for login_id in list(self._login_attempts):
            await self.login_cancel(login_id)

    async def _close_login(self, login_id: str) -> None:
        attempt = self._login_attempts.pop(login_id, None)
        if attempt is not None:
            with suppress(Exception):
                if attempt.client.is_connected:
                    await attempt.client.disconnect()

    async def _sweep_login_attempts(self) -> None:
        while True:
            await asyncio.sleep(30)
            now = time.monotonic()
            expired = [
                login_id
                for login_id, attempt in self._login_attempts.items()
                if attempt.expires_at <= now
            ]
            for login_id in expired:
                await self._close_login(login_id)

    def _get_login(self, login_id: str) -> _LoginAttempt:
        attempt = self._login_attempts.get(login_id)
        if attempt is None or attempt.expires_at <= time.monotonic():
            raise ConnectionFailedError("Telegram login expired; start again")
        return attempt

    async def login_start(
        self,
        config: dict[str, Any],
        login_id: str,
        phone: str,
        channel_ref: str,
    ) -> dict[str, str]:
        await self._close_login(login_id)
        client = Client(
            f"umedia-login-{uuid.uuid4().hex}",
            api_id=int(config["api_id"]),
            api_hash=str(config["api_hash"]),
            in_memory=True,
            no_updates=True,
        )
        try:
            await client.connect()
            sent_code = await client.send_code(phone)
        except Exception:
            if client.is_connected:
                await client.disconnect()
            raise
        self._login_attempts[login_id] = _LoginAttempt(
            client,
            phone,
            sent_code.phone_code_hash,
            channel_ref,
        )
        return {"step": "code"}

    async def _finish_login(
        self,
        login_id: str,
        attempt: _LoginAttempt,
    ) -> dict[str, str]:
        if await attempt.client.get_me() is None:
            await self._close_login(login_id)
            raise ConnectionFailedError("Telegram login was not authorized")
        try:
            channel_id = await _resolve_channel(attempt.client, attempt.channel_ref)
            await _admin_channel(attempt.client, {"channel_id": str(channel_id)})
            session = await attempt.client.export_session_string()
        except Exception:
            await self._close_login(login_id)
            raise
        await self._close_login(login_id)
        return {
            "step": "complete",
            "session": session,
            "channel_id": str(channel_id),
        }

    async def login_code(
        self,
        _config: dict[str, Any],
        login_id: str,
        code: str,
    ) -> dict[str, str]:
        attempt = self._get_login(login_id)
        try:
            await attempt.client.sign_in(attempt.phone, attempt.code_hash, code)
        except SessionPasswordNeeded:
            return {"step": "password"}
        except (PhoneCodeExpired, PhoneCodeInvalid) as error:
            attempt.failed_attempts += 1
            if attempt.failed_attempts >= 3:
                await self._close_login(login_id)
            raise ConnectionFailedError(
                "Telegram login code is invalid or expired",
            ) from error
        except Exception:
            attempt.failed_attempts += 1
            if attempt.failed_attempts >= 3:
                await self._close_login(login_id)
            raise
        return await self._finish_login(login_id, attempt)

    async def login_password(
        self,
        _config: dict[str, Any],
        login_id: str,
        password: str,
    ) -> dict[str, str]:
        attempt = self._get_login(login_id)
        try:
            await attempt.client.check_password(password)
        except PasswordHashInvalid as error:
            attempt.failed_attempts += 1
            if attempt.failed_attempts >= 3:
                await self._close_login(login_id)
            raise ConnectionFailedError(
                "Telegram two-step password is incorrect"
            ) from error
        except Exception:
            attempt.failed_attempts += 1
            if attempt.failed_attempts >= 3:
                await self._close_login(login_id)
            raise
        return await self._finish_login(login_id, attempt)

    async def login_cancel(self, login_id: str) -> None:
        await self._close_login(login_id)

    async def connect(self, config: dict[str, Any]) -> None:
        async with _connected_client(config) as client:
            await _admin_channel(client, config)

    async def list_resources(
        self,
        config: dict[str, Any],
        *,
        parent_id: str | None,
    ) -> list[Resource]:
        if parent_id:
            # Flat namespace: nothing has ever been "inside" another item.
            raise ResourceNotFoundError(parent_id)
        async with _connected_client(config) as client:
            channel = await _admin_channel(client, config)
            messages = client.get_chat_history(channel, limit=LIST_LIMIT)
            return [
                _to_resource(message)
                async for message in messages
                if getattr(message, "document", None)
            ]

    async def get_resource(self, config: dict[str, Any], resource_id: str) -> Resource:
        async with _connected_client(config) as client:
            channel = await _admin_channel(client, config)
            message = await client.get_messages(
                channel,
                message_ids=int(resource_id),
            )
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
            message = await client.get_messages(
                channel,
                message_ids=int(resource_id),
            )
            if message is None or not getattr(message, "document", None):
                raise ResourceNotFoundError(resource_id)
            offset, limit = _parse_range(range_header)
            chunk_offset = offset // CHUNK_SIZE
            chunk_skip = offset % CHUNK_SIZE
            chunk_limit = (
                math.ceil((chunk_skip + limit) / CHUNK_SIZE) if limit is not None else 0
            )
            remaining = limit
            async for chunk in client.stream_media(
                message,
                offset=chunk_offset,
                limit=chunk_limit,
            ):
                if chunk_skip:
                    chunk = chunk[chunk_skip:]
                    chunk_skip = 0
                if remaining is not None:
                    chunk = chunk[:remaining]
                    remaining -= len(chunk)
                yield chunk
                if remaining == 0:
                    break

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
            message = await client.send_document(
                channel,
                document=io.BytesIO(body),
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
            message = await client.get_messages(
                channel,
                message_ids=int(resource_id),
            )
            if message is None or not getattr(message, "document", None):
                raise ResourceNotFoundError(resource_id)

            new_name = changes.name or _filename(message)
            if content is not None:
                body = b"".join([chunk async for chunk in content])
            else:
                # No in-place rename (see module docstring): re-upload the
                # existing bytes under the new name.
                buffer = io.BytesIO()
                async for chunk in client.stream_media(message):
                    buffer.write(chunk)
                body = buffer.getvalue()

            await client.delete_messages(channel, [message.id])
            new_message = await client.send_document(
                channel,
                document=io.BytesIO(body),
                file_name=new_name,
                force_document=True,
            )
            return _to_resource(new_message)

    async def delete_resource(self, config: dict[str, Any], resource_id: str) -> None:
        async with _connected_client(config) as client:
            channel = await _admin_channel(client, config)
            message = await client.get_messages(
                channel,
                message_ids=int(resource_id),
            )
            if message is None or not getattr(message, "document", None):
                raise ResourceNotFoundError(resource_id)
            await client.delete_messages(channel, [message.id])
