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
import hashlib
import io
import logging
import math
import time
import unicodedata
import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from typing import Any

from pyrogram import Client
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import (
    FloodWait,
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
logger = logging.getLogger(__name__)


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


class _TelegramBusyError(ConnectionFailedError):
    """Telegram asked us to slow down; this session pauses for a while."""

    http_status = 503


# One shared client per Telegram session, not one per request. Opening a
# client (connect + get_me) for every call made ~60 thumbnail requests open
# ~60 MTProto connections at once; Telegram answered with "Transport flood"
# and Kurigram reconnected forever, ~10 errors/s for 16 hours (2026-10-25)
# and again when the poller reached the connection (2026-10-28).
MAX_CONCURRENT_CALLS = 3
CONNECT_TIMEOUT_SECONDS = 30.0
CALL_TIMEOUT_SECONDS = 60.0
IDLE_CLOSE_SECONDS = 600.0
ADMIN_CHECK_TTL_SECONDS = 600.0
# After a timeout (how a transport flood shows up) or a FloodWait shorter
# than this, the session still pauses this long before reconnecting.
FLOOD_COOLDOWN_SECONDS = 60.0


def _now() -> float:
    return time.monotonic()


def _session_key(config: dict[str, Any]) -> str:
    raw = f"{config['api_id']}|{config['api_hash']}|{config['session']}"
    return hashlib.sha256(raw.encode()).hexdigest()


class _PooledClient:
    """A started client shared by every call for one session."""

    def __init__(self, client: Client, stack: AsyncExitStack) -> None:
        self.client = client
        self.stack = stack
        self.calls = asyncio.Semaphore(MAX_CONCURRENT_CALLS)
        self.in_use = 0
        self.last_used = _now()
        self.admin_channel: int | None = None
        self.admin_checked_at = 0.0

    async def close(self) -> None:
        with suppress(Exception):
            await self.stack.aclose()


class _ClientPool:
    def __init__(self) -> None:
        self._clients: dict[str, _PooledClient] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._paused_until: dict[str, float] = {}

    def _check_paused(self, key: str) -> None:
        wait = self._paused_until.get(key, 0.0) - _now()
        if wait > 0:
            raise _TelegramBusyError(
                f"Telegram asked to slow down; retry in {math.ceil(wait)}s",
            )

    async def _drop(self, key: str, pause_seconds: float) -> None:
        self._paused_until[key] = _now() + pause_seconds
        pooled = self._clients.pop(key, None)
        if pooled is not None:
            # Stopping the client also ends Kurigram's reconnect loop.
            await pooled.close()

    async def _open(self, key: str, config: dict[str, Any]) -> _PooledClient:
        async with self._locks.setdefault(key, asyncio.Lock()):
            pooled = self._clients.get(key)
            if pooled is not None:
                return pooled
            self._check_paused(key)
            client = Client(
                "umedia-telegram",
                api_id=int(config["api_id"]),
                api_hash=str(config["api_hash"]),
                session_string=str(config["session"]),
                no_updates=True,
                in_memory=True,
            )
            stack = AsyncExitStack()
            try:
                await asyncio.wait_for(
                    stack.enter_async_context(client),
                    CONNECT_TIMEOUT_SECONDS,
                )
                me = await asyncio.wait_for(
                    client.get_me(), CONNECT_TIMEOUT_SECONDS,
                )
            except BaseException:
                with suppress(Exception):
                    await stack.aclose()
                raise
            if me is None:
                await stack.aclose()
                raise ConnectionFailedError("Telegram session is not authorized")
            pooled = _PooledClient(client, stack)
            self._clients[key] = pooled
            return pooled

    @asynccontextmanager
    async def use(self, config: dict[str, Any]) -> AsyncIterator[_PooledClient]:
        """Borrow the session's shared client, at most
        MAX_CONCURRENT_CALLS at a time. A FloodWait or a timeout closes the
        client and pauses the session, so callers get a fast 503 instead
        of piling more connections onto Telegram."""
        key = _session_key(config)
        self._check_paused(key)
        try:
            pooled = await self._open(key, config)
            async with pooled.calls:
                self._check_paused(key)
                pooled.in_use += 1
                try:
                    yield pooled
                finally:
                    pooled.in_use -= 1
                    pooled.last_used = _now()
        except FloodWait as error:
            wait = max(float(getattr(error, "value", 0) or 0), FLOOD_COOLDOWN_SECONDS)
            logger.warning("Telegram FloodWait %ss; pausing the session", wait)
            await self._drop(key, wait)
            raise _TelegramBusyError(
                f"Telegram asked to slow down; retry in {math.ceil(wait)}s",
            ) from error
        except TimeoutError as error:
            logger.warning("Telegram call timed out; pausing the session")
            await self._drop(key, FLOOD_COOLDOWN_SECONDS)
            raise _TelegramBusyError(
                "Telegram did not answer in time; retry shortly",
            ) from error

    async def close_idle(self) -> None:
        now = _now()
        for key, pooled in list(self._clients.items()):
            if pooled.in_use == 0 and now - pooled.last_used > IDLE_CLOSE_SECONDS:
                self._clients.pop(key, None)
                await pooled.close()

    async def close_all(self) -> None:
        clients = list(self._clients.values())
        self._clients.clear()
        for pooled in clients:
            await pooled.close()


async def _call(awaitable: Any) -> Any:  # noqa: ANN401
    """One Telegram request with a deadline (a hung call is a flood sign)."""
    return await asyncio.wait_for(awaitable, CALL_TIMEOUT_SECONDS)


async def _admin_channel(
    pooled: _PooledClient,
    config: dict[str, Any],
    *,
    fresh: bool = False,
) -> int:
    """Return the configured channel after checking the current user's
    role; the answer is reused for ADMIN_CHECK_TTL_SECONDS (`fresh` forces
    a new check, as `connect()` does)."""
    channel_id = int(config["channel_id"])
    now = _now()
    if (
        not fresh
        and pooled.admin_channel == channel_id
        and now - pooled.admin_checked_at < ADMIN_CHECK_TTL_SECONDS
    ):
        return channel_id
    member = await _call(pooled.client.get_chat_member(channel_id, "me"))
    if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
        pooled.admin_channel = None
        raise ConnectionFailedError("Telegram session must administer the channel")
    pooled.admin_channel = channel_id
    pooled.admin_checked_at = now
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
        self._pool = _ClientPool()

    async def startup(self) -> None:
        self._login_sweeper = asyncio.create_task(self._sweep_login_attempts())

    async def shutdown(self) -> None:
        if self._login_sweeper is not None:
            self._login_sweeper.cancel()
            with suppress(asyncio.CancelledError):
                await self._login_sweeper
        for login_id in list(self._login_attempts):
            await self.login_cancel(login_id)
        await self._pool.close_all()

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
            await self._pool.close_idle()

    def _get_login(self, login_id: str) -> _LoginAttempt:
        attempt = self._login_attempts.get(login_id)
        if attempt is None or attempt.expires_at <= time.monotonic():
            logger.warning(
                "Telegram login step rejected",
                extra={"phase": "state_lookup", "outcome": "expired_or_missing"},
            )
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
        logger.info(
            "Telegram login code sent",
            extra={"phase": "start", "outcome": "code_sent"},
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
        code = "".join(
            str(unicodedata.decimal(character)) if character.isdecimal() else character
            for character in code.strip()
            if not character.isspace()
            and character not in {"-", "\u2010", "\u2011", "\u2012", "\u2013", "\u2014"}
        )
        try:
            await attempt.client.sign_in(attempt.phone, attempt.code_hash, code)
        except SessionPasswordNeeded:
            logger.info(
                "Telegram login requires two-step password",
                extra={"phase": "code", "outcome": "password_required"},
            )
            return {"step": "password"}
        except (PhoneCodeExpired, PhoneCodeInvalid) as error:
            logger.warning(
                "Telegram login code rejected",
                extra={
                    "phase": "code",
                    "outcome": "rejected",
                    "reason": type(error).__name__,
                },
            )
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
            logger.warning(
                "Telegram two-step password rejected",
                extra={
                    "phase": "password",
                    "outcome": "rejected",
                    "reason": type(error).__name__,
                },
            )
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
        async with self._pool.use(config) as pooled:
            await _admin_channel(pooled, config, fresh=True)

    async def list_resources(
        self,
        config: dict[str, Any],
        *,
        parent_id: str | None,
    ) -> list[Resource]:
        if parent_id:
            # Flat namespace: nothing has ever been "inside" another item.
            raise ResourceNotFoundError(parent_id)
        async with self._pool.use(config) as pooled:
            channel = await _admin_channel(pooled, config)

            async def _collect() -> list[Resource]:
                messages = pooled.client.get_chat_history(channel, limit=LIST_LIMIT)
                return [
                    _to_resource(message)
                    async for message in messages
                    if getattr(message, "document", None)
                ]

            return await _call(_collect())

    async def _document(
        self,
        pooled: _PooledClient,
        channel: int,
        resource_id: str,
    ) -> Any:  # noqa: ANN401
        message = await _call(
            pooled.client.get_messages(channel, message_ids=int(resource_id)),
        )
        if message is None or not getattr(message, "document", None):
            raise ResourceNotFoundError(resource_id)
        return message

    async def get_resource(self, config: dict[str, Any], resource_id: str) -> Resource:
        async with self._pool.use(config) as pooled:
            channel = await _admin_channel(pooled, config)
            return _to_resource(await self._document(pooled, channel, resource_id))

    async def read_content(
        self,
        config: dict[str, Any],
        resource_id: str,
        *,
        range_header: str | None,
    ) -> AsyncIterator[bytes]:
        async with self._pool.use(config) as pooled:
            channel = await _admin_channel(pooled, config)
            message = await self._document(pooled, channel, resource_id)
            offset, limit = _parse_range(range_header)
            chunk_offset = offset // CHUNK_SIZE
            chunk_skip = offset % CHUNK_SIZE
            chunk_limit = (
                math.ceil((chunk_skip + limit) / CHUNK_SIZE) if limit is not None else 0
            )
            remaining = limit
            async for chunk in pooled.client.stream_media(
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
        async with self._pool.use(config) as pooled:
            channel = await _admin_channel(pooled, config)
            message = await pooled.client.send_document(
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
        async with self._pool.use(config) as pooled:
            channel = await _admin_channel(pooled, config)
            message = await self._document(pooled, channel, resource_id)

            new_name = changes.name or _filename(message)
            if content is not None:
                body = b"".join([chunk async for chunk in content])
            else:
                # No in-place rename (see module docstring): re-upload the
                # existing bytes under the new name.
                buffer = io.BytesIO()
                async for chunk in pooled.client.stream_media(message):
                    buffer.write(chunk)
                body = buffer.getvalue()

            await _call(pooled.client.delete_messages(channel, [message.id]))
            new_message = await pooled.client.send_document(
                channel,
                document=io.BytesIO(body),
                file_name=new_name,
                force_document=True,
            )
            return _to_resource(new_message)

    async def delete_resource(self, config: dict[str, Any], resource_id: str) -> None:
        async with self._pool.use(config) as pooled:
            channel = await _admin_channel(pooled, config)
            message = await self._document(pooled, channel, resource_id)
            await _call(pooled.client.delete_messages(channel, [message.id]))
