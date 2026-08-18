"""Recycle-bin behavior: the `trash` list scope (soft-deleted roots,
owner-only), the restore / permanent-delete round trips out of it, and
the nightly purge of items deleted more than 30 days ago.

Same harness as test_media_file_service: real repositories on a
throwaway SQLite, fake plugin gateway.
"""

from collections.abc import AsyncGenerator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

from apps.media_files.api_schemas import MediaFileOut
from apps.media_files.errors import MediaFileNotFoundError
from apps.media_files.schemas import MediaFileRecord
from tests.media_file_helpers import FakeConnection, Harness, build_harness

CONNECTION_ID = "connection-1"
OWNER_ID = "user-1"
OTHER_USER_ID = "user-2"


@pytest_asyncio.fixture
async def harness(tmp_path: Path) -> AsyncGenerator[Harness]:
    built = await build_harness(tmp_path, FakeConnection(uid=CONNECTION_ID))
    yield built
    await built.engine.dispose()


async def _backdate(harness: Harness, uid: str, *, days: int) -> None:
    """Rewind a soft-deleted row's `deleted_at` -- how tests age items
    into (or near) the purge window."""
    await harness.files.update(
        uid, {"deleted_at": datetime.now() - timedelta(days=days)},
    )


async def _trash(harness: Harness, *, actor: str = OWNER_ID,
                 parent_id: str | None = None) -> list[MediaFileRecord]:
    page = await harness.service.list_children(
        parent_id, actor_user_id=actor, scope="trash",
    )
    return page.items


# ----------------------------------------------------------------------
# Listing: deleted roots only, owner-only, newest first
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trash_lists_deleted_roots_but_hides_cascaded_children(
    harness: Harness,
) -> None:
    folder = await harness.service.create_folder(
        name="docs", parent_id=None, owner_id=OWNER_ID,
    )
    child = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=folder.uid,
        name="child.txt",
        content=b"x",
        owner_id=OWNER_ID,
    )
    loose = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="loose.txt",
        content=b"y",
        owner_id=OWNER_ID,
    )

    await harness.service.soft_delete(folder.uid, actor_user_id=OWNER_ID)
    await harness.service.soft_delete(loose.uid, actor_user_id=OWNER_ID)

    items = await _trash(harness)

    # Roots only: the cascaded child stays hidden behind its folder.
    assert [record.uid for record in items] == [loose.uid, folder.uid]
    assert child.uid not in {record.uid for record in items}
    # Newest deletion first, and `deleted_at` is populated.
    assert all(record.deleted_at is not None for record in items)
    assert items[0].deleted_at >= items[1].deleted_at


@pytest.mark.asyncio
async def test_trash_is_owner_only(harness: Harness) -> None:
    mine = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="mine.txt",
        content=b"m",
        owner_id=OWNER_ID,
    )
    theirs = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="theirs.txt",
        content=b"t",
        owner_id=OTHER_USER_ID,
    )
    await harness.service.soft_delete(mine.uid, actor_user_id=OWNER_ID)
    await harness.service.soft_delete(theirs.uid, actor_user_id=OTHER_USER_ID)

    assert [r.uid for r in await _trash(harness)] == [mine.uid]
    assert [r.uid for r in await _trash(harness, actor=OTHER_USER_ID)] == [
        theirs.uid,
    ]


@pytest.mark.asyncio
async def test_trash_ignores_parent_id(harness: Harness) -> None:
    folder = await harness.service.create_folder(
        name="docs", parent_id=None, owner_id=OWNER_ID,
    )
    await harness.service.soft_delete(folder.uid, actor_user_id=OWNER_ID)

    # A parent filter makes no sense for the flat trash view; passing one
    # must not hide anything.
    with_parent = await _trash(harness, parent_id="whatever")
    without = await _trash(harness)
    assert [r.uid for r in with_parent] == [r.uid for r in without]


@pytest.mark.asyncio
async def test_child_deleted_alone_is_a_trash_root_until_its_parent_is(
    harness: Harness,
) -> None:
    folder = await harness.service.create_folder(
        name="docs", parent_id=None, owner_id=OWNER_ID,
    )
    child = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=folder.uid,
        name="child.txt",
        content=b"x",
        owner_id=OWNER_ID,
    )

    await harness.service.soft_delete(child.uid, actor_user_id=OWNER_ID)
    # The folder is alive, so the deleted child is its own trash root.
    assert [r.uid for r in await _trash(harness)] == [child.uid]

    await harness.service.soft_delete(folder.uid, actor_user_id=OWNER_ID)
    # Now the folder is the root; the child collapses behind it.
    assert [r.uid for r in await _trash(harness)] == [folder.uid]


# ----------------------------------------------------------------------
# Restore / permanent delete round trips
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restore_removes_the_item_from_trash(harness: Harness) -> None:
    created = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="back.txt",
        content=b"b",
        owner_id=OWNER_ID,
    )
    await harness.service.soft_delete(created.uid, actor_user_id=OWNER_ID)
    assert [r.uid for r in await _trash(harness)] == [created.uid]

    await harness.service.restore(created.uid, actor_user_id=OWNER_ID)

    assert await _trash(harness) == []
    restored = await harness.service.get(created.uid, actor_user_id=OWNER_ID)
    assert restored.deleted_at is None


