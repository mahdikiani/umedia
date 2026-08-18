import hashlib
from pathlib import Path

import pytest
import pytest_asyncio

from plugins.contracts import Resource as PluginResource
from tests.media_file_helpers import FakeConnection, Harness, build_harness

CONNECTION_ID = "connection-1"
ACTOR_ID = "admin-1"


def _tree_listing() -> dict[str | None, list[PluginResource]]:
    return {
        None: [
            PluginResource(id="docs", type="folder", name="docs"),
            PluginResource(id="a.txt", type="file", name="a.txt", size=3),
        ],
        "docs": [
            PluginResource(
                id="docs/b.txt",
                type="file",
                name="b.txt",
                parent_id="docs",
                size=7,
            ),
        ],
    }


def _file_listing(*, size: int, mtime: float) -> dict[str | None, list[PluginResource]]:
    return {
        None: [
            PluginResource(
                id="a.txt",
                type="file",
                name="a.txt",
                size=size,
                metadata={"mtime": mtime},
            ),
        ],
    }


@pytest_asyncio.fixture
async def harness(tmp_path: Path) -> Harness:
    built = await build_harness(
        tmp_path,
        FakeConnection(
            uid=CONNECTION_ID,
            name="My library",
            import_existing=True,
        ),
    )
    yield built
    await built.engine.dispose()


@pytest.mark.asyncio
async def test_import_hashes_each_file_but_not_folders(harness: Harness) -> None:
    harness.plugins.listings[CONNECTION_ID] = _tree_listing()
    harness.plugins.store["a.txt"] = b"abc"

    await harness.service.import_from_provider(
        CONNECTION_ID,
        actor_user_id=ACTOR_ID,
    )

    objects = await harness.objects.list(provider_connection_id=CONNECTION_ID)
    by_reference = {obj.content_reference: obj for obj in objects}
    assert by_reference["docs"].content_hash is None
    assert by_reference["a.txt"].content_hash == hashlib.sha256(b"abc").hexdigest()
    assert by_reference["docs/b.txt"].content_hash == hashlib.sha256(
        b"x" * 7,
    ).hexdigest()


@pytest.mark.asyncio
async def test_unchanged_second_sync_reuses_hash_without_reading(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _tree_listing()

    await harness.service.import_from_provider(
        CONNECTION_ID,
        actor_user_id=ACTOR_ID,
    )
    reads_after_import = list(harness.plugins.plugin_calls("read"))

    await harness.service.import_from_provider(
        CONNECTION_ID,
        actor_user_id=ACTOR_ID,
    )

    assert reads_after_import == [
        ("read", CONNECTION_ID, "a.txt"),
        ("read", CONNECTION_ID, "docs/b.txt"),
    ]
    assert harness.plugins.plugin_calls("read") == reads_after_import


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("second_size", "second_mtime", "second_content"),
    [
        pytest.param(4, 1.0, b"new!", id="size-changed"),
        pytest.param(3, 2.0, b"NEW", id="mtime-changed"),
    ],
)
async def test_changed_remote_file_recomputes_hash(
    harness: Harness,
    second_size: int,
    second_mtime: float,
    second_content: bytes,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _file_listing(size=3, mtime=1.0)
    harness.plugins.store["a.txt"] = b"old"
    await harness.service.import_from_provider(
        CONNECTION_ID,
        actor_user_id=ACTOR_ID,
    )

    harness.plugins.listings[CONNECTION_ID] = _file_listing(
        size=second_size,
        mtime=second_mtime,
    )
    harness.plugins.store["a.txt"] = second_content
    await harness.service.import_from_provider(
        CONNECTION_ID,
        actor_user_id=ACTOR_ID,
    )

    obj = await harness.objects.get_by_reference(
        provider_connection_id=CONNECTION_ID,
        content_reference="a.txt",
    )
    assert obj is not None
    assert obj.content_hash == hashlib.sha256(second_content).hexdigest()
    assert harness.plugins.plugin_calls("read") == [
        ("read", CONNECTION_ID, "a.txt"),
        ("read", CONNECTION_ID, "a.txt"),
    ]
