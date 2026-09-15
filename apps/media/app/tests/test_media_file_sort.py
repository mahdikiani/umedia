from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import update

from apps.media_files.models import MediaFile
from tests.media_file_helpers import FakeConnection, Harness, build_harness

ACTOR_ID = "sort-owner"
OTHER_USER_ID = "sort-sharer"
CONNECTION_ID = "sort-connection"


@pytest_asyncio.fixture(loop_scope="function")
async def harness(tmp_path: Path) -> AsyncGenerator[Harness]:
    built = await build_harness(tmp_path, FakeConnection(uid=CONNECTION_ID))
    yield built
    await built.engine.dispose()


async def _set_updated_at(harness: Harness, uid: str, value: datetime) -> None:
    async with harness.engine.begin() as connection:
        await connection.execute(
            update(MediaFile).where(MediaFile.uid == uid).values(updated_at=value),
        )


async def _set_deleted_at(harness: Harness, uid: str, value: datetime) -> None:
    async with harness.engine.begin() as connection:
        await connection.execute(
            update(MediaFile).where(MediaFile.uid == uid).values(deleted_at=value),
        )


@pytest.mark.asyncio
async def test_name_sort_is_case_insensitive_with_folders_first(
    harness: Harness,
) -> None:
    # Given folders and files whose names would otherwise interleave.
    await harness.service.create_folder(
        name="zeta",
        parent_id=None,
        owner_id=ACTOR_ID,
    )
    await harness.service.create_folder(
        name="Archive",
        parent_id=None,
        owner_id=ACTOR_ID,
    )
    await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="Beta.pdf",
        content=b"beta",
        owner_id=ACTOR_ID,
    )
    await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="apple.pdf",
        content=b"apple",
        owner_id=ACTOR_ID,
    )

    # When the default name ordering is requested.
    page = await harness.service.list_children(
        None,
        actor_user_id=ACTOR_ID,
        sort="name",
        order="asc",
    )

    # Then folders form the first group and each group is case-insensitive.
    assert [record.name for record in page.items] == [
        "Archive",
        "zeta",
        "apple.pdf",
        "Beta.pdf",
    ]


@pytest.mark.asyncio
async def test_updated_at_desc_keeps_folders_before_newer_files(
    harness: Harness,
) -> None:
    # Given one old folder and two files with distinct update times.
    folder = await harness.service.create_folder(
        name="old-folder",
        parent_id=None,
        owner_id=ACTOR_ID,
    )
    older = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="older.txt",
        content=b"old",
        owner_id=ACTOR_ID,
    )
    newer = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="newer.txt",
        content=b"new",
        owner_id=ACTOR_ID,
    )
    await _set_updated_at(harness, folder.uid, datetime(2025, 1, 1, tzinfo=UTC))
    await _set_updated_at(harness, older.uid, datetime(2025, 2, 1, tzinfo=UTC))
    await _set_updated_at(harness, newer.uid, datetime(2025, 3, 1, tzinfo=UTC))

    # When newest-first ordering is requested.
    page = await harness.service.list_children(
        None,
        actor_user_id=ACTOR_ID,
        sort="updated_at",
        order="desc",
    )

    # Then the folder remains first and the file group is newest-first.
    assert [record.uid for record in page.items] == [
        folder.uid,
        newer.uid,
        older.uid,
    ]


