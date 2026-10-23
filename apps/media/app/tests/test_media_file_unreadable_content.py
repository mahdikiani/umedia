"""Files whose bytes the provider will not hand over (GlitchTip UMEDIA-2).

The hash backfill records a 404/422 read on the StorageObject, so a
restart no longer re-reads (and re-reports) every such file; a change seen
by sync, or enough time, makes the file eligible again. Opening such a file
answers 404/502 before any byte instead of a 200 with a cut-off body.
"""

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from apps.media_files import services
from apps.media_files.errors import (
    MediaFileContentUnavailableError,
    MediaFileNotFoundError,
)
from apps.s3.exceptions import InvalidArgument, ServiceUnavailable
from apps.s3.services import S3ObjectService
from plugins.client import PluginRPCError
from plugins.contracts import Resource as PluginResource
from tests.media_file_helpers import FakeConnection, Harness, build_harness

CONNECTION_ID = "connection-1"
ACTOR_ID = "admin-1"
LINK = ".oh-my-zsh/plugins/zsh-syntax-highlighting/highlighters/cursor/README.md"

NOT_FOUND = PluginRPCError(
    "Plugin content read returned 404 (ResourceNotFoundError)",
    status_code=404,
    code="ResourceNotFoundError",
)
PROVIDER_DOWN = PluginRPCError(
    "Plugin content read returned 502 (PluginBackendError)",
    status_code=502,
    code="PluginBackendError",
)


def _listing(size: int = 762) -> dict[str | None, list[PluginResource]]:
    return {
        None: [
            PluginResource(id="ok.txt", type="file", name="ok.txt", size=3),
            PluginResource(id=LINK, type="file", name="README.md", size=size),
        ],
    }


@pytest_asyncio.fixture
async def harness(tmp_path: Path) -> Harness:
    built = await build_harness(
        tmp_path,
        FakeConnection(uid=CONNECTION_ID, name="mobin", import_existing=True),
    )
    built.plugins.store["ok.txt"] = b"abc"
    yield built
    await built.engine.dispose()


def _reads(harness: Harness, reference: str) -> int:
    return sum(
        1 for call in harness.plugins.plugin_calls("read") if call[2] == reference
    )


async def _object(harness: Harness, reference: str) -> object:
    objects = await harness.objects.list(provider_connection_id=CONNECTION_ID)
    return next(obj for obj in objects if obj.content_reference == reference)


async def _sync(harness: Harness) -> None:
    await harness.service.import_from_provider(CONNECTION_ID, actor_user_id=ACTOR_ID)


# ----------------------------------------------------------------------
# Hash backfill
# ----------------------------------------------------------------------


