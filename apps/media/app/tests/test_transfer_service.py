import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from apps.media_files.errors import MediaFileValidationError
from apps.media_files.schemas import MediaFileRecord
from apps.media_files.transfer_repository import TransferRecord, TransferRepository
from apps.media_files.transfer_service import TransferCreate, TransferService
from tests.media_file_helpers import FakeConnection, Harness, build_harness

CONNECTION_ID = "connection-1"
CONNECTION_2 = "connection-2"
OWNER_ID = "user-1"
OTHER_USER_ID = "user-2"


@pytest_asyncio.fixture(loop_scope="function")
async def harness(tmp_path: Path) -> Harness:
    built = await build_harness(tmp_path, FakeConnection(uid=CONNECTION_ID))
    yield built
    await built.engine.dispose()


def _transfers(harness: Harness) -> TransferService:
    return TransferService(
        TransferRepository(harness.session_factory),
        harness.service,
        tasks=set(),
    )


async def _run(
    transfers: TransferService,
    *,
    actor_user_id: str,
    operation: str,
    source_ids: list[str],
    dest_parent_id: str | None = None,
    conflict: str = "rename",
    is_admin: bool = False,
) -> TransferRecord:
    record = await transfers.create_and_enqueue(
        actor_user_id,
        TransferCreate(
            operation=operation,
            source_ids=source_ids,
            dest_parent_id=dest_parent_id,
            conflict=conflict,
        ),
        is_admin=is_admin,
        schedule=False,
    )
    await transfers.run_job(record.uid)
    return await transfers.get(record.uid, actor_user_id=actor_user_id)


@pytest.mark.asyncio
async def test_cross_storage_transfers_into_local_preserve_admin_context(
    tmp_path: Path,
) -> None:
    harness = await build_harness(
        tmp_path,
        FakeConnection(uid=CONNECTION_ID, owner_id=OWNER_ID),
        FakeConnection(
            uid=CONNECTION_2,
            name="Local",
            provider_type="local",
            owner_id=OWNER_ID,
        ),
    )
    try:
        await harness.settings.update({
            "placement_policy": "default",
            "default_connection_id": CONNECTION_ID,
        })
        sources = [
            await harness.service.upload(
                provider_connection_id=CONNECTION_ID,
                parent_id=None,
                name=name,
                content=name.encode(),
                owner_id=OWNER_ID,
            )
            for name in ("copy.txt", "move.txt", "denied.txt")
        ]
        await harness.settings.update({"default_connection_id": CONNECTION_2})
        dest = await harness.service.create_folder(
            name="Local target",
            parent_id=None,
            owner_id=OWNER_ID,
            is_admin=True,
        )
        transfers = _transfers(harness)

        copy_job = await _run(
            transfers,
            actor_user_id=OWNER_ID,
            operation="copy",
            source_ids=[sources[0].uid],
            dest_parent_id=dest.uid,
            is_admin=True,
        )
        move_job = await _run(
            transfers,
            actor_user_id=OWNER_ID,
            operation="move",
            source_ids=[sources[1].uid],
            dest_parent_id=dest.uid,
            is_admin=True,
        )
        denied_job = await _run(
            transfers,
            actor_user_id=OWNER_ID,
            operation="copy",
            source_ids=[sources[2].uid],
            dest_parent_id=dest.uid,
        )

        assert copy_job.is_admin is True
        assert move_job.is_admin is True
        assert copy_job.status == "completed"
        assert move_job.status == "completed"
        assert denied_job.is_admin is False
        assert denied_job.status == "failed"
        assert denied_job.error == "This folder's storage is disabled or missing"
    finally:
        await harness.engine.dispose()


@pytest.mark.asyncio
async def test_single_move_completes(harness: Harness) -> None:
    folder = await harness.service.create_folder(
        name="docs",
        parent_id=None,
        owner_id=OWNER_ID,
    )
    file = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="a.txt",
        content=b"hello",
        owner_id=OWNER_ID,
    )
    transfers = _transfers(harness)

    job = await _run(
        transfers,
        actor_user_id=OWNER_ID,
        operation="move",
        source_ids=[file.uid],
        dest_parent_id=folder.uid,
    )

    assert job.status == "completed"
    assert job.done_items == job.total_items
    assert job.progress_pct == 100
    assert job.failed_items == 0
    moved = await harness.service.get(file.uid, actor_user_id=OWNER_ID)
    assert moved.parent_id == folder.uid