@pytest.mark.asyncio
async def test_permanent_delete_removes_the_item_from_trash(
    harness: Harness,
) -> None:
    created = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="gone.txt",
        content=b"g",
        owner_id=OWNER_ID,
    )
    await harness.service.soft_delete(created.uid, actor_user_id=OWNER_ID)

    await harness.service.hard_delete(created.uid, actor_user_id=OWNER_ID)

    assert await _trash(harness) == []
    with pytest.raises(MediaFileNotFoundError):
        await harness.service.get(created.uid, actor_user_id=OWNER_ID)


# ----------------------------------------------------------------------
# Purge: >30-day trash roots go, newer trash stays, physical layer survives
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_removes_expired_roots_and_leaves_newer_trash(
    harness: Harness,
) -> None:
    old_folder = await harness.service.create_folder(
        name="old-docs", parent_id=None, owner_id=OWNER_ID,
    )
    old_child = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=old_folder.uid,
        name="old-child.txt",
        content=b"c",
        owner_id=OWNER_ID,
    )
    old_file = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="old.txt",
        content=b"o",
        owner_id=OWNER_ID,
    )
    fresh = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="fresh.txt",
        content=b"f",
        owner_id=OWNER_ID,
    )
    for uid in (old_folder.uid, old_file.uid, fresh.uid):
        await harness.service.soft_delete(uid, actor_user_id=OWNER_ID)
    for uid in (old_folder.uid, old_child.uid, old_file.uid):
        await _backdate(harness, uid, days=40)
    harness.plugins.calls.clear()

    purged = await harness.service.purge_expired_trash()

    assert purged == 2  # the folder tree counts once, plus the loose file
    assert await harness.files.get(old_folder.uid) is None
    assert await harness.files.get(old_child.uid) is None
    assert await harness.files.get(old_file.uid) is None
    # Newer trash is untouched and still listed.
    assert [r.uid for r in await _trash(harness)] == [fresh.uid]
    # v1 rule: StorageObjects and provider bytes always survive a purge.
    assert await harness.objects.get(old_file.storage_object_uid) is not None
    assert await harness.objects.get(old_child.storage_object_uid) is not None
    assert harness.plugins.plugin_calls("delete") == []


@pytest.mark.asyncio
async def test_purge_spares_expired_children_of_an_unexpired_root(
    harness: Harness,
) -> None:
    folder = await harness.service.create_folder(
        name="docs", parent_id=None, owner_id=OWNER_ID,
    )
    child = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=folder.uid,
        name="child.txt",
        content=b"x",
        owner_id=OWNER_ID,
    )
    await harness.service.soft_delete(folder.uid, actor_user_id=OWNER_ID)
    # Only the child is aged: it is not a trash root, so it waits for its
    # folder to expire rather than being ripped out from under it.
    await _backdate(harness, child.uid, days=40)

    assert await harness.service.purge_expired_trash() == 0
    assert await harness.files.get(child.uid) is not None


@pytest.mark.asyncio
async def test_purge_boundary_is_strictly_older_than_the_cutoff(
    harness: Harness,
) -> None:
    at_edge = await harness.service.create_folder(
        name="edge", parent_id=None, owner_id=OWNER_ID,
    )
    beyond = await harness.service.create_folder(
        name="beyond", parent_id=None, owner_id=OWNER_ID,
    )
    for uid in (at_edge.uid, beyond.uid):
        await harness.service.soft_delete(uid, actor_user_id=OWNER_ID)
    await _backdate(harness, at_edge.uid, days=29)
    await _backdate(harness, beyond.uid, days=31)

    assert await harness.service.purge_expired_trash(older_than_days=30) == 1
    assert await harness.files.get(at_edge.uid) is not None
    assert await harness.files.get(beyond.uid) is None


# ----------------------------------------------------------------------
# API shape + scheduler entry point
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_media_file_out_exposes_deleted_at(harness: Harness) -> None:
    created = await harness.service.create_folder(
        name="docs", parent_id=None, owner_id=OWNER_ID,
    )
    assert MediaFileOut.from_record(created).deleted_at is None

    await harness.service.soft_delete(created.uid, actor_user_id=OWNER_ID)
    deleted = (await _trash(harness))[0]
    assert MediaFileOut.from_record(deleted).deleted_at is not None


@pytest.mark.asyncio
async def test_worker_purges_through_a_bare_session_factory(
    tmp_path: Path,
) -> None:
    """The APScheduler job body needs nothing but the session factory --
    no plugin processes, no app state."""
    from fastapi_mongo_base.sql.models import BaseEntity

    from apps.media_files import worker
    from apps.media_files.repository import MediaFileRepository
    from server.database import create_engine, create_session_factory

    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'worker.sqlite3'}")
    async with engine.begin() as connection:
        await connection.run_sync(BaseEntity.metadata.create_all)
    session_factory = create_session_factory(engine)
    files = MediaFileRepository(session_factory)
    try:
        record = await files.create({
            "owner_id": OWNER_ID,
            "type": "file",
            "name": "ancient.txt",
            "parent_id": None,
            "metadata": {},
            "status": "completed",
            "error": None,
            "public_permission": "none",
            "permissions": [],
            "workspace_id": None,
        })
        await files.soft_delete(record.uid)
        await files.update(
            record.uid, {"deleted_at": datetime.now() - timedelta(days=31)},
        )

        assert await worker.purge_expired_trash(session_factory) == 1
        assert await files.get(record.uid) is None
    finally:
        await engine.dispose()