@pytest.mark.asyncio
async def test_type_sort_groups_files_by_content_type_after_folders(
    harness: Harness,
) -> None:
    # Given folders plus files backed by different MIME types.
    folder_z = await harness.service.create_folder(
        name="Zoo",
        parent_id=None,
        owner_id=ACTOR_ID,
    )
    folder_a = await harness.service.create_folder(
        name="Archive",
        parent_id=None,
        owner_id=ACTOR_ID,
    )
    text = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="notes.txt",
        content=b"notes",
        owner_id=ACTOR_ID,
    )
    pdf = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="diagram.pdf",
        content=b"pdf",
        owner_id=ACTOR_ID,
    )
    assert text.storage_object_uid is not None
    assert pdf.storage_object_uid is not None
    await harness.objects.update(
        text.storage_object_uid,
        {"content_type": "text/plain"},
    )
    await harness.objects.update(
        pdf.storage_object_uid,
        {"content_type": "application/pdf"},
    )

    # When type ordering is requested.
    page = await harness.service.list_children(
        None,
        actor_user_id=ACTOR_ID,
        sort="type",
        order="asc",
    )

    # Then folders sort by name before MIME-grouped files.
    assert [record.uid for record in page.items] == [
        folder_a.uid,
        folder_z.uid,
        pdf.uid,
        text.uid,
    ]


@pytest.mark.asyncio
async def test_size_sort_keeps_folders_first(
    harness: Harness,
) -> None:
    folder = await harness.service.create_folder(
        name="docs",
        parent_id=None,
        owner_id=ACTOR_ID,
    )
    small = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="small.txt",
        content=b"ab",
        owner_id=ACTOR_ID,
    )
    large = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="large.txt",
        content=b"abcdefgh",
        owner_id=ACTOR_ID,
    )

    page = await harness.service.list_children(
        None,
        actor_user_id=ACTOR_ID,
        sort="size",
        order="desc",
    )

    assert [record.uid for record in page.items] == [
        folder.uid,
        large.uid,
        small.uid,
    ]


@pytest.mark.asyncio
async def test_filtered_scope_sorts_before_slicing(harness: Harness) -> None:
    # Given three shared files created in an order different from their mtimes.
    shared = []
    for name in ("first.txt", "second.txt", "third.txt"):
        record = await harness.service.upload(
            provider_connection_id=CONNECTION_ID,
            parent_id=None,
            name=name,
            content=name.encode(),
            owner_id=OTHER_USER_ID,
        )
        await harness.service.set_user_permission(
            record.uid,
            actor_user_id=OTHER_USER_ID,
            target_user_id=ACTOR_ID,
            permission=10,
        )
        shared.append(record)
    await _set_updated_at(harness, shared[0].uid, datetime(2025, 1, 1, tzinfo=UTC))
    await _set_updated_at(harness, shared[1].uid, datetime(2025, 3, 1, tzinfo=UTC))
    await _set_updated_at(harness, shared[2].uid, datetime(2025, 2, 1, tzinfo=UTC))

    # When the second item of the newest-first shared scope is requested.
    page = await harness.service.list_children(
        None,
        actor_user_id=ACTOR_ID,
        scope="shared_with_me",
        sort="updated_at",
        order="desc",
        limit=1,
        offset=1,
    )

    # Then filtering and ordering both happened before pagination.
    assert [record.uid for record in page.items] == [shared[2].uid]
    assert page.total == 3


@pytest.mark.asyncio
async def test_trash_ignores_requested_sort(harness: Harness) -> None:
    # Given two deleted roots with explicit deletion times.
    older = await harness.service.create_folder(
        name="alpha",
        parent_id=None,
        owner_id=ACTOR_ID,
    )
    newer = await harness.service.create_folder(
        name="zulu",
        parent_id=None,
        owner_id=ACTOR_ID,
    )
    await harness.files.soft_delete(older.uid)
    await harness.files.soft_delete(newer.uid)
    await _set_deleted_at(harness, older.uid, datetime(2025, 1, 1, tzinfo=UTC))
    await _set_deleted_at(harness, newer.uid, datetime(2025, 2, 1, tzinfo=UTC))

    # When an alphabetical sort is supplied to the trash scope.
    page = await harness.service.list_children(
        None,
        actor_user_id=ACTOR_ID,
        scope="trash",
        sort="name",
        order="asc",
    )

    # Then trash keeps its newest-deletion-first contract.
    assert [record.uid for record in page.items] == [newer.uid, older.uid]