@pytest.mark.asyncio
async def test_move_to_current_parent_is_rejected_before_enqueue(
    harness: Harness,
) -> None:
    file = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="already-at-root.txt",
        content=b"x",
        owner_id=OWNER_ID,
    )
    transfers = _transfers(harness)

    with pytest.raises(MediaFileValidationError):
        await transfers.create_and_enqueue(
            OWNER_ID,
            TransferCreate(
                operation="move",
                source_ids=[file.uid],
                dest_parent_id=None,
            ),
            schedule=False,
        )

    assert await transfers.list_for_user(actor_user_id=OWNER_ID) == []


@pytest.mark.asyncio
async def test_move_folder_into_itself_is_rejected_before_enqueue(
    harness: Harness,
) -> None:
    folder = await harness.service.create_folder(
        name="folder",
        parent_id=None,
        owner_id=OWNER_ID,
    )
    transfers = _transfers(harness)

    with pytest.raises(MediaFileValidationError):
        await transfers.create_and_enqueue(
            OWNER_ID,
            TransferCreate(
                operation="move",
                source_ids=[folder.uid],
                dest_parent_id=folder.uid,
            ),
            schedule=False,
        )

    assert await transfers.list_for_user(actor_user_id=OWNER_ID) == []


@pytest.mark.asyncio
async def test_bulk_move_progress_reaches_100(harness: Harness) -> None:
    dest = await harness.service.create_folder(
        name="dest",
        parent_id=None,
        owner_id=OWNER_ID,
    )
    sources = [
        await harness.service.upload(
            provider_connection_id=CONNECTION_ID,
            parent_id=None,
            name=name,
            content=name.encode(),
            owner_id=OWNER_ID,
        )
        for name in ("a.txt", "b.txt", "c.txt")
    ]
    transfers = _transfers(harness)

    job = await _run(
        transfers,
        actor_user_id=OWNER_ID,
        operation="move",
        source_ids=[item.uid for item in sources],
        dest_parent_id=dest.uid,
    )

    assert job.status == "completed"
    assert job.total_items == 3
    assert job.done_items == 3
    assert job.progress_pct == 100
    for item in sources:
        moved = await harness.service.get(item.uid, actor_user_id=OWNER_ID)
        assert moved.parent_id == dest.uid


@pytest.mark.asyncio
async def test_copy_same_storage_shares_storage_object(harness: Harness) -> None:
    dest = await harness.service.create_folder(
        name="dest",
        parent_id=None,
        owner_id=OWNER_ID,
    )
    original = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="shared.txt",
        content=b"shared-bytes",
        owner_id=OWNER_ID,
    )
    transfers = _transfers(harness)

    job = await _run(
        transfers,
        actor_user_id=OWNER_ID,
        operation="copy",
        source_ids=[original.uid],
        dest_parent_id=dest.uid,
    )

    assert job.status == "completed"
    children = await harness.files.list(parent_id=dest.uid)
    assert len(children) == 1
    copy = children[0]
    assert copy.uid != original.uid
    assert copy.name == "shared.txt"
    assert copy.storage_object_uid == original.storage_object_uid
    assert copy.storage_object_uid is not None
    # Original still live at root.
    still = await harness.service.get(original.uid, actor_user_id=OWNER_ID)
    assert still.parent_id is None
    assert still.is_deleted is False


