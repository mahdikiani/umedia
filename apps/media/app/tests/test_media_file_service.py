"""MediaFileService behavior (docs/11-dual-layer-library.md): the user
library layer. Listing is SQLite-only, folders are library-only, uploads
write through the plugin and produce a StorageObject + link, and the
Resource-era ACL carries over unchanged.

Real repositories on a throwaway SQLite; fake plugin gateway and
connection lookup -- see tests/media_file_helpers.py.
"""

from pathlib import Path

import pytest
import pytest_asyncio

from apps.media_files.errors import (
    MediaFileNotFoundError,
    MediaFilePermissionError,
    MediaFileValidationError,
    MediaFileWriteFailedError,
)
from tests.media_file_helpers import FakeConnection, Harness, build_harness

CONNECTION_ID = "connection-1"
OWNER_ID = "user-1"
OTHER_USER_ID = "user-2"


@pytest_asyncio.fixture
async def harness(tmp_path: Path) -> Harness:
    built = await build_harness(tmp_path, FakeConnection(uid=CONNECTION_ID))
    yield built
    await built.engine.dispose()


# ----------------------------------------------------------------------
# Listing is local-only
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_children_never_calls_the_plugin(harness: Harness) -> None:
    await harness.service.create_folder(
        name="docs", parent_id=None, owner_id=OWNER_ID,
    )
    await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="a.txt",
        content=b"hello",
        owner_id=OWNER_ID,
    )
    harness.plugins.calls.clear()

    listed = await harness.service.list_children(None, actor_user_id=OWNER_ID)

    assert sorted(record.name for record in listed.items) == ["a.txt", "docs"]
    assert harness.plugins.calls == []  # SQLite only, no provider I/O


@pytest.mark.asyncio
async def test_search_is_global_visible_and_boosts_the_current_folder(
    harness: Harness,
) -> None:
    current = await harness.service.create_folder(
        name="Current", parent_id=None, owner_id=OWNER_ID,
    )
    nested = await harness.service.create_folder(
        name="Nested", parent_id=current.uid, owner_id=OWNER_ID,
    )
    direct = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=current.uid,
        name="budget-2026.txt",
        content=b"direct",
        owner_id=OWNER_ID,
    )
    descendant = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=nested.uid,
        name="budget-notes.txt",
        content=b"descendant",
        owner_id=OWNER_ID,
    )
    outside = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="budget.txt",
        content=b"outside",
        owner_id=OWNER_ID,
    )
    hidden = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="budget-secret.txt",
        content=b"hidden",
        owner_id=OTHER_USER_ID,
    )
    shared = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="budget-shared.txt",
        content=b"shared",
        owner_id=OTHER_USER_ID,
    )
    await harness.service.set_user_permission(
        shared.uid,
        actor_user_id=OTHER_USER_ID,
        target_user_id=OWNER_ID,
        permission=10,
    )
    harness.plugins.calls.clear()

    results = await harness.service.search(
        "budget",
        actor_user_id=OWNER_ID,
        under_parent_id=current.uid,
    )

    result_ids = [record.uid for record in results.items]
    assert result_ids[0] == direct.uid
    assert result_ids.index(descendant.uid) < result_ids.index(outside.uid)
    assert outside.uid in result_ids
    assert shared.uid in result_ids
    assert hidden.uid not in result_ids
    assert harness.plugins.calls == []


# ----------------------------------------------------------------------
# Folders are library-only
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_folder_is_library_only(harness: Harness) -> None:
    folder = await harness.service.create_folder(
        name="docs", parent_id=None, owner_id=OWNER_ID,
    )

    assert folder.type == "folder"
    assert folder.status == "completed"
    assert folder.storage_object_uid is None
    assert folder.provider_connection_id is None
    assert folder.content_type == "inode/directory"
    assert harness.plugins.calls == []  # no plugin write for a folder


@pytest.mark.asyncio
async def test_create_folder_rejects_a_file_parent(harness: Harness) -> None:
    file_ = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="a.txt",
        content=b"x",
        owner_id=OWNER_ID,
    )

    with pytest.raises(MediaFileValidationError):
        await harness.service.create_folder(
            name="child", parent_id=file_.uid, owner_id=OWNER_ID,
        )


