"""Import job (docs/11-dual-layer-library.md "Sync"): plugin list ->
upsert StorageObjects -> MediaFiles under a library root folder named
after the connection. `import_if_enabled` is the connect-time entry point
gated by the connection's `import_existing` flag."""

from pathlib import Path

import pytest
import pytest_asyncio

from plugins.contracts import Resource as PluginResource
from tests.media_file_helpers import FakeConnection, Harness, build_harness

CONNECTION_ID = "connection-1"
ACTOR_ID = "admin-1"


def _tree_listing() -> dict[str | None, list[PluginResource]]:
    """A local-like provider: one folder with a nested file, one root file."""
    return {
        None: [
            PluginResource(
                id="docs", type="folder", name="docs", parent_id=None,
            ),
            PluginResource(
                id="a.txt", type="file", name="a.txt", parent_id=None,
                size=3, content_type="text/plain",
            ),
        ],
        "docs": [
            PluginResource(
                id="docs/b.txt", type="file", name="b.txt", parent_id="docs",
                size=7, content_type="text/plain",
            ),
        ],
    }


def _flat_listing() -> dict[str | None, list[PluginResource]]:
    """A Telegram-like flat provider: messages, no folders, no parents."""
    return {
        None: [
            PluginResource(id="msg-1", type="message", name="photo.jpg", size=10),
            PluginResource(id="msg-2", type="message", name="video.mp4", size=20),
        ],
    }


@pytest_asyncio.fixture
async def harness(tmp_path: Path) -> Harness:
    built = await build_harness(
        tmp_path,
        FakeConnection(
            uid=CONNECTION_ID, name="My library", import_existing=True,
        ),
    )
    yield built
    await built.engine.dispose()


@pytest.mark.asyncio
async def test_import_builds_the_library_under_a_connection_root(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _tree_listing()

    result = await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )

    assert result["imported"] == 3  # docs, a.txt, docs/b.txt

    roots = await harness.service.list_children(None, actor_user_id=ACTOR_ID)
    assert [r.name for r in roots.items] == ["My library"]
    root = roots.items[0]
    assert root.type == "folder"
    assert root.owner_id == ACTOR_ID

    level_one = await harness.service.list_children(
        root.uid, actor_user_id=ACTOR_ID,
    )
    assert sorted(r.name for r in level_one.items) == ["a.txt", "docs"]

    docs = next(r for r in level_one.items if r.name == "docs")
    nested = await harness.service.list_children(
        docs.uid, actor_user_id=ACTOR_ID,
    )
    assert [r.name for r in nested.items] == ["b.txt"]
    assert nested.items[0].size == 7
    assert nested.items[0].provider_connection_id == CONNECTION_ID
    assert nested.items[0].storage_object_uid is not None

    # The physical index has every remote object, folders included.
    objects = await harness.objects.list(provider_connection_id=CONNECTION_ID)
    assert {o.content_reference for o in objects} == {"docs", "a.txt", "docs/b.txt"}


@pytest.mark.asyncio
async def test_import_is_idempotent_across_reruns(harness: Harness) -> None:
    harness.plugins.listings[CONNECTION_ID] = _tree_listing()

    first = await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )
    second = await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )

    assert first["imported"] == 3
    assert second["imported"] == 0
    assert second["seen"] == 3

    roots = await harness.service.list_children(None, actor_user_id=ACTOR_ID)
    assert len(roots.items) == 1  # one root, not one per run
    objects = await harness.objects.list(provider_connection_id=CONNECTION_ID)
    assert len(objects) == 3  # upserted, not duplicated


@pytest.mark.asyncio
async def test_flat_provider_imports_everything_under_the_root(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _flat_listing()

    await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )

    roots = await harness.service.list_children(None, actor_user_id=ACTOR_ID)
    root = roots.items[0]
    children = await harness.service.list_children(
        root.uid, actor_user_id=ACTOR_ID,
    )
    assert sorted(r.name for r in children.items) == ["photo.jpg", "video.mp4"]
    assert all(r.parent_id == root.uid for r in children.items)