@pytest.mark.asyncio
async def test_copy_folder_recursively(harness: Harness) -> None:
    dest = await harness.service.create_folder(
        name="dest",
        parent_id=None,
        owner_id=OWNER_ID,
    )
    source_folder = await harness.service.create_folder(
        name="album",
        parent_id=None,
        owner_id=OWNER_ID,
    )
    await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=source_folder.uid,
        name="photo.txt",
        content=b"img",
        owner_id=OWNER_ID,
    )
    nested = await harness.service.create_folder(
        name="raw",
        parent_id=source_folder.uid,
        owner_id=OWNER_ID,
    )
    await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=nested.uid,
        name="neg.txt",
        content=b"neg",
        owner_id=OWNER_ID,
    )
    transfers = _transfers(harness)

    job = await _run(
        transfers,
        actor_user_id=OWNER_ID,
        operation="copy",
        source_ids=[source_folder.uid],
        dest_parent_id=dest.uid,
    )

    assert job.status == "completed"
    assert job.total_items == 4  # album + photo + raw + neg
    assert job.progress_pct == 100
    copied_roots = await harness.files.list(parent_id=dest.uid)
    assert len(copied_roots) == 1
    assert copied_roots[0].name == "album"
    assert copied_roots[0].uid != source_folder.uid
    kids = {c.name: c for c in await harness.files.list(parent_id=copied_roots[0].uid)}
    assert set(kids) == {"photo.txt", "raw"}
    raw_kids = await harness.files.list(parent_id=kids["raw"].uid)
    assert [c.name for c in raw_kids] == ["neg.txt"]


@pytest.mark.asyncio
async def test_cross_storage_move_copies_then_soft_deletes_source(
    tmp_path: Path,
) -> None:
    harness = await build_harness(
        tmp_path,
        FakeConnection(uid=CONNECTION_ID, owner_id=OWNER_ID),
        FakeConnection(uid=CONNECTION_2, name="Other", owner_id=OWNER_ID),
    )
    try:
        await harness.settings.update({
            "placement_policy": "default",
            "default_connection_id": CONNECTION_ID,
        })
        source_folder = await harness.service.create_folder(
            name="FromA",
            parent_id=None,
            owner_id=OWNER_ID,
        )
        original = await harness.service.upload(
            parent_id=source_folder.uid,
            name="clip.txt",
            content=b"video-bytes",
            owner_id=OWNER_ID,
        )
        await harness.settings.update({"default_connection_id": CONNECTION_2})
        dest = await harness.service.create_folder(
            name="ToB",
            parent_id=None,
            owner_id=OWNER_ID,
        )
        assert dest.provider_connection_id == CONNECTION_2
        transfers = _transfers(harness)

        job = await _run(
            transfers,
            actor_user_id=OWNER_ID,
            operation="move",
            source_ids=[original.uid],
            dest_parent_id=dest.uid,
        )

        assert job.status == "completed"
        source = await harness.files.get(original.uid)
        assert source is not None
        assert source.is_deleted is True
        kids = await harness.files.list(parent_id=dest.uid)
        assert len(kids) == 1
        copy = kids[0]
        assert copy.is_deleted is False
        assert copy.name == "clip.txt"
        assert copy.storage_object_uid != original.storage_object_uid
        assert copy.provider_connection_id == CONNECTION_2
        assert b"video-bytes" in harness.plugins.store.values()
    finally:
        await harness.engine.dispose()


@pytest.mark.asyncio
async def test_create_and_enqueue_schedules_background_task(
    harness: Harness,
) -> None:
    folder = await harness.service.create_folder(
        name="docs",
        parent_id=None,
        owner_id=OWNER_ID,
    )
    file = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="bg.txt",
        content=b"x",
        owner_id=OWNER_ID,
    )
    tasks: set[asyncio.Task] = set()
    transfers = TransferService(
        TransferRepository(harness.session_factory),
        harness.service,
        tasks=tasks,
    )
    job = await transfers.create_and_enqueue(
        OWNER_ID,
        TransferCreate(
            operation="move",
            source_ids=[file.uid],
            dest_parent_id=folder.uid,
        ),
    )
    assert job.status == "queued"
    assert tasks
    await asyncio.wait(tasks, timeout=5)
    done = await transfers.get(job.uid, actor_user_id=OWNER_ID)
    assert done.status == "completed"


@pytest.mark.asyncio
async def test_get_transfer_not_found_for_other_user(harness: Harness) -> None:
    file = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="secret.txt",
        content=b"x",
        owner_id=OWNER_ID,
    )
    transfers = _transfers(harness)
    job = await transfers.create_and_enqueue(
        OWNER_ID,
        TransferCreate(operation="copy", source_ids=[file.uid], dest_parent_id=None),
        schedule=False,
    )
    with pytest.raises(Exception) as exc:
        await transfers.get(job.uid, actor_user_id=OTHER_USER_ID)
    assert getattr(exc.value, "status_code", None) == 404


