"""Unit tests for TelegramBackend against a fake Kurigram client.

No live Telegram account/session is possible in this environment -- these
tests verify UMedia's own glue logic (the right Kurigram calls, mapped
into the right Resource shape), not Kurigram itself. See
plugins/telegram/backend.py's module docstring.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import ExitStack
from dataclasses import dataclass
from types import SimpleNamespace
from typing import BinaryIO
from unittest.mock import patch

import pytest
from pyrogram.enums import ChatMemberStatus

from plugins.contracts import (
    ConnectionFailedError,
    CreateResourceIn,
    ResourceNotFoundError,
    UpdateResourceIn,
)
from plugins.telegram.backend import TelegramBackend

CONFIG = {
    "api_id": "1",
    "api_hash": "hash",
    "channel_id": "100",
    "session": "session-string",
}


class _FakeDocument:
    def __init__(self, data: bytes, name: str) -> None:
        self.size = len(data)
        self.file_size = len(data)
        self.file_name = name
        self.mime_type = "application/octet-stream"
        self.data = data


@dataclass
class _FakeMessage:
    id: int
    document: _FakeDocument | None


@dataclass
class _FakeMember:
    status: ChatMemberStatus


class FakeTelegramClient:
    def __init__(
        self,
        *_args: object,
        authorized: bool = True,
        admin: bool = True,
        **_kwargs: object,
    ) -> None:
        self.authorized = authorized
        self.admin = admin
        self.connected = False
        self.messages: dict[int, _FakeMessage] = {}
        self._next_id = 1
        self.last_sign_in_code: str | None = None

    async def get_chat(self, channel_ref: str | int) -> SimpleNamespace:
        if channel_ref == "A Channel":
            raise ValueError("not a username")
        assert channel_ref in {"my_channel", -100123}
        return SimpleNamespace(id=-100123)

    async def get_dialogs(self) -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(chat=SimpleNamespace(id=-100123, title="A Channel"))

    async def __aenter__(self) -> FakeTelegramClient:
        await self.connect()
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.disconnect()

    @property
    def is_connected(self) -> bool:
        return self.connected

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def send_code(self, _phone: str) -> SimpleNamespace:
        return SimpleNamespace(phone_code_hash="hash")

    async def sign_in(self, _phone: str, _code_hash: str, _code: str) -> None:
        self.last_sign_in_code = _code

    async def check_password(self, _password: str) -> None:
        return None

    async def export_session_string(self) -> str:
        return "exported-session"

    async def get_me(self) -> str | None:
        return "me" if self.authorized else None

    async def get_chat_member(self, _channel: int, _who: str) -> _FakeMember:
        status = (
            ChatMemberStatus.ADMINISTRATOR if self.admin else ChatMemberStatus.MEMBER
        )
        return _FakeMember(status=status)

    async def get_chat_history(
        self,
        _channel: str,
        *,
        limit: int,
    ) -> AsyncIterator[_FakeMessage]:
        for message in sorted(
            self.messages.values(),
            key=lambda m: m.id,
            reverse=True,
        )[:limit]:
            yield message

    async def get_messages(
        self,
        _channel: str,
        *,
        message_ids: int,
    ) -> _FakeMessage | None:
        return self.messages.get(message_ids)

    async def stream_media(
        self,
        message: _FakeMessage,
        *,
        offset: int = 0,
        limit: int = 0,
    ) -> AsyncIterator[bytes]:
        assert message.document is not None
        start = offset * (1024 * 1024)
        end = (
            len(message.document.data) if limit == 0 else start + limit * (1024 * 1024)
        )
        yield message.document.data[start:end]

    async def send_document(
        self,
        _channel: str,
        *,
        document: BinaryIO,
        file_name: str,
        force_document: bool,
    ) -> _FakeMessage:
        body = document.read()
        message = _FakeMessage(self._next_id, _FakeDocument(body, file_name))
        self.messages[message.id] = message
        self._next_id += 1
        return message

    async def delete_messages(self, _channel: str, ids: list[int]) -> None:
        for message_id in ids:
            self.messages.pop(message_id, None)


async def _content(chunks: list[bytes]) -> AsyncIterator[bytes]:  # noqa: RUF029
    for chunk in chunks:
        yield chunk


def _patched(client: FakeTelegramClient) -> ExitStack:
    stack = ExitStack()
    stack.enter_context(
        patch("plugins.telegram.backend.Client", return_value=client),
    )
    return stack


@pytest.mark.asyncio
async def test_connect_succeeds_when_authorized_and_admin() -> None:
    client = FakeTelegramClient()
    with _patched(client):
        await TelegramBackend().connect(CONFIG)
    assert client.connected is False  # always disconnected after the call


@pytest.mark.asyncio
async def test_connect_rejects_an_unauthorized_session() -> None:
    client = FakeTelegramClient(authorized=False)
    with _patched(client), pytest.raises(ConnectionFailedError, match="not authorized"):
        await TelegramBackend().connect(CONFIG)


@pytest.mark.asyncio
async def test_connect_rejects_a_non_admin_session() -> None:
    client = FakeTelegramClient(admin=False)
    with _patched(client), pytest.raises(ConnectionFailedError, match="administer"):
        await TelegramBackend().connect(CONFIG)


@pytest.mark.asyncio
async def test_create_list_get_read_round_trip() -> None:
    client = FakeTelegramClient()
    backend = TelegramBackend()
    with _patched(client):
        created = await backend.create_resource(
            CONFIG,
            CreateResourceIn(name="hello.txt"),
            content=_content([b"hi"]),
        )
        assert created.name == "hello.txt"
        assert created.size == 2

        listing = await backend.list_resources(CONFIG, parent_id=None)
        assert [r.id for r in listing] == [created.id]

        fetched = await backend.get_resource(CONFIG, created.id)
        assert fetched.name == "hello.txt"

        content = b"".join([
            chunk
            async for chunk in backend.read_content(
                CONFIG,
                created.id,
                range_header=None,
            )
        ])
        assert content == b"hi"

        ranged_content = b"".join([
            chunk
            async for chunk in backend.read_content(
                CONFIG,
                created.id,
                range_header="bytes=1-1",
            )
        ])
        assert ranged_content == b"i"


@pytest.mark.asyncio
async def test_create_folder_is_rejected() -> None:
    client = FakeTelegramClient()
    with _patched(client), pytest.raises(ConnectionFailedError, match="folders"):
        await TelegramBackend().create_resource(
            CONFIG,
            CreateResourceIn(name="dir", type="folder"),
            content=_content([]),
        )


@pytest.mark.asyncio
async def test_list_with_a_parent_id_is_rejected() -> None:
    client = FakeTelegramClient()
    with _patched(client), pytest.raises(ResourceNotFoundError):
        await TelegramBackend().list_resources(CONFIG, parent_id="something")


@pytest.mark.asyncio
async def test_get_missing_resource_raises_not_found() -> None:
    client = FakeTelegramClient()
    with _patched(client), pytest.raises(ResourceNotFoundError):
        await TelegramBackend().get_resource(CONFIG, "999")


@pytest.mark.asyncio
async def test_update_with_content_replaces_and_gets_a_new_id() -> None:
    client = FakeTelegramClient()
    backend = TelegramBackend()
    with _patched(client):
        created = await backend.create_resource(
            CONFIG,
            CreateResourceIn(name="a.txt"),
            content=_content([b"old"]),
        )

        updated = await backend.update_resource(
            CONFIG,
            created.id,
            UpdateResourceIn(name="b.txt"),
            content=_content([b"new"]),
        )

        assert updated.name == "b.txt"
        assert updated.id != created.id  # no in-place edit; old message is gone
        with pytest.raises(ResourceNotFoundError):
            await backend.get_resource(CONFIG, created.id)
        new_content = b"".join([
            chunk
            async for chunk in backend.read_content(
                CONFIG,
                updated.id,
                range_header=None,
            )
        ])
        assert new_content == b"new"


@pytest.mark.asyncio
async def test_rename_without_content_reuploads_the_same_bytes() -> None:
    client = FakeTelegramClient()
    backend = TelegramBackend()
    with _patched(client):
        created = await backend.create_resource(
            CONFIG,
            CreateResourceIn(name="a.txt"),
            content=_content([b"unchanged"]),
        )

        renamed = await backend.update_resource(
            CONFIG,
            created.id,
            UpdateResourceIn(name="b.txt"),
            content=None,
        )

        assert renamed.name == "b.txt"
        content = b"".join([
            chunk
            async for chunk in backend.read_content(
                CONFIG,
                renamed.id,
                range_header=None,
            )
        ])
        assert content == b"unchanged"


@pytest.mark.asyncio
async def test_delete_removes_the_message() -> None:
    client = FakeTelegramClient()
    backend = TelegramBackend()
    with _patched(client):
        created = await backend.create_resource(
            CONFIG,
            CreateResourceIn(name="a.txt"),
            content=_content([b"x"]),
        )

        await backend.delete_resource(CONFIG, created.id)

        with pytest.raises(ResourceNotFoundError):
            await backend.get_resource(CONFIG, created.id)


@pytest.mark.asyncio
async def test_login_code_exports_session_and_closes_temporary_client() -> None:
    client = FakeTelegramClient()
    backend = TelegramBackend()
    with _patched(client):
        await backend.login_start(
            {"api_id": "1", "api_hash": "hash"},
            "login-id",
            "+1234567890",
            "@my_channel",
        )
        result = await backend.login_code({}, "login-id", "12345")

    assert result == {
        "step": "complete",
        "session": "exported-session",
        "channel_id": "-100123",
    }
    assert client.connected is False
    assert "login-id" not in backend._login_attempts


@pytest.mark.asyncio
async def test_login_resolves_channel_title_from_joined_dialogs() -> None:
    client = FakeTelegramClient()
    backend = TelegramBackend()
    with _patched(client):
        await backend.login_start(
            {"api_id": "1", "api_hash": "hash"},
            "login-id",
            "+1234567890",
            "A Channel",
        )
        result = await backend.login_code({}, "login-id", "12345")

    assert result["channel_id"] == "-100123"


@pytest.mark.asyncio
async def test_login_returns_password_step_when_telegram_requires_two_factor() -> None:
    from pyrogram.errors import SessionPasswordNeeded

    client = FakeTelegramClient()
    backend = TelegramBackend()
    with (
        _patched(client),
        patch.object(
            client,
            "sign_in",
            side_effect=SessionPasswordNeeded,
        ),
    ):
        await backend.login_start(
            {"api_id": "1", "api_hash": "hash"},
            "login-id",
            "+1234567890",
            "-100123",
        )
        result = await backend.login_code({}, "login-id", "12345")

    assert result == {"step": "password"}
    assert client.connected is True
    result = await backend.login_password({}, "login-id", "two-step-password")
    assert result == {
        "step": "complete",
        "session": "exported-session",
        "channel_id": "-100123",
    }
    assert client.connected is False


@pytest.mark.asyncio
async def test_invalid_login_code_returns_a_safe_provider_error() -> None:
    from pyrogram.errors import PhoneCodeInvalid

    client = FakeTelegramClient()
    backend = TelegramBackend()
    with (
        _patched(client),
        patch.object(client, "sign_in", side_effect=PhoneCodeInvalid),
    ):
        await backend.login_start(
            {"api_id": "1", "api_hash": "hash"},
            "login-id",
            "+1234567890",
            "-100123",
        )
        with pytest.raises(ConnectionFailedError, match="invalid or expired"):
            await backend.login_code({}, "login-id", "wrong")

    assert client.connected is True
    await backend.login_cancel("login-id")
    assert client.connected is False


@pytest.mark.asyncio
async def test_login_code_normalizes_unicode_digits_and_copy_formatting() -> None:
    client = FakeTelegramClient()
    backend = TelegramBackend()
    with _patched(client):
        await backend.login_start(
            {"api_id": "1", "api_hash": "hash"},
            "login-id",
            "+1234567890",
            "-100123",
        )
        await backend.login_code({}, "login-id", " \u06f1\u06f2-\u0663\u0664\u06f5 ")

    assert client.last_sign_in_code == "12345"


@pytest.mark.asyncio
async def test_delete_missing_resource_raises_not_found() -> None:
    client = FakeTelegramClient()
    with _patched(client), pytest.raises(ResourceNotFoundError):
        await TelegramBackend().delete_resource(CONFIG, "999")
