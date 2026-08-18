"""Unit tests for TelegramBackend against a fake Telethon client.

No live Telegram account/session is possible in this environment -- these
tests verify UMedia's own glue logic (the right Telethon calls, mapped
into the right Resource shape), not Telethon itself. See
plugins/telegram/backend.py's module docstring.
"""

from collections.abc import AsyncIterator
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest
from telethon.tl.types import DocumentAttributeFilename

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
        self.mime_type = "application/octet-stream"
        self.attributes = [DocumentAttributeFilename(file_name=name)]
        self.data = data


class _FakeMessage:
    def __init__(self, message_id: int, document: _FakeDocument | None) -> None:
        self.id = message_id
        self.document = document

    @property
    def media(self) -> _FakeDocument | None:
        return self.document


@dataclass
class _FakePermissions:
    is_admin: bool
    is_creator: bool = False


class FakeTelegramClient:
    """Stands in for `telethon.TelegramClient` in these tests."""

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

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def is_user_authorized(self) -> bool:
        return self.authorized

    async def get_entity(self, channel_id: int) -> str:
        return f"channel-{channel_id}"

    async def get_permissions(self, _entity: str, _who: str) -> _FakePermissions:
        return _FakePermissions(is_admin=self.admin)

    async def iter_messages(
        self, _channel: str, *, limit: int,
    ) -> AsyncIterator[_FakeMessage]:
        for message in sorted(
            self.messages.values(), key=lambda m: m.id, reverse=True,
        )[:limit]:
            yield message

    async def get_messages(self, _channel: str, *, ids: int) -> _FakeMessage | None:
        return self.messages.get(ids)

    async def iter_download(
        self, media: _FakeDocument, *, offset: int = 0, limit: int | None = None,
    ) -> AsyncIterator[bytes]:
        end = len(media.data) if limit is None else offset + limit
        yield media.data[offset:end]

    async def send_file(
        self, _channel: str, *, file: Any, file_name: str, force_document: bool,  # noqa: ANN401
    ) -> _FakeMessage:
        body = file.read()
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
    """Patch both `TelegramClient` and `StringSession` -- the real
    `StringSession` validates its input string's format eagerly in
    `__init__`, and a plain test string like "session-string" isn't a
    valid one, so it must be faked too, not just the client."""
    stack = ExitStack()
    stack.enter_context(
        patch("plugins.telegram.backend.TelegramClient", return_value=client),
    )
    stack.enter_context(
        patch("plugins.telegram.backend.StringSession", return_value="fake-session"),
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
            CONFIG, CreateResourceIn(name="hello.txt"), content=_content([b"hi"]),
        )
        assert created.name == "hello.txt"
        assert created.size == 2

        listing = await backend.list_resources(CONFIG, parent_id=None)
        assert [r.id for r in listing] == [created.id]

        fetched = await backend.get_resource(CONFIG, created.id)
        assert fetched.name == "hello.txt"

        content = b"".join([
            chunk async for chunk in backend.read_content(
                CONFIG, created.id, range_header=None,
            )
        ])
        assert content == b"hi"


@pytest.mark.asyncio
async def test_create_folder_is_rejected() -> None:
    client = FakeTelegramClient()
    with _patched(client), pytest.raises(ConnectionFailedError, match="folders"):
        await TelegramBackend().create_resource(
            CONFIG, CreateResourceIn(name="dir", type="folder"), content=_content([]),
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
            CONFIG, CreateResourceIn(name="a.txt"), content=_content([b"old"]),
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
            chunk async for chunk in backend.read_content(
                CONFIG, updated.id, range_header=None,
            )
        ])
        assert new_content == b"new"


@pytest.mark.asyncio
async def test_rename_without_content_reuploads_the_same_bytes() -> None:
    client = FakeTelegramClient()
    backend = TelegramBackend()
    with _patched(client):
        created = await backend.create_resource(
            CONFIG, CreateResourceIn(name="a.txt"), content=_content([b"unchanged"]),
        )

        renamed = await backend.update_resource(
            CONFIG, created.id, UpdateResourceIn(name="b.txt"), content=None,
        )

        assert renamed.name == "b.txt"
        content = b"".join([
            chunk async for chunk in backend.read_content(
                CONFIG, renamed.id, range_header=None,
            )
        ])
        assert content == b"unchanged"


@pytest.mark.asyncio
async def test_delete_removes_the_message() -> None:
    client = FakeTelegramClient()
    backend = TelegramBackend()
    with _patched(client):
        created = await backend.create_resource(
            CONFIG, CreateResourceIn(name="a.txt"), content=_content([b"x"]),
        )

        await backend.delete_resource(CONFIG, created.id)

        with pytest.raises(ResourceNotFoundError):
            await backend.get_resource(CONFIG, created.id)


@pytest.mark.asyncio
async def test_delete_missing_resource_raises_not_found() -> None:
    client = FakeTelegramClient()
    with _patched(client), pytest.raises(ResourceNotFoundError):
        await TelegramBackend().delete_resource(CONFIG, "999")
