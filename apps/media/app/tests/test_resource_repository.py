"""Real-SQLite integration tests for ResourceRepository -- exercises the
actual SQL layer, complementing test_resource_service.py's fake-repository
unit tests (which cover the business rules)."""

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi_mongo_base.sql.models import BaseEntity
from sqlalchemy.ext.asyncio import async_sessionmaker

from apps.resources.models import Resource  # noqa: F401
from apps.resources.repository import ResourceRepository
from apps.resources.schemas import HistoryEntry
from server.database import create_engine, create_session_factory


@pytest_asyncio.fixture
async def repository(tmp_path: Path) -> AsyncGenerator[ResourceRepository]:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'resources-test.sqlite3'}")
    async with engine.begin() as connection:
        await connection.run_sync(BaseEntity.metadata.create_all)
    session_factory: async_sessionmaker = create_session_factory(engine)
    yield ResourceRepository(session_factory)
    await engine.dispose()


def _data(**overrides: object) -> dict[str, object]:
    base = {
        "provider_connection_id": "connection-1",
        "owner_id": "user-1",
        "type": "file",
        "name": "hello.txt",
        "parent_id": None,
        "content_reference": None,
        "content_hash": None,
        "content_type": "text/plain",
        "size": 0,
        "status": "processing",
        "error": None,
        "public_permission": "none",
        "permissions": [],
        "workspace_id": None,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_create_then_get(repository: ResourceRepository) -> None:
    created = await repository.create(_data())

    fetched = await repository.get(created.uid)

    assert fetched is not None
    assert fetched.name == "hello.txt"
    assert fetched.status == "processing"
    assert fetched.metadata == {}
    assert fetched.history == []
    assert fetched.owner_id == "user-1"
    assert fetched.permissions == []
    assert fetched.workspace_id is None


@pytest.mark.asyncio
async def test_acl_columns_round_trip(repository: ResourceRepository) -> None:
    created = await repository.create(_data(workspace_id="workspace-9"))

    updated = await repository.update(created.uid, {
        "permissions": [{"user_id": "user-2", "permission": 10}],
    })

    assert updated.workspace_id == "workspace-9"
    assert updated.permissions == [{"user_id": "user-2", "permission": 10}]

    fetched = await repository.get(created.uid)
    assert fetched.permissions == [{"user_id": "user-2", "permission": 10}]


@pytest.mark.asyncio
async def test_get_missing_returns_none(repository: ResourceRepository) -> None:
    assert await repository.get("does-not-exist") is None


@pytest.mark.asyncio
async def test_list_filters_by_parent_and_excludes_deleted_by_default(
    repository: ResourceRepository,
) -> None:
    folder = await repository.create(_data(name="folder", type="folder"))
    child = await repository.create(_data(name="child.txt", parent_id=folder.uid))
    await repository.create(_data(name="root.txt"))  # different parent

    listed = await repository.list(parent_id=folder.uid)

    assert [r.uid for r in listed] == [child.uid]

    await repository.soft_delete(child.uid)
    assert await repository.list(parent_id=folder.uid) == []
    assert [r.uid for r in await repository.list(
        parent_id=folder.uid, include_deleted=True,
    )] == [child.uid]


@pytest.mark.asyncio
async def test_find_by_content_hash_matches_connection_parent_and_owner(
    repository: ResourceRepository,
) -> None:
    created = await repository.create(
        _data(content_hash="abc123", provider_connection_id="conn-a"),
    )

    found = await repository.find_by_content_hash(
        provider_connection_id="conn-a",
        parent_id=None,
        content_hash="abc123",
        owner_id="user-1",
    )
    wrong_connection = await repository.find_by_content_hash(
        provider_connection_id="conn-b",
        parent_id=None,
        content_hash="abc123",
        owner_id="user-1",
    )
    wrong_owner = await repository.find_by_content_hash(
        provider_connection_id="conn-a",
        parent_id=None,
        content_hash="abc123",
        owner_id="user-2",
    )

    assert found is not None
    assert found.uid == created.uid
    assert wrong_connection is None
    assert wrong_owner is None


@pytest.mark.asyncio
async def test_list_for_actor_scopes(repository: ResourceRepository) -> None:
    mine = await repository.create(_data(name="mine.txt"))
    shared_to_me = await repository.create(_data(
        name="shared-to-me.txt",
        owner_id="user-2",
        permissions=[{"user_id": "user-1", "permission": 10}],
    ))
    await repository.create(_data(name="theirs.txt", owner_id="user-2"))
    shared_by_me = await repository.create(_data(
        name="shared-by-me.txt",
        permissions=[{"user_id": "user-2", "permission": 20}],
    ))

    owned = await repository.list_for_actor(actor_user_id="user-1", scope="owned")
    assert [r.uid for r in owned] == [mine.uid, shared_by_me.uid]

    shared_with_me = await repository.list_for_actor(
        actor_user_id="user-1", scope="shared_with_me",
    )
    assert [r.uid for r in shared_with_me] == [shared_to_me.uid]

    outgoing = await repository.list_for_actor(
        actor_user_id="user-1", scope="shared_by_me",
    )
    assert [r.uid for r in outgoing] == [shared_by_me.uid]

    visible = await repository.list_for_actor(
        actor_user_id="user-1", scope="all_visible",
    )
    assert {r.uid for r in visible} == {mine.uid, shared_to_me.uid, shared_by_me.uid}


@pytest.mark.asyncio
async def test_list_for_actor_owned_include_deleted_is_the_trash_view(
    repository: ResourceRepository,
) -> None:
    kept = await repository.create(_data(name="kept.txt"))
    trashed = await repository.create(_data(name="trashed.txt"))
    await repository.soft_delete(trashed.uid)

    active = await repository.list_for_actor(actor_user_id="user-1", scope="owned")
    assert [r.uid for r in active] == [kept.uid]

    with_trash = await repository.list_for_actor(
        actor_user_id="user-1", scope="owned", include_deleted=True,
    )
    assert {r.uid for r in with_trash} == {kept.uid, trashed.uid}


@pytest.mark.asyncio
async def test_update_applies_changes_including_metadata_and_history(
    repository: ResourceRepository,
) -> None:
    created = await repository.create(_data())

    updated = await repository.update(created.uid, {
        "status": "completed",
        "content_reference": "remote-1",
        "metadata": {"channel_id": "100"},
        "history": [
            HistoryEntry(
                content_reference="old-ref",
                content_hash="old-hash",
                content_type="text/plain",
                size=3,
            ),
        ],
    })

    assert updated.status == "completed"
    assert updated.content_reference == "remote-1"
    assert updated.metadata == {"channel_id": "100"}
    assert updated.history == [
        HistoryEntry(
            content_reference="old-ref",
            content_hash="old-hash",
            content_type="text/plain",
            size=3,
        ),
    ]


@pytest.mark.asyncio
async def test_touch_access_updates_the_timestamp(
    repository: ResourceRepository,
) -> None:
    created = await repository.create(_data())
    before = created.access_at

    await repository.touch_access(created.uid)

    after = await repository.get(created.uid)
    assert after.access_at >= before


@pytest.mark.asyncio
async def test_soft_delete_then_restore(repository: ResourceRepository) -> None:
    created = await repository.create(_data())

    await repository.soft_delete(created.uid)
    deleted = await repository.get(created.uid)
    assert deleted.is_deleted is True
    assert deleted.deleted_at is not None

    await repository.restore(created.uid)
    restored = await repository.get(created.uid)
    assert restored.is_deleted is False
    assert restored.deleted_at is None


@pytest.mark.asyncio
async def test_hard_delete_removes_the_row(repository: ResourceRepository) -> None:
    created = await repository.create(_data())

    await repository.hard_delete(created.uid)

    assert await repository.get(created.uid) is None


@pytest.mark.asyncio
async def test_volume_stats_splits_active_and_deleted(
    repository: ResourceRepository,
) -> None:
    kept = await repository.create(_data(name="kept.txt", size=5))
    removed = await repository.create(_data(name="removed.txt", size=10))
    await repository.soft_delete(removed.uid)

    stats = await repository.volume_stats()

    assert stats["active_size"] == 5
    assert stats["active_count"] == 1
    assert stats["deleted_size"] == 10
    assert stats["deleted_count"] == 1
    assert kept.status == "processing"