@pytest.mark.asyncio
async def test_cancel_queued_transfer_finishes_without_moving_source(
    harness: Harness,
) -> None:
    destination = await harness.service.create_folder(
        name="cancel destination",
        parent_id=None,
        owner_id=OWNER_ID,
    )
    source = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="queued.txt",
        content=b"queued",
        owner_id=OWNER_ID,
    )
    transfers = _transfers(harness)
    job = await transfers.create_and_enqueue(
        OWNER_ID,
        TransferCreate(
            operation="move",
            source_ids=[source.uid],
            dest_parent_id=destination.uid,
        ),
        schedule=False,
    )

    cancelled = await transfers.cancel(job.uid, actor_user_id=OWNER_ID)
    result = await transfers.run_job(job.uid)

    assert cancelled.status == "cancelled"
    assert result.status == "cancelled"
    assert result.done_items == 0
    stored_source = await harness.service.get(source.uid, actor_user_id=OWNER_ID)
    assert stored_source.parent_id is None


@pytest.mark.asyncio
async def test_cancel_running_transfer_rolls_back_completed_items(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = await harness.service.create_folder(
        name="running cancel destination",
        parent_id=None,
        owner_id=OWNER_ID,
    )
    sources = [
        await harness.service.upload(
            provider_connection_id=CONNECTION_ID,
            parent_id=None,
            name=name,
            content=b"x",
            owner_id=OWNER_ID,
        )
        for name in ("first.txt", "second.txt")
    ]
    transfers = _transfers(harness)
    job = await transfers.create_and_enqueue(
        OWNER_ID,
        TransferCreate(
            operation="move",
            source_ids=[source.uid for source in sources],
            dest_parent_id=destination.uid,
        ),
        schedule=False,
    )
    original_process_move = transfers._process_move
    item_started = asyncio.Event()
    continue_item = asyncio.Event()

    async def hold_first_item(
        job_uid: str,
        source: MediaFileRecord,
        transfer_record: TransferRecord,
        *,
        actor_user_id: str,
        is_admin: bool = False,
    ) -> None:
        if not item_started.is_set():
            item_started.set()
            await continue_item.wait()
        await original_process_move(
            job_uid,
            source,
            transfer_record,
            actor_user_id=actor_user_id,
            is_admin=is_admin,
        )

    monkeypatch.setattr(transfers, "_process_move", hold_first_item)
    task = asyncio.create_task(transfers.run_job(job.uid))
    await asyncio.wait_for(item_started.wait(), timeout=2)

    requested = await transfers.cancel(job.uid, actor_user_id=OWNER_ID)
    continue_item.set()
    finished = await asyncio.wait_for(task, timeout=2)

    assert requested.status == "cancelling"
    assert finished.status == "cancelled"
    assert finished.done_items == 1
    first = await harness.service.get(sources[0].uid, actor_user_id=OWNER_ID)
    second = await harness.service.get(sources[1].uid, actor_user_id=OWNER_ID)
    assert first.parent_id is None
    assert second.parent_id is None


@pytest.mark.asyncio
async def test_cancel_cross_storage_move_keeps_source_after_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = await build_harness(
        tmp_path,
        FakeConnection(uid=CONNECTION_ID, owner_id=OWNER_ID),
        FakeConnection(
            uid=CONNECTION_2,
            name="Local",
            provider_type="local",
            owner_id=OWNER_ID,
        ),
    )
    try:
        await harness.settings.update({
            "placement_policy": "default",
            "default_connection_id": CONNECTION_2,
        })
        source = await harness.service.upload(
            provider_connection_id=CONNECTION_ID,
            parent_id=None,
            name="cross-storage.txt",
            content=b"source-bytes",
            owner_id=OWNER_ID,
        )
        destination = await harness.service.create_folder(
            name="cancel cross-storage",
            parent_id=None,
            owner_id=OWNER_ID,
            is_admin=True,
        )
        transfers = _transfers(harness)
        job = await transfers.create_and_enqueue(
            OWNER_ID,
            TransferCreate(
                operation="move",
                source_ids=[source.uid],
                dest_parent_id=destination.uid,
            ),
            is_admin=True,
            schedule=False,
        )
        original_copy_tree = transfers._copy_tree
        copy_finished = asyncio.Event()
        release_copy = asyncio.Event()

        async def pause_after_copy(
            job_uid: str,
            file: MediaFileRecord,
            *,
            dest_parent_id: str | None,
            conflict: str,
            actor_user_id: str,
            byte_copy: bool,
            is_admin: bool = False,
        ) -> MediaFileRecord | None:
            result = await original_copy_tree(
                job_uid,
                file,
                dest_parent_id=dest_parent_id,
                conflict=conflict,
                actor_user_id=actor_user_id,
                byte_copy=byte_copy,
                is_admin=is_admin,
            )
            copy_finished.set()
            await release_copy.wait()
            return result

        monkeypatch.setattr(transfers, "_copy_tree", pause_after_copy)
        task = asyncio.create_task(transfers.run_job(job.uid))
        await asyncio.wait_for(copy_finished.wait(), timeout=2)

        requested = await transfers.cancel(job.uid, actor_user_id=OWNER_ID)
        release_copy.set()
        finished = await asyncio.wait_for(task, timeout=2)

        assert requested.status == "cancelling"
        assert finished.status == "cancelled"
        source_after = await harness.service.get(source.uid, actor_user_id=OWNER_ID)
        assert source_after.is_deleted is False
        copied = await harness.files.list(parent_id=destination.uid)
        assert copied == []
    finally:
        await harness.engine.dispose()


@pytest.mark.asyncio
async def test_cancel_during_failed_upload_removes_placeholder_and_keeps_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = await build_harness(
        tmp_path,
        FakeConnection(uid=CONNECTION_ID, owner_id=OWNER_ID),
        FakeConnection(uid=CONNECTION_2, name="Second", owner_id=OWNER_ID),
    )
    try:
        await harness.settings.update({
            "placement_policy": "default",
            "default_connection_id": CONNECTION_2,
        })
        source = await harness.service.upload(
            provider_connection_id=CONNECTION_ID,
            parent_id=None,
            name="cancelled-upload.txt",
            content=b"source-bytes",
            owner_id=OWNER_ID,
        )
        destination = await harness.service.create_folder(
            name="cancel failed upload",
            parent_id=None,
            owner_id=OWNER_ID,
        )
        transfers = _transfers(harness)
        job = await transfers.create_and_enqueue(
            OWNER_ID,
            TransferCreate(
                operation="copy",
                source_ids=[source.uid],
                dest_parent_id=destination.uid,
            ),
            schedule=False,
        )
        upload_started = asyncio.Event()
        release_upload = asyncio.Event()

        async def fail_upload(
            connection_id: str,
            metadata: object,
            content: bytes,
        ) -> object:
            upload_started.set()
            await release_upload.wait()
            raise RuntimeError("simulated unauthenticated upload")

        monkeypatch.setattr(harness.plugins, "create_resource", fail_upload)
        task = asyncio.create_task(transfers.run_job(job.uid))
        await asyncio.wait_for(upload_started.wait(), timeout=2)
        requested = await transfers.cancel(job.uid, actor_user_id=OWNER_ID)
        release_upload.set()
        finished = await asyncio.wait_for(task, timeout=2)

        assert requested.status == "cancelling"
        assert finished.status == "cancelled"
        assert await harness.files.list(parent_id=destination.uid) == []
        source_after = await harness.service.get(source.uid, actor_user_id=OWNER_ID)
        assert source_after.is_deleted is False
    finally:
        await harness.engine.dispose()


def test_humanize_getobject_403_vs_putobject() -> None:
    from apps.media_files.transfer_service import _humanize_transfer_error

    get_msg = _humanize_transfer_error(
        "Failed to cat: GetObject: AccessDenied: 403 Forbidden "
        "(incomplete chunked read)"
    )
    assert "GetObject 403" in get_msg
    assert "pointer clipboard" in get_msg
    assert "PutObject" not in get_msg

    gzip_msg = _humanize_transfer_error(
        "error decoding message body: gzip: invalid header / serialization failed"
    )
    assert "GetObject 403" in gzip_msg

    put_msg = _humanize_transfer_error("PutObject failed: AccessDenied 403 Forbidden")
    assert "PutObject" in put_msg or "denied the write" in put_msg
    assert "GetObject" not in put_msg
