"""Deleting a ProviderConnection soft-deletes bound MediaFiles (trash),
keeps StorageObjects / remote bytes, and clears placement references.
"""

from pathlib import Path

import pytest
import pytest_asyncio

from apps.media_files.errors import MediaFileNotFoundError
from apps.provider_connections.models import ProviderConnection  # noqa: F401
from apps.provider_connections.repository import ProviderConnectionRepository
from apps.provider_connections.services import ProviderConnectionService
from plugins.manifest import ConfigField, PluginManifest
from tests.media_file_helpers import FakeConnection, Harness, build_harness
from tests.test_provider_service import FakeCipher, FakeRegistry, passing_connect

CONNECTION_ID = "connection-1"
OWNER_ID = "owner-1"


@pytest_asyncio.fixture
async def harness(tmp_path: Path) -> Harness:
    built = await build_harness(
        tmp_path,
        FakeConnection(uid=CONNECTION_ID, owner_id=OWNER_ID),
    )
    yield built
    await built.engine.dispose()


async def _persist_connection(harness: Harness) -> str:
    row = await ProviderConnectionRepository(harness.session_factory).create(
        {
            "uid": CONNECTION_ID,
            "owner_id": OWNER_ID,
            "provider_type": "local",
            "name": "cascade-library",
            "encrypted_config": "cipher-text",
            "status": "configured",
            "enabled": True,
        },
    )
    return row.uid


def _connection_service(harness: Harness) -> ProviderConnectionService:
    return ProviderConnectionService(
        ProviderConnectionRepository(harness.session_factory),
        FakeCipher(),
        FakeRegistry(
            {
                "local": PluginManifest(
                    id="local",
                    name="Local",
                    description="test",
                    entrypoint=["python", "-m", "plugins.local.main"],
                    config_fields=(
                        ConfigField(key="root_path", label="Root path"),
                    ),
                    capabilities=("list", "read", "write", "delete", "move"),
                ),
            },
        ),
        passing_connect,
    )


@pytest.mark.asyncio
async def test_delete_connection_soft_deletes_library_keeps_objects(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uid = await _persist_connection(harness)
    await harness.settings.update(
        {
            "placement_policy": "fill_order",
            "default_connection_id": uid,
            "fill_order": [uid, "other-connection"],
        },
    )

    folder = await harness.service.create_folder(
        name="docs",
        parent_id=None,
        owner_id=OWNER_ID,
    )
    child = await harness.service.upload(
        provider_connection_id=uid,
        parent_id=folder.uid,
        name="child.txt",
        content=b"payload",
        owner_id=OWNER_ID,
    )
    root_file = await harness.service.upload(
        provider_connection_id=uid,
        parent_id=None,
        name="root.txt",
        content=b"root",
        owner_id=OWNER_ID,
    )
    child_obj_uid = child.storage_object_uid
    root_obj_uid = root_file.storage_object_uid

    original_bulk_delete = harness.files.soft_delete_many
    bulk_delete_calls: list[list[str]] = []

    async def record_bulk_delete(uids: list[str]) -> None:
        bulk_delete_calls.append(uids)
        await original_bulk_delete(uids)

    monkeypatch.setattr(harness.files, "soft_delete_many", record_bulk_delete)

    deleted = await _connection_service(harness).delete(
        uid,
        owner_id=OWNER_ID,
        library=harness.service,
        placement=harness.settings,
    )
    assert deleted is True
    assert len(bulk_delete_calls) == 1
    assert set(bulk_delete_calls[0]) == {folder.uid, child.uid, root_file.uid}

    assert await ProviderConnectionRepository(harness.session_factory).get(
        uid, owner_id=OWNER_ID,
    ) is None

    with pytest.raises(MediaFileNotFoundError):
        await harness.service.get(folder.uid, actor_user_id=OWNER_ID)
    with pytest.raises(MediaFileNotFoundError):
        await harness.service.get(child.uid, actor_user_id=OWNER_ID)
    with pytest.raises(MediaFileNotFoundError):
        await harness.service.get(root_file.uid, actor_user_id=OWNER_ID)

    folder_row = await harness.files.get(folder.uid)
    child_row = await harness.files.get(child.uid)
    root_row = await harness.files.get(root_file.uid)
    assert folder_row is not None and folder_row.is_deleted
    assert child_row is not None and child_row.is_deleted
    assert root_row is not None and root_row.is_deleted

    assert await harness.objects.get(child_obj_uid) is not None
    assert await harness.objects.get(root_obj_uid) is not None
    assert (await harness.objects.get(child_obj_uid)).is_deleted is False
    assert harness.plugins.plugin_calls("delete") == []

    placement = await harness.settings.get()
    assert placement.default_connection_id is None
    assert placement.fill_order == ["other-connection"]


@pytest.mark.asyncio
async def test_delete_connection_missing_returns_false(harness: Harness) -> None:
    deleted = await _connection_service(harness).delete(
        "missing",
        owner_id=OWNER_ID,
        library=harness.service,
        placement=harness.settings,
    )
    assert deleted is False


@pytest.mark.asyncio
async def test_soft_delete_for_connection_counts_forest_roots(
    harness: Harness,
) -> None:
    await harness.settings.update(
        {
            "placement_policy": "default",
            "default_connection_id": CONNECTION_ID,
        },
    )
    folder = await harness.service.create_folder(
        name="tree",
        parent_id=None,
        owner_id=OWNER_ID,
    )
    await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=folder.uid,
        name="nested.txt",
        content=b"x",
        owner_id=OWNER_ID,
    )
    await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="sibling.txt",
        content=b"y",
        owner_id=OWNER_ID,
    )

    roots = await harness.service.soft_delete_for_connection(CONNECTION_ID)
    # Folder root + sibling file root; nested child is covered by the tree.
    assert roots == 2
