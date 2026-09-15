import importlib.util
import os
from pathlib import Path
from types import ModuleType

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import UniqueConstraint

from apps.media_files import models
from apps.media_files.errors import MediaFileNotFoundError
from apps.media_files.transfer_repository import TransferRepository
from tests.media_file_helpers import FakeConnection, Harness, build_harness

CONNECTION_ID = "temporary-connection"
OWNER_ID = "temporary-owner"
OTHER_USER_ID = "temporary-reader"

ADMIN_CREDENTIALS = {
    "email": "admin@example.com",
    "password": "a secure first password",
}


@pytest_asyncio.fixture
async def harness(tmp_path: Path) -> Harness:
    built = await build_harness(tmp_path, FakeConnection(uid=CONNECTION_ID))
    yield built
    await built.engine.dispose()


@pytest.mark.asyncio
async def test_add_is_idempotent_and_never_creates_files_objects_or_transfers(
    harness: Harness,
) -> None:
    original = await harness.service.upload(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="original.txt",
        content=b"original bytes",
        owner_id=OWNER_ID,
    )
    plugin_calls = list(harness.plugins.calls)
    root_before = await harness.files.list(parent_id=None)
    objects_before = await harness.objects.list(
        provider_connection_id=CONNECTION_ID,
    )

    first_added = await harness.service.add_temporary_items(
        [original.uid],
        actor_user_id=OWNER_ID,
    )
    second_added = await harness.service.add_temporary_items(
        [original.uid],
        actor_user_id=OWNER_ID,
    )

    assert first_added == 1
    assert second_added == 0
    assert [item.uid for item in await harness.service.list_temporary_items(
        actor_user_id=OWNER_ID,
    )] == [original.uid]
    assert await harness.files.list(parent_id=None) == root_before
    assert await harness.objects.list(
        provider_connection_id=CONNECTION_ID,
    ) == objects_before
    assert harness.plugins.calls == plugin_calls
    assert await TransferRepository(harness.session_factory).list_for_owner(
        owner_id=OWNER_ID,
    ) == []


@pytest.mark.asyncio
async def test_remove_and_clear_delete_only_callers_pointers(
    harness: Harness,
) -> None:
    first = await harness.service.create_folder(
        name="First",
        parent_id=None,
        owner_id=OWNER_ID,
    )
    second = await harness.service.create_folder(
        name="Second",
        parent_id=None,
        owner_id=OWNER_ID,
    )
    await harness.service.add_temporary_items(
        [first.uid, second.uid],
        actor_user_id=OWNER_ID,
    )
    await harness.service.set_user_permission(
        first.uid,
        actor_user_id=OWNER_ID,
        target_user_id=OTHER_USER_ID,
        permission=10,
    )
    await harness.service.add_temporary_items(
        [first.uid],
        actor_user_id=OTHER_USER_ID,
    )

    await harness.service.remove_temporary_item(
        first.uid,
        actor_user_id=OWNER_ID,
    )

    assert [item.uid for item in await harness.service.list_temporary_items(
        actor_user_id=OWNER_ID,
    )] == [second.uid]
    assert [item.uid for item in await harness.service.list_temporary_items(
        actor_user_id=OTHER_USER_ID,
    )] == [first.uid]
    assert (await harness.files.get(first.uid)) is not None

    await harness.service.clear_temporary_items(actor_user_id=OWNER_ID)

    assert await harness.service.list_temporary_items(actor_user_id=OWNER_ID) == []
    assert (await harness.files.get(second.uid)) is not None


@pytest.mark.asyncio
async def test_add_requires_read_and_list_skips_access_revoked_after_add(
    harness: Harness,
) -> None:
    item = await harness.service.create_folder(
        name="Shared briefly",
        parent_id=None,
        owner_id=OWNER_ID,
    )

    with pytest.raises(MediaFileNotFoundError):
        await harness.service.add_temporary_items(
            [item.uid],
            actor_user_id=OTHER_USER_ID,
        )

    await harness.service.set_user_permission(
        item.uid,
        actor_user_id=OWNER_ID,
        target_user_id=OTHER_USER_ID,
        permission=10,
    )
    await harness.service.add_temporary_items(
        [item.uid],
        actor_user_id=OTHER_USER_ID,
    )
    await harness.service.set_user_permission(
        item.uid,
        actor_user_id=OWNER_ID,
        target_user_id=OTHER_USER_ID,
        permission=0,
    )

    assert await harness.service.list_temporary_items(
        actor_user_id=OTHER_USER_ID,
    ) == []


