"""Real-SQLite tests for StorageObjectRepository -- the physical provider
index (docs/11-dual-layer-library.md). One row per remote object per
connection, keyed by `(provider_connection_id, content_reference)`."""

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi_mongo_base.sql.models import BaseEntity

from apps.storage_objects.repository import StorageObjectRepository
from server.database import create_engine, create_session_factory


@pytest_asyncio.fixture
async def repository(tmp_path: Path) -> AsyncGenerator[StorageObjectRepository]:
    engine = create_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'storage-objects.sqlite3'}",
    )
    async with engine.begin() as connection:
        await connection.run_sync(BaseEntity.metadata.create_all)
    yield StorageObjectRepository(create_session_factory(engine))
    await engine.dispose()


def _data(**overrides: object) -> dict[str, object]:
    base = {
        "provider_connection_id": "connection-1",
        "content_reference": "folder/hello.txt",
        "provider_parent_ref": "folder",
        "type": "file",
        "name": "hello.txt",
        "content_hash": None,
        "content_type": "text/plain",
        "size": 5,
        "metadata": {},
        "status": "active",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_create_then_get(repository: StorageObjectRepository) -> None:
    created = await repository.create(_data())

    fetched = await repository.get(created.uid)

    assert fetched is not None
    assert fetched.provider_connection_id == "connection-1"
    assert fetched.content_reference == "folder/hello.txt"
    assert fetched.provider_parent_ref == "folder"
    assert fetched.name == "hello.txt"
    assert fetched.size == 5
    assert fetched.metadata == {}
    assert fetched.last_seen_at is not None
    assert fetched.is_deleted is False


@pytest.mark.asyncio
async def test_get_by_reference_is_scoped_to_the_connection(
    repository: StorageObjectRepository,
) -> None:
    created = await repository.create(_data())

    same_connection = await repository.get_by_reference(
        provider_connection_id="connection-1",
        content_reference="folder/hello.txt",
    )
    other_connection = await repository.get_by_reference(
        provider_connection_id="connection-2",
        content_reference="folder/hello.txt",
    )

    assert same_connection is not None
    assert same_connection.uid == created.uid
    assert other_connection is None


@pytest.mark.asyncio
async def test_upsert_creates_then_updates_in_place(
    repository: StorageObjectRepository,
) -> None:
    first = await repository.upsert(_data(size=5))
    second = await repository.upsert(_data(size=99, name="renamed.txt"))

    assert second.uid == first.uid  # same physical object, updated
    assert second.size == 99
    assert second.name == "renamed.txt"
    assert second.last_seen_at >= first.last_seen_at

    listed = await repository.list(provider_connection_id="connection-1")
    assert [row.uid for row in listed] == [first.uid]


@pytest.mark.asyncio
async def test_list_filters_by_connection_and_parent_ref(
    repository: StorageObjectRepository,
) -> None:
    in_folder = await repository.upsert(_data())
    at_root = await repository.upsert(_data(
        content_reference="root.txt", provider_parent_ref=None, name="root.txt",
    ))
    await repository.upsert(_data(
        provider_connection_id="connection-2",
        content_reference="other.txt",
        provider_parent_ref=None,
        name="other.txt",
    ))

    everything = await repository.list(provider_connection_id="connection-1")
    assert {row.uid for row in everything} == {in_folder.uid, at_root.uid}

    only_root = await repository.list(
        provider_connection_id="connection-1", parent_ref=None,
        filter_by_parent=True,
    )
    assert [row.uid for row in only_root] == [at_root.uid]

    only_folder = await repository.list(
        provider_connection_id="connection-1", parent_ref="folder",
        filter_by_parent=True,
    )
    assert [row.uid for row in only_folder] == [in_folder.uid]


@pytest.mark.asyncio
async def test_update_applies_changes(
    repository: StorageObjectRepository,
) -> None:
    created = await repository.create(_data())

    updated = await repository.update(created.uid, {
        "content_reference": "moved/hello.txt",
        "provider_parent_ref": "moved",
        "content_hash": "abc123",
    })

    assert updated.content_reference == "moved/hello.txt"
    assert updated.provider_parent_ref == "moved"
    assert updated.content_hash == "abc123"


@pytest.mark.asyncio
async def test_soft_delete_hides_from_list_but_get_still_returns(
    repository: StorageObjectRepository,
) -> None:
    created = await repository.create(_data())

    await repository.soft_delete(created.uid)

    assert await repository.list(provider_connection_id="connection-1") == []
    still_there = await repository.get(created.uid)
    assert still_there is not None
    assert still_there.is_deleted is True
    assert still_there.deleted_at is not None


@pytest.mark.asyncio
async def test_get_missing_returns_none(
    repository: StorageObjectRepository,
) -> None:
    assert await repository.get("nope") is None
    assert await repository.get_by_reference(
        provider_connection_id="connection-1", content_reference="nope",
    ) is None
