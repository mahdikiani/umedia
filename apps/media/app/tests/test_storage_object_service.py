from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi_mongo_base.sql.models import BaseEntity

from apps.storage_objects.repository import StorageObjectRepository
from apps.storage_objects.services import StorageObjectService
from server.database import create_engine, create_session_factory


@pytest_asyncio.fixture
async def service(
    tmp_path: Path,
) -> AsyncGenerator[tuple[StorageObjectService, StorageObjectRepository]]:
    engine = create_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'storage-search.sqlite3'}",
    )
    async with engine.begin() as connection:
        await connection.run_sync(BaseEntity.metadata.create_all)
    repository = StorageObjectRepository(create_session_factory(engine))
    yield StorageObjectService(repository), repository
    await engine.dispose()


def _object_data(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "provider_connection_id": "provider-1",
        "content_reference": "docs/report.pdf",
        "provider_parent_ref": "docs",
        "type": "file",
        "name": "report.pdf",
        "content_hash": None,
        "content_type": "application/pdf",
        "size": 1,
        "metadata": {},
        "status": "active",
    }
    data.update(overrides)
    return data


@pytest.mark.asyncio
async def test_search_is_provider_scoped_subtree_scoped_and_best_first(
    service: tuple[StorageObjectService, StorageObjectRepository],
) -> None:
    storage_service, repository = service
    best = await repository.create(_object_data())
    nested = await repository.create(_object_data(
        content_reference="docs/archive/r-e-p-o-r-t.txt",
        provider_parent_ref="docs/archive",
        name="r-e-p-o-r-t.txt",
    ))
    outside = await repository.create(_object_data(
        content_reference="outside/report.pdf",
        provider_parent_ref="outside",
        name="report.pdf",
    ))
    other_provider = await repository.create(_object_data(
        provider_connection_id="provider-2",
        content_reference="docs/report.pdf",
    ))

    results = await storage_service.search(
        "provider-1", query="report", parent_ref="docs",
    )

    assert [row.uid for row in results.items] == [best.uid, nested.uid]
    assert outside.uid not in {row.uid for row in results.items}
    assert other_provider.uid not in {row.uid for row in results.items}