@pytest.mark.asyncio
async def test_legacy_staging_folder_is_hidden_from_root_browse(
    harness: Harness,
) -> None:
    staging = await harness.files.create({
        "owner_id": OWNER_ID,
        "type": "folder",
        "name": "Temporary",
        "parent_id": None,
        "provider_connection_id": None,
        "metadata": {"staging": True},
        "status": "completed",
        "error": None,
        "public_permission": "none",
        "permissions": [],
        "workspace_id": None,
    })
    visible = await harness.service.list_children(
        None,
        actor_user_id=OWNER_ID,
    )

    assert staging.uid not in {item.uid for item in visible.items}


@pytest.mark.asyncio
async def test_legacy_staging_folder_and_children_are_not_addressable(
    harness: Harness,
) -> None:
    staging = await harness.files.create({
        "owner_id": OWNER_ID,
        "type": "folder",
        "name": "Temporary",
        "parent_id": None,
        "provider_connection_id": None,
        "metadata": {"staging": True},
        "status": "completed",
        "error": None,
        "public_permission": "none",
        "permissions": [],
        "workspace_id": None,
    })
    child = await harness.files.create({
        "owner_id": OWNER_ID,
        "type": "file",
        "name": "stale-copy.txt",
        "parent_id": staging.uid,
        "provider_connection_id": None,
        "metadata": {},
        "status": "completed",
        "error": None,
        "public_permission": "none",
        "permissions": [],
        "workspace_id": None,
    })

    with pytest.raises(MediaFileNotFoundError):
        await harness.service.get(staging.uid, actor_user_id=OWNER_ID)
    with pytest.raises(MediaFileNotFoundError):
        await harness.service.get(child.uid, actor_user_id=OWNER_ID)

    listed = await harness.service.list_children(
        staging.uid,
        actor_user_id=OWNER_ID,
    )
    assert listed.items == []


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0012_temporary_items.py"
    )
    spec = importlib.util.spec_from_file_location("temporary_items_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_temporary_model_and_migration_match_star_shape() -> None:
    temporary_model = getattr(models, "MediaFileTemporaryItem", None)
    assert temporary_model is not None
    unique_columns = {
        tuple(constraint.columns.keys())
        for constraint in temporary_model.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    migration = _load_migration()

    assert migration.revision == "0012_temporary_items"
    assert migration.down_revision == "0011_library_transfers"
    assert ("user_id", "media_file_id") in unique_columns

    purge = importlib.util.spec_from_file_location(
        "purge_staging_migration",
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0013_purge_legacy_staging_folders.py",
    )
    assert purge is not None and purge.loader is not None
    purge_module = importlib.util.module_from_spec(purge)
    purge.loader.exec_module(purge_module)
    assert purge_module.down_revision == "0012_temporary_items"


async def _authenticated(client: httpx.AsyncClient) -> None:
    state = (await client.get("/auth/state")).json()
    if state["configured"]:
        response = await client.post("/auth/sessions", json=ADMIN_CREDENTIALS)
    else:
        response = await client.post("/auth/setup", json=ADMIN_CREDENTIALS)
    assert response.status_code == 201, response.text


def _library_dir(name: str) -> Path:
    return Path(os.environ["UMEDIA_DATA_DIR"]) / "storage" / name


@pytest.fixture(scope="module")
async def connection_id(client: httpx.AsyncClient) -> str:
    await _authenticated(client)
    response = await client.post(
        "/providers",
        json={
            "provider_type": "local",
            "name": "Temporary pointer routes",
            "config": {"root_path": str(_library_dir("temporary-pointer-routes"))},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["uid"]


@pytest.mark.asyncio
async def test_temporary_routes_never_enqueue_or_copy(
    client: httpx.AsyncClient,
    connection_id: str,
) -> None:
    await _authenticated(client)
    uploaded = await client.post(
        "/files",
        data={"provider_connection_id": connection_id, "name": "route-pointer.txt"},
        files={"file": ("route-pointer.txt", b"route bytes", "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    original = uploaded.json()
    transfers_before = (await client.get("/files/transfers")).json()

    added = await client.post(
        "/files/temporary",
        json={"media_file_ids": [original["uid"], original["uid"]]},
    )
    listed = await client.get("/files/temporary")

    assert added.status_code == 204, added.text
    assert listed.status_code == 200, listed.text
    assert [item["uid"] for item in listed.json()] == [original["uid"]]
    assert (await client.get("/files/transfers")).json() == transfers_before
    fetched = await client.get(f"/files/{original['uid']}")
    assert fetched.json()["parent_id"] == original["parent_id"]

    removed = await client.delete(f"/files/temporary/{original['uid']}")
    assert removed.status_code == 204
    assert (await client.get("/files/temporary")).json() == []

    await client.post(
        "/files/temporary",
        json={"media_file_ids": [original["uid"]]},
    )
    cleared = await client.delete("/files/temporary")
    assert cleared.status_code == 204
    assert (await client.get("/files/temporary")).json() == []
    assert (await client.get(f"/files/{original['uid']}")).status_code == 200