async def test_unreadable_file_is_recorded_and_not_reread_after_a_restart(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _listing()
    harness.plugins.read_errors[LINK] = NOT_FOUND
    await _sync(harness)

    await harness.service.hash_missing_content(CONNECTION_ID)
    link = await _object(harness, LINK)
    assert link.content_hash is None
    assert link.hash_error is not None
    assert "404" in link.hash_error
    assert link.hash_error_at is not None
    assert (await _object(harness, "ok.txt")).content_hash

    services._hash_failures.clear()  # a restart forgets the in-memory backoff
    await harness.service.hash_missing_content(CONNECTION_ID)
    await harness.service.hash_missing_content(CONNECTION_ID)
    assert _reads(harness, LINK) == 1


async def test_a_change_seen_by_sync_makes_the_file_readable_again(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _listing()
    harness.plugins.read_errors[LINK] = NOT_FOUND
    await _sync(harness)
    await harness.service.hash_missing_content(CONNECTION_ID)

    await _sync(harness)  # unchanged: stays recorded
    assert (await _object(harness, LINK)).hash_error is not None

    harness.plugins.listings[CONNECTION_ID] = _listing(size=800)
    harness.plugins.read_errors.clear()
    harness.plugins.store[LINK] = b"x" * 800
    await _sync(harness)
    assert (await _object(harness, LINK)).hash_error is None

    await harness.service.hash_missing_content(CONNECTION_ID)
    link = await _object(harness, LINK)
    assert link.content_hash
    assert _reads(harness, LINK) == 2


async def test_a_recorded_failure_gets_another_try_after_the_retry_window(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _listing()
    harness.plugins.read_errors[LINK] = NOT_FOUND
    await _sync(harness)
    await harness.service.hash_missing_content(CONNECTION_ID)

    link = await _object(harness, LINK)
    aged = datetime.now() - services.HASH_ERROR_RETRY_AFTER - timedelta(hours=1)
    await harness.objects.update(link.uid, {"hash_error_at": aged})
    await harness.service.hash_missing_content(CONNECTION_ID)
    assert _reads(harness, LINK) == 2
    assert (await _object(harness, LINK)).hash_error_at > aged  # recorded again


async def test_a_transient_read_failure_is_not_recorded(harness: Harness) -> None:
    harness.plugins.listings[CONNECTION_ID] = _listing()
    harness.plugins.read_errors[LINK] = PROVIDER_DOWN
    await _sync(harness)

    await harness.service.hash_missing_content(CONNECTION_ID)
    link = await _object(harness, LINK)
    assert link.hash_error is None
    assert link.uid in services._hash_failures  # in-memory backoff instead
    services._hash_failures.pop(link.uid, None)


async def test_storage_browse_shows_the_recorded_failure(harness: Harness) -> None:
    from apps.media_files.api_schemas import StorageObjectOut

    harness.plugins.listings[CONNECTION_ID] = _listing()
    harness.plugins.read_errors[LINK] = NOT_FOUND
    await _sync(harness)
    await harness.service.hash_missing_content(CONNECTION_ID)

    out = StorageObjectOut.from_record(await _object(harness, LINK))
    assert out.hash_error is not None
    assert out.hash_error_at is not None


# ----------------------------------------------------------------------
# Opening the file
# ----------------------------------------------------------------------


async def _library_uid(harness: Harness, name: str) -> str:
    roots = await harness.service.list_children(None, actor_user_id=ACTOR_ID)
    children = await harness.service.list_children(
        roots.items[0].uid, actor_user_id=ACTOR_ID,
    )
    return next(item.uid for item in children.items if item.name == name)


async def test_opening_a_file_the_provider_says_is_gone_is_a_404(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _listing()
    harness.plugins.read_errors[LINK] = NOT_FOUND
    await _sync(harness)

    with pytest.raises(MediaFileNotFoundError):
        await harness.service.read_content(
            await _library_uid(harness, "README.md"), actor_user_id=ACTOR_ID,
        )


async def test_opening_a_file_while_the_provider_fails_is_a_502(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _listing()
    harness.plugins.read_errors[LINK] = PROVIDER_DOWN
    await _sync(harness)

    with pytest.raises(MediaFileContentUnavailableError) as caught:
        await harness.service.read_content(
            await _library_uid(harness, "README.md"), actor_user_id=ACTOR_ID,
        )
    assert caught.value.status_code == 502


async def test_opening_a_readable_file_still_streams_all_of_it(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _listing()
    await _sync(harness)

    _, stream = await harness.service.read_content(
        await _library_uid(harness, "ok.txt"), actor_user_id=ACTOR_ID,
    )
    assert b"".join([chunk async for chunk in stream]) == b"abc"


async def test_s3_get_object_reports_a_failed_provider_read_as_retryable() -> None:
    media_files = SimpleNamespace(
        read_content=AsyncMock(side_effect=MediaFileContentUnavailableError("down")),
    )
    s3 = S3ObjectService(user_id=ACTOR_ID, media_files=media_files, connections=None)
    s3._record_from_key = AsyncMock(
        return_value=SimpleNamespace(uid="file-1", size=3),
    )
    s3.scope_for = AsyncMock(return_value=None)

    with pytest.raises(ServiceUnavailable) as caught:
        await s3.get_object(bucket="mobin", key="a/link.md")
    assert caught.value.status_code == 503


async def test_s3_get_object_checks_the_range_before_starting_the_read() -> None:
    """An invalid Range must not leave a provider read running unread."""
    media_files = SimpleNamespace(read_content=AsyncMock())
    s3 = S3ObjectService(user_id=ACTOR_ID, media_files=media_files, connections=None)
    s3._record_from_key = AsyncMock(
        return_value=SimpleNamespace(uid="file-1", size=3),
    )
    s3.scope_for = AsyncMock(return_value=None)

    with pytest.raises(InvalidArgument):
        await s3.get_object(bucket="mobin", key="a/b.txt", range_header="bytes=5-9")
    media_files.read_content.assert_not_awaited()


async def test_a_started_read_is_closed_when_touch_access_fails(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _listing()
    await _sync(harness)
    closed: list[bool] = []

    async def tracked(*_args: object, **_kwargs: object) -> AsyncIterator[bytes]:  # noqa: RUF029
        try:
            yield b"a"
            yield b"bc"
        finally:
            closed.append(True)

    monkeypatch.setattr(harness.plugins, "read_content", tracked)
    monkeypatch.setattr(
        harness.files, "touch_access", AsyncMock(side_effect=RuntimeError("locked")),
    )
    with pytest.raises(RuntimeError, match="locked"):
        await harness.service.read_content(
            await _library_uid(harness, "ok.txt"), actor_user_id=ACTOR_ID,
        )
    assert closed == [True]


# ----------------------------------------------------------------------
# When a recorded failure goes stale
# ----------------------------------------------------------------------


async def test_a_new_mtime_alone_makes_the_file_readable_again(
    harness: Harness,
) -> None:
    def listing(mtime: str) -> dict[str | None, list[PluginResource]]:
        return {
            None: [
                PluginResource(
                    id=LINK, type="file", name="README.md", size=762,
                    metadata={"mtime": mtime},
                ),
            ],
        }

    harness.plugins.listings[CONNECTION_ID] = listing("2026-10-01T00:00:00Z")
    harness.plugins.read_errors[LINK] = NOT_FOUND
    await _sync(harness)
    await harness.service.hash_missing_content(CONNECTION_ID)
    assert (await _object(harness, LINK)).hash_error is not None

    harness.plugins.listings[CONNECTION_ID] = listing("2026-10-20T00:00:00Z")
    await _sync(harness)  # same size, newer mtime
    assert (await _object(harness, LINK)).hash_error is None


async def test_a_change_that_races_the_backfill_still_clears_the_failure(
    harness: Harness,
) -> None:
    """The backfill records the error between sync reading the object and
    sync writing the new size: the upsert itself must notice the change."""
    fields = {
        "provider_connection_id": CONNECTION_ID,
        "content_reference": LINK,
        "type": "file",
        "name": "README.md",
        "content_hash": None,
        "size": 762,
        "metadata": {},
        "status": "active",
    }
    obj = await harness.objects.upsert(fields)  # what sync read: no error yet
    assert await harness.objects.set_hash_error_if_unchanged(
        uid=obj.uid, expected_size=762, expected_metadata={}, error="404",
    )

    refreshed = await harness.objects.upsert({**fields, "size": 800})
    assert refreshed.hash_error is None
    assert refreshed.hash_error_at is None

    unchanged = await harness.objects.update(
        obj.uid, {"hash_error": "404", "hash_error_at": datetime.now()},
    )
    assert unchanged.hash_error == "404"
    assert (await harness.objects.upsert({**fields, "size": 800})).hash_error == "404"


async def test_a_write_that_stores_the_hash_clears_a_recorded_failure(
    harness: Harness,
) -> None:
    fields = {
        "provider_connection_id": CONNECTION_ID,
        "content_reference": LINK,
        "type": "file",
        "name": "README.md",
        "content_hash": None,
        "size": 762,
        "metadata": {},
        "status": "active",
    }
    obj = await harness.objects.upsert(fields)
    await harness.objects.update(
        obj.uid, {"hash_error": "404", "hash_error_at": datetime.now()},
    )

    written = await harness.objects.upsert({**fields, "content_hash": "abc123"})
    assert written.content_hash == "abc123"
    assert written.hash_error is None