@pytest.mark.asyncio
async def test_import_if_enabled_respects_the_flag(tmp_path: Path) -> None:
    harness = await build_harness(
        tmp_path,
        FakeConnection(uid="conn-on", name="On", import_existing=True),
        FakeConnection(uid="conn-off", name="Off", import_existing=False),
    )
    try:
        harness.plugins.listings["conn-on"] = _flat_listing()
        harness.plugins.listings["conn-off"] = _flat_listing()

        ran = await harness.service.import_if_enabled(
            "conn-on", actor_user_id=ACTOR_ID,
        )
        skipped = await harness.service.import_if_enabled(
            "conn-off", actor_user_id=ACTOR_ID,
        )

        assert ran is not None
        assert ran["imported"] == 2
        assert skipped is None

        roots = await harness.service.list_children(
            None, actor_user_id=ACTOR_ID,
        )
        assert [r.name for r in roots.items] == ["On"]  # nothing imported for Off
        assert harness.plugins.plugin_calls("list") == [
            ("list", "conn-on", None),
        ]
    finally:
        await harness.engine.dispose()


def _file_listing(
    *, mtime: float, size: int = 3, content_type: str = "text/plain",
) -> dict[str | None, list[PluginResource]]:
    return {
        None: [
            PluginResource(
                id="a.txt",
                type="file",
                name="a.txt",
                parent_id=None,
                size=size,
                content_type=content_type,
                metadata={"mtime": mtime},
            ),
        ],
    }


@pytest.mark.asyncio
async def test_remote_newer_keeps_prior_version_in_history(
    harness: Harness,
) -> None:
    harness.plugins.listings[CONNECTION_ID] = _file_listing(mtime=1.0, size=3)
    harness.plugins.store["a.txt"] = b"old"

    first = await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )
    assert first["imported"] == 1

    harness.plugins.listings[CONNECTION_ID] = _file_listing(mtime=99.0, size=10)
    harness.plugins.store["a.txt"] = b"remote-new!"

    second = await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )
    assert second["imported"] == 0
    assert second["updated"] == 1
    assert second["pushed"] == 0

    roots = await harness.service.list_children(None, actor_user_id=ACTOR_ID)
    children = await harness.service.list_children(
        roots.items[0].uid, actor_user_id=ACTOR_ID,
    )
    media = children.items[0]
    assert media.size == 10
    assert len(media.history) == 1
    assert media.history[0].size == 3


@pytest.mark.asyncio
async def test_ours_newer_pushes_when_mirror_structure(
    tmp_path: Path,
) -> None:
    harness = await build_harness(
        tmp_path,
        FakeConnection(
            uid=CONNECTION_ID,
            name="My library",
            import_existing=True,
            mirror_structure=True,
        ),
    )
    try:
        harness.plugins.listings[CONNECTION_ID] = _file_listing(mtime=100.0, size=11)
        harness.plugins.store["a.txt"] = b"our-version"

        await harness.service.import_from_provider(
            CONNECTION_ID, actor_user_id=ACTOR_ID,
        )

        # Provider listing looks older; indexed mtime stays ahead.
        harness.plugins.listings[CONNECTION_ID] = _file_listing(mtime=50.0, size=11)
        harness.plugins.store["a.txt"] = b"our-version"

        result = await harness.service.import_from_provider(
            CONNECTION_ID, actor_user_id=ACTOR_ID,
        )
        assert result["pushed"] == 1
        assert result["updated"] == 0
        assert ("update", CONNECTION_ID, "a.txt", None, None) in (
            harness.plugins.plugin_calls("update")
        )
        assert harness.plugins.store["a.txt"] == b"our-version"
    finally:
        await harness.engine.dispose()


@pytest.mark.asyncio
async def test_ours_newer_skips_push_without_mirror(harness: Harness) -> None:
    harness.plugins.listings[CONNECTION_ID] = _file_listing(mtime=100.0, size=3)
    harness.plugins.store["a.txt"] = b"ours"

    await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )
    harness.plugins.listings[CONNECTION_ID] = _file_listing(mtime=50.0, size=3)

    result = await harness.service.import_from_provider(
        CONNECTION_ID, actor_user_id=ACTOR_ID,
    )
    assert result["pushed"] == 0
    assert harness.plugins.plugin_calls("update") == []