# ----------------------------------------------------------------------
# Upload: plugin write -> StorageObject -> MediaFile + primary link
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_creates_storage_object_and_links_it_primary(
    harness: Harness,
) -> None:
    created = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="hello.txt",
        content=b"hello",
        content_type="text/plain",
        owner_id=OWNER_ID,
    )

    assert created.status == "completed"
    assert created.type == "file"
    assert created.size == 5
    assert created.provider_connection_id == CONNECTION_ID
    assert created.storage_object_uid is not None

    obj = await harness.objects.get(created.storage_object_uid)
    assert obj is not None
    assert obj.provider_connection_id == CONNECTION_ID
    assert obj.content_reference == created.content_reference
    assert obj.size == 5
    assert obj.content_hash is not None
    assert harness.plugins.store[obj.content_reference] == b"hello"


@pytest.mark.asyncio
async def test_upload_failure_marks_the_media_file_failed(
    harness: Harness,
) -> None:
    harness.plugins.fail_create = True

    with pytest.raises(MediaFileWriteFailedError):
        await harness.service.upload(
            provider_connection_id=CONNECTION_ID,
            parent_id=None,
            name="doomed.txt",
            content=b"x",
            owner_id=OWNER_ID,
        )

    rows = await harness.files.list(parent_id=None)
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert rows[0].error is not None
    assert rows[0].storage_object_uid is None


@pytest.mark.asyncio
async def test_replace_content_overwrites_bytes_and_snapshots_history(
    harness: Harness,
) -> None:
    created = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="versioned.txt",
        content=b"old",
        content_type="text/plain",
        owner_id=OWNER_ID,
    )

    replaced = await harness.service.replace_content(
        created.uid,
        content=b"new content",
        content_type="text/markdown",
        actor_user_id=OWNER_ID,
    )

    _, stream = await harness.service.read_content(
        created.uid,
        actor_user_id=OWNER_ID,
    )
    assert b"".join([chunk async for chunk in stream]) == b"new content"
    assert replaced.name == "versioned.txt"
    assert replaced.parent_id is None
    assert replaced.size == len(b"new content")
    assert len(replaced.history) == 1
    assert replaced.history[0].storage_object_uid == created.storage_object_uid
    assert replaced.history[0].size == len(b"old")


@pytest.mark.asyncio
async def test_upload_into_a_folder(harness: Harness) -> None:
    folder = await harness.service.create_folder(
        name="docs", parent_id=None, owner_id=OWNER_ID,
    )

    child = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=folder.uid,
        name="child.txt",
        content=b"nested",
        owner_id=OWNER_ID,
    )

    assert child.parent_id == folder.uid
    listed = await harness.service.list_children(
        folder.uid, actor_user_id=OWNER_ID,
    )
    assert [record.uid for record in listed.items] == [child.uid]


@pytest.mark.asyncio
async def test_upload_rejects_an_unknown_parent(harness: Harness) -> None:
    with pytest.raises(MediaFileValidationError):
        await harness.service.upload(
            provider_connection_id=CONNECTION_ID,
            parent_id="nope",
            name="a.txt",
            content=b"x",
            owner_id=OWNER_ID,
        )


# ----------------------------------------------------------------------
# Content read: MediaFile -> primary StorageObject -> plugin stream
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_content_streams_through_the_primary_object(
    harness: Harness,
) -> None:
    created = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="a.txt",
        content=b"streamed content",
        owner_id=OWNER_ID,
    )
    before = (await harness.files.get(created.uid)).access_at

    record, stream = await harness.service.read_content(
        created.uid, actor_user_id=OWNER_ID,
    )
    body = b"".join([chunk async for chunk in stream])

    assert body == b"streamed content"
    assert record.uid == created.uid
    assert (await harness.files.get(created.uid)).access_at >= before


@pytest.mark.asyncio
async def test_volume_stats_sums_owned_live_files(harness: Harness) -> None:
    await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="a.txt",
        content=b"12345",
        owner_id=OWNER_ID,
    )
    await harness.service.create_folder(
        name="docs", parent_id=None, owner_id=OWNER_ID,
    )

    stats = await harness.service.volume_stats(actor_user_id=OWNER_ID)

    assert stats["used_bytes"] == 5
    assert stats["file_count"] == 1
    assert stats["folder_count"] == 1


@pytest.mark.asyncio
async def test_read_content_of_a_folder_is_not_found(harness: Harness) -> None:
    folder = await harness.service.create_folder(
        name="docs", parent_id=None, owner_id=OWNER_ID,
    )

    with pytest.raises(MediaFileNotFoundError):
        await harness.service.read_content(folder.uid, actor_user_id=OWNER_ID)


