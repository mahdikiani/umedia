"""Real-SQLite integration tests for ProviderConnectionRepository.

Unlike apps/provider_connections/services.py's unit tests (fakes only),
this exercises the actual SQL layer -- the ORM mapping matches what
Alembic's migration creates (docs/09-tasks.md P1.9).
"""

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi_mongo_base.sql.models import BaseEntity
from sqlalchemy.ext.asyncio import async_sessionmaker

from apps.provider_connections.models import ProviderConnection
from apps.provider_connections.repository import ProviderConnectionRepository
from server.database import create_engine, create_session_factory


def _data(**overrides: object) -> dict:
    payload: dict = {
        "owner_id": "user-1",
        "provider_type": "local",
        "name": "Library",
        "encrypted_config": "cipher-text",
        "status": "configured",
    }
    payload.update(overrides)
    return payload


@pytest_asyncio.fixture
async def repository(
    tmp_path: Path,
) -> AsyncGenerator[ProviderConnectionRepository]:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'provider-test.sqlite3'}")
    async with engine.begin() as connection:
        await connection.run_sync(BaseEntity.metadata.create_all)
    session_factory: async_sessionmaker = create_session_factory(engine)
    yield ProviderConnectionRepository(session_factory)
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_then_list_and_get(
    repository: ProviderConnectionRepository,
) -> None:
    created = await repository.create(_data())

    listed = await repository.list()
    assert [item.uid for item in listed] == [created.uid]
    assert created.owner_id == "user-1"

    fetched = await repository.get(created.uid)
    assert fetched is not None
    assert fetched.name == "Library"
    assert fetched.owner_id == "user-1"


@pytest.mark.asyncio
async def test_list_filters_by_owner(
    repository: ProviderConnectionRepository,
) -> None:
    mine = await repository.create(_data(name="Mine", owner_id="user-a"))
    await repository.create(_data(name="Theirs", owner_id="user-b"))

    listed = await repository.list(owner_id="user-a")
    assert [item.uid for item in listed] == [mine.uid]


@pytest.mark.asyncio
async def test_get_update_delete_are_ownership_aware(
    repository: ProviderConnectionRepository,
) -> None:
    created = await repository.create(_data(owner_id="user-a"))

    assert await repository.get(created.uid, owner_id="user-b") is None
    assert await repository.update(
        created.uid, {"name": "Nope"}, owner_id="user-b",
    ) is None
    assert await repository.delete(created.uid, owner_id="user-b") is False

    updated = await repository.update(
        created.uid, {"name": "Renamed"}, owner_id="user-a",
    )
    assert updated is not None
    assert updated.name == "Renamed"
    assert await repository.delete(created.uid, owner_id="user-a") is True
    assert await repository.get(created.uid) is None


@pytest.mark.asyncio
async def test_owner_id_column_is_required_on_model() -> None:
    column = ProviderConnection.__table__.c.owner_id
    assert column.nullable is False


@pytest.mark.asyncio
async def test_get_missing_returns_none(
    repository: ProviderConnectionRepository,
) -> None:
    assert await repository.get("does-not-exist") is None


@pytest.mark.asyncio
async def test_delete_is_soft_and_excludes_from_list_and_get(
    repository: ProviderConnectionRepository,
) -> None:
    created = await repository.create(_data(provider_type="s3", name="Backblaze"))

    assert await repository.delete(created.uid) is True
    assert await repository.get(created.uid) is None
    assert await repository.list() == []


@pytest.mark.asyncio
async def test_delete_missing_returns_false(
    repository: ProviderConnectionRepository,
) -> None:
    assert await repository.delete("does-not-exist") is False


@pytest.mark.asyncio
async def test_created_connections_default_to_enabled(
    repository: ProviderConnectionRepository,
) -> None:
    created = await repository.create(_data())

    assert created.enabled is True


@pytest.mark.asyncio
async def test_update_changes_name_and_enabled(
    repository: ProviderConnectionRepository,
) -> None:
    created = await repository.create(_data())

    updated = await repository.update(
        created.uid, {"name": "Renamed", "enabled": False},
    )

    assert updated is not None
    assert updated.name == "Renamed"
    assert updated.enabled is False
    fetched = await repository.get(created.uid)
    assert fetched.name == "Renamed"
    assert fetched.enabled is False


@pytest.mark.asyncio
async def test_update_missing_connection_returns_none(
    repository: ProviderConnectionRepository,
) -> None:
    assert await repository.update("does-not-exist", {"name": "X"}) is None


@pytest.mark.asyncio
async def test_multiple_connections_of_the_same_provider_type_are_allowed(
    repository: ProviderConnectionRepository,
) -> None:
    """Multi-instance is the whole point -- two S3 connections must coexist."""
    first = await repository.create(_data(
        provider_type="s3",
        name="AWS Personal",
        encrypted_config="cipher-text-1",
    ))
    second = await repository.create(_data(
        provider_type="s3",
        name="Backblaze Backup",
        encrypted_config="cipher-text-2",
    ))

    listed_ids = {item.uid for item in await repository.list()}
    assert listed_ids == {first.uid, second.uid}
