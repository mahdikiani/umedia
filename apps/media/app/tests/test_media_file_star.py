import importlib.util
from pathlib import Path
from types import ModuleType

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import UniqueConstraint

from apps.media_files import models
from apps.media_files.errors import (
    MediaFileNotFoundError,
    MediaFileValidationError,
)
from tests.media_file_helpers import FakeConnection, Harness, build_harness

CONNECTION_ID = "star-connection"
OWNER_ID = "star-owner"
OTHER_USER_ID = "star-reader"

ADMIN_CREDENTIALS = {
    "email": "admin@example.com",
    "password": "a secure first password",
}


@pytest_asyncio.fixture
async def harness(tmp_path: Path) -> Harness:
    built = await build_harness(tmp_path, FakeConnection(uid=CONNECTION_ID))
    yield built
    await built.engine.dispose()


async def _folder(harness: Harness, *, owner_id: str = OWNER_ID):
    return await harness.service.create_folder(
        name="Star me",
        parent_id=None,
        owner_id=owner_id,
    )


@pytest.mark.asyncio
async def test_star_then_starred_scope_returns_actor_item(
    harness: Harness,
) -> None:
    item = await _folder(harness)

    starred = await harness.service.set_starred(
        item.uid,
        actor_user_id=OWNER_ID,
        starred=True,
    )
    listed = await harness.service.list_children(
        "ignored-parent",
        actor_user_id=OWNER_ID,
        scope="starred",
    )

    assert starred.starred is True
    assert [record.uid for record in listed.items] == [item.uid]
    assert listed.items[0].starred is True


@pytest.mark.asyncio
async def test_unstar_removes_item_and_regular_reads_report_false(
    harness: Harness,
) -> None:
    item = await _folder(harness)
    await harness.service.set_starred(
        item.uid,
        actor_user_id=OWNER_ID,
        starred=True,
    )

    fetched = await harness.service.get(item.uid, actor_user_id=OWNER_ID)
    listed = await harness.service.list_children(None, actor_user_id=OWNER_ID)
    unstarred = await harness.service.set_starred(
        item.uid,
        actor_user_id=OWNER_ID,
        starred=False,
    )
    starred_scope = await harness.service.list_children(
        None,
        actor_user_id=OWNER_ID,
        scope="starred",
    )

    assert fetched.starred is True
    assert listed.items[0].starred is True
    assert unstarred.starred is False
    assert starred_scope.items == []


@pytest.mark.asyncio
async def test_cannot_star_without_read_access(harness: Harness) -> None:
    item = await _folder(harness)

    with pytest.raises(MediaFileNotFoundError):
        await harness.service.set_starred(
            item.uid,
            actor_user_id=OTHER_USER_ID,
            starred=True,
        )


@pytest.mark.asyncio
async def test_another_users_stars_do_not_leak(harness: Harness) -> None:
    item = await _folder(harness)
    await harness.service.set_user_permission(
        item.uid,
        actor_user_id=OWNER_ID,
        target_user_id=OTHER_USER_ID,
        permission=10,
    )
    await harness.service.set_starred(
        item.uid,
        actor_user_id=OTHER_USER_ID,
        starred=True,
    )

    owner_starred = await harness.service.list_children(
        None,
        actor_user_id=OWNER_ID,
        scope="starred",
    )
    reader_starred = await harness.service.list_children(
        None,
        actor_user_id=OTHER_USER_ID,
        scope="starred",
    )

    assert owner_starred.items == []
    assert [record.uid for record in reader_starred.items] == [item.uid]


@pytest.mark.asyncio
async def test_starred_scope_never_exposes_deleted_files(
    harness: Harness,
) -> None:
    item = await _folder(harness)
    await harness.service.set_starred(
        item.uid,
        actor_user_id=OWNER_ID,
        starred=True,
    )
    await harness.service.soft_delete(item.uid, actor_user_id=OWNER_ID)

    assert (await harness.service.list_children(
        None,
        actor_user_id=OWNER_ID,
        scope="starred",
    )).items == []
    with pytest.raises(MediaFileValidationError):
        await harness.service.list_children(
            None,
            actor_user_id=OWNER_ID,
            scope="starred",
            include_deleted=True,
        )


def _load_star_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0006_media_file_stars.py"
    )
    spec = importlib.util.spec_from_file_location("media_file_stars_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_chain_and_actor_file_uniqueness() -> None:
    star_model = getattr(models, "MediaFileStar", None)
    assert star_model is not None
    unique_columns = {
        tuple(constraint.columns.keys())
        for constraint in star_model.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    migration = _load_star_migration()

    assert migration.revision == "0006_media_file_stars"
    assert migration.down_revision == "0005_storage_media_files"
    assert ("user_id", "media_file_id") in unique_columns


async def _authenticated(client: httpx.AsyncClient) -> None:
    state = (await client.get("/auth/state")).json()
    if state["configured"]:
        response = await client.post("/auth/sessions", json=ADMIN_CREDENTIALS)
    else:
        response = await client.post("/auth/setup", json=ADMIN_CREDENTIALS)
    assert response.status_code == 201, response.text


@pytest.mark.asyncio
@pytest.mark.skip(reason="ASGI LifespanManager client fixture currently times out at startup")
async def test_patch_star_round_trip_and_actor_isolation(
    client: httpx.AsyncClient,
) -> None:
    await _authenticated(client)
    created = await client.post(
        "/files",
        data={"name": "Route star folder", "type": "folder"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["starred"] is False
    uid = created.json()["uid"]

    starred = await client.patch(f"/files/{uid}", json={"starred": True})
    assert starred.status_code == 200, starred.text
    assert starred.json()["starred"] is True
    assert (await client.get(f"/files/{uid}")).json()["starred"] is True
    regular = (await client.get("/files")).json()["items"]
    assert next(item for item in regular if item["uid"] == uid)["starred"] is True
    scoped = (
        await client.get("/files", params={"scope": "starred"})
    ).json()["items"]
    assert [item["uid"] for item in scoped if item["uid"] == uid] == [uid]

    member_credentials = {
        "email": "star-route-reader@example.com",
        "password": "a secure reader password",
    }
    user = await client.post(
        "/users",
        json={**member_credentials, "role": "user"},
    )
    assert user.status_code == 201, user.text

    member = httpx.AsyncClient(
        transport=client._transport,
        base_url=str(client.base_url),
    )
    async with member:
        login = await member.post("/auth/sessions", json=member_credentials)
        assert login.status_code == 201, login.text
        denied = await member.patch(f"/files/{uid}", json={"starred": True})
        assert denied.status_code == 404
        member_scope = await member.get("/files", params={"scope": "starred"})
        assert all(
            item["uid"] != uid for item in member_scope.json()["items"]
        )

    unstarred = await client.patch(f"/files/{uid}", json={"starred": False})
    assert unstarred.status_code == 200, unstarred.text
    assert unstarred.json()["starred"] is False
    scoped_after = (
        await client.get("/files", params={"scope": "starred"})
    ).json()["items"]
    assert all(item["uid"] != uid for item in scoped_after)
