"""Mirror behavior (docs/11-dual-layer-library.md): a library move/rename
touching a linked file is reflected on the provider only when the
connection has `mirror_structure` AND the plugin supports structure
(capability "move"). Otherwise structure lives only in UMedia."""

from pathlib import Path

import pytest
import pytest_asyncio

from apps.media_files.errors import MediaFileWriteFailedError
from plugins.contracts import Resource as PluginResource
from tests.media_file_helpers import FakeConnection, Harness, build_harness

MIRRORED = "conn-mirrored"
PLAIN = "conn-plain"
FLAT = "conn-flat"
OWNER_ID = "user-1"


@pytest_asyncio.fixture
async def harness(tmp_path: Path) -> Harness:
    built = await build_harness(
        tmp_path,
        FakeConnection(uid=MIRRORED, name="Mirrored", mirror_structure=True),
        FakeConnection(uid=PLAIN, name="Plain", mirror_structure=False),
        FakeConnection(uid=FLAT, name="Flat", mirror_structure=True),
    )
    # Telegram-like: mirror requested but the provider can't do structure.
    built.plugins.capabilities_by_connection[FLAT] = (
        "list", "read", "write", "delete", "copy",
    )
    yield built
    await built.engine.dispose()


async def _uploaded(harness: Harness, connection_id: str) -> object:
    return await harness.service.upload(
        provider_connection_id=connection_id,
        parent_id=None,
        name="file.txt",
        content=b"x",
        owner_id=OWNER_ID,
    )


@pytest.mark.asyncio
async def test_rename_is_mirrored_when_flag_and_capability_allow(
    harness: Harness,
) -> None:
    created = await _uploaded(harness, MIRRORED)
    harness.plugins.calls.clear()

    renamed = await harness.service.update(
        created.uid, actor_user_id=OWNER_ID, name="renamed.txt",
    )

    assert renamed.name == "renamed.txt"
    updates = harness.plugins.plugin_calls("update")
    assert updates == [
        ("update", MIRRORED, created.content_reference, "renamed.txt", None),
    ]


@pytest.mark.asyncio
async def test_rename_is_library_only_when_mirror_is_off(
    harness: Harness,
) -> None:
    created = await _uploaded(harness, PLAIN)
    harness.plugins.calls.clear()

    renamed = await harness.service.update(
        created.uid, actor_user_id=OWNER_ID, name="renamed.txt",
    )

    assert renamed.name == "renamed.txt"
    assert harness.plugins.plugin_calls("update") == []


@pytest.mark.asyncio
async def test_rename_is_library_only_when_the_provider_is_flat(
    harness: Harness,
) -> None:
    created = await _uploaded(harness, FLAT)
    harness.plugins.calls.clear()

    renamed = await harness.service.update(
        created.uid, actor_user_id=OWNER_ID, name="renamed.txt",
    )

    assert renamed.name == "renamed.txt"
    assert harness.plugins.plugin_calls("update") == []


@pytest.mark.asyncio
async def test_move_into_a_provider_backed_folder_passes_the_parent_ref(
    harness: Harness,
) -> None:
    """An imported folder MediaFile is linked to a folder StorageObject --
    moving a mirrored file into it can and does tell the provider."""
    harness.plugins.listings[MIRRORED] = {
        None: [PluginResource(id="docs", type="folder", name="docs")],
        "docs": [],
    }
    await harness.service.import_from_provider(
        MIRRORED, actor_user_id=OWNER_ID,
    )
    roots = await harness.service.list_children(None, actor_user_id=OWNER_ID)
    root = next(r for r in roots.items if r.name == "Mirrored")
    docs = (await harness.service.list_children(
        root.uid, actor_user_id=OWNER_ID,
    )).items[0]
    created = await _uploaded(harness, MIRRORED)
    harness.plugins.calls.clear()

    moved = await harness.service.update(
        created.uid, actor_user_id=OWNER_ID, parent_id=docs.uid,
    )

    assert moved.parent_id == docs.uid
    updates = harness.plugins.plugin_calls("update")
    assert updates == [
        ("update", MIRRORED, created.content_reference, None, "docs"),
    ]


@pytest.mark.asyncio
async def test_move_into_a_library_only_folder_stays_library_only(
    harness: Harness,
) -> None:
    """A pure library folder has no provider counterpart, so there is
    nothing to mirror the move onto -- even with mirror_structure on."""
    folder = await harness.service.create_folder(
        name="library-folder", parent_id=None, owner_id=OWNER_ID,
    )
    created = await _uploaded(harness, MIRRORED)
    harness.plugins.calls.clear()

    moved = await harness.service.update(
        created.uid, actor_user_id=OWNER_ID, parent_id=folder.uid,
    )

    assert moved.parent_id == folder.uid
    assert harness.plugins.plugin_calls("update") == []


@pytest.mark.asyncio
async def test_mirror_failure_leaves_the_library_unchanged(
    harness: Harness,
) -> None:
    created = await _uploaded(harness, MIRRORED)
    harness.plugins.fail_update = True

    with pytest.raises(MediaFileWriteFailedError):
        await harness.service.update(
            created.uid, actor_user_id=OWNER_ID, name="renamed.txt",
        )

    unchanged = await harness.service.get(created.uid, actor_user_id=OWNER_ID)
    assert unchanged.name == "file.txt"