# ----------------------------------------------------------------------
# ACL (ported from the Resource ACL; admins get no implicit override)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_other_users_cannot_see_an_unshared_file(
    harness: Harness,
) -> None:
    created = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="private.txt",
        content=b"secret",
        owner_id=OWNER_ID,
    )

    with pytest.raises(MediaFileNotFoundError):  # 404, not 403: uid stays private
        await harness.service.get(created.uid, actor_user_id=OTHER_USER_ID)

    listed = await harness.service.list_children(
        None, actor_user_id=OTHER_USER_ID,
    )
    assert listed.items == []


@pytest.mark.asyncio
async def test_read_grant_gives_visibility_but_not_write(
    harness: Harness,
) -> None:
    created = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="shared.txt",
        content=b"x",
        owner_id=OWNER_ID,
    )

    await harness.service.set_user_permission(
        created.uid,
        actor_user_id=OWNER_ID,
        target_user_id=OTHER_USER_ID,
        permission=10,  # READ
    )

    fetched = await harness.service.get(
        created.uid, actor_user_id=OTHER_USER_ID,
    )
    assert fetched.name == "shared.txt"

    with pytest.raises(MediaFilePermissionError):
        await harness.service.update(
            created.uid, actor_user_id=OTHER_USER_ID, name="nope.txt",
        )


@pytest.mark.asyncio
async def test_public_link_visibility(harness: Harness) -> None:
    created = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="linked.txt",
        content=b"x",
        owner_id=OWNER_ID,
    )

    with pytest.raises(MediaFileNotFoundError):
        await harness.service.get_via_public_link(created.uid, actor_user_id=None)

    await harness.service.set_public_permission(
        created.uid, "read", actor_user_id=OWNER_ID,
    )

    fetched = await harness.service.get_via_public_link(
        created.uid, actor_user_id=None,
    )
    assert fetched.uid == created.uid


# ----------------------------------------------------------------------
# Rename / move (library-only here; mirror covered in test_media_file_mirror)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rename_and_move_update_the_library_tree(
    harness: Harness,
) -> None:
    folder = await harness.service.create_folder(
        name="docs", parent_id=None, owner_id=OWNER_ID,
    )
    created = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="old.txt",
        content=b"x",
        owner_id=OWNER_ID,
    )

    renamed = await harness.service.update(
        created.uid, actor_user_id=OWNER_ID, name="new.txt",
    )
    assert renamed.name == "new.txt"

    moved = await harness.service.update(
        created.uid, actor_user_id=OWNER_ID, parent_id=folder.uid,
    )
    assert moved.parent_id == folder.uid

    back_to_root = await harness.service.update(
        created.uid, actor_user_id=OWNER_ID, parent_id=None,
    )
    assert back_to_root.parent_id is None


@pytest.mark.asyncio
async def test_move_rejects_a_non_folder_target(harness: Harness) -> None:
    a = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="a.txt",
        content=b"a",
        owner_id=OWNER_ID,
    )
    b = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="b.txt",
        content=b"b",
        owner_id=OWNER_ID,
    )

    with pytest.raises(MediaFileValidationError):
        await harness.service.update(
            a.uid, actor_user_id=OWNER_ID, parent_id=b.uid,
        )


# ----------------------------------------------------------------------
# Delete lifecycle: soft-delete MediaFile only; StorageObject stays
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_soft_delete_cascades_and_leaves_the_storage_object(
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

    with pytest.raises(MediaFileNotFoundError):
        await harness.service.get(child.uid, actor_user_id=OWNER_ID)
    # The physical layer is untouched: object row alive, remote bytes kept.
    obj = await harness.objects.get(child.storage_object_uid)
    assert obj.is_deleted is False
    assert harness.plugins.plugin_calls("delete") == []

    await harness.service.restore(folder.uid, actor_user_id=OWNER_ID)
    restored = await harness.service.get(child.uid, actor_user_id=OWNER_ID)
    assert restored.uid == child.uid


@pytest.mark.asyncio
async def test_hard_delete_removes_the_row_but_never_the_remote(
    harness: Harness,
) -> None:
    created = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="doomed.txt",
        content=b"x",
        owner_id=OWNER_ID,
    )
    await harness.service.soft_delete(created.uid, actor_user_id=OWNER_ID)

    await harness.service.hard_delete(created.uid, actor_user_id=OWNER_ID)

    assert await harness.files.get(created.uid) is None
    # v1 keeps the StorageObject and the provider bytes (doc 11).
    assert await harness.objects.get(created.storage_object_uid) is not None
    assert harness.plugins.plugin_calls("delete") == []
