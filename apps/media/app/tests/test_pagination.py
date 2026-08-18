from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi_mongo_base.sql.models import BaseEntity

from apps.storage_objects.repository import StorageObjectRepository
from apps.storage_objects.services import StorageObjectService
from server.database import create_engine, create_session_factory
from tests.media_file_helpers import FakeConnection, Harness, build_harness

ACTOR_ID = "pagination-owner"
CONNECTION_ID = "pagination-connection"


@pytest_asyncio.fixture(loop_scope="function")
async def media_harness(tmp_path: Path) -> AsyncGenerator[Harness]:
    harness = await build_harness(tmp_path, FakeConnection(uid=CONNECTION_ID))
    yield harness
    await harness.engine.dispose()


@pytest_asyncio.fixture(loop_scope="function")
async def storage_service(
    tmp_path: Path,
) -> AsyncGenerator[tuple[StorageObjectService, StorageObjectRepository]]:
    engine = create_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'pagination-storage.sqlite3'}",
    )
    async with engine.begin() as connection:
        await connection.run_sync(BaseEntity.metadata.create_all)
    repository = StorageObjectRepository(create_session_factory(engine))
    yield StorageObjectService(repository), repository
    await engine.dispose()


def _object_data(name: str) -> dict[str, object]:
    return {
        "provider_connection_id": CONNECTION_ID,
        "content_reference": name,
        "provider_parent_ref": None,
        "type": "file",
        "name": name,
        "content_hash": None,
        "content_type": "text/plain",
        "size": 1,
        "metadata": {},
        "status": "active",
    }


@pytest.mark.asyncio
async def test_media_file_page_reports_total_and_offset_slice(
    media_harness: Harness,
) -> None:
    # Given three alphabetically ordered library children.
    for name in ("alpha", "bravo", "charlie"):
        await media_harness.service.create_folder(
            name=name,
            parent_id=None,
            owner_id=ACTOR_ID,
        )

    # When the second two-item page is requested.
    page = await media_harness.service.list_children(
        None,
        actor_user_id=ACTOR_ID,
        limit=2,
        offset=1,
    )

    # Then the envelope describes the full result set honestly.
    assert [record.name for record in page.items] == ["bravo", "charlie"]
    assert page.total == 3
    assert page.limit == 2
    assert page.offset == 1
    assert page.has_more is False


@pytest.mark.asyncio
async def test_media_file_search_pages_the_full_ranked_match_set(
    media_harness: Harness,
) -> None:
    # Given three visible fuzzy matches.
    for name in ("report-a", "report-b", "report-c"):
        await media_harness.service.create_folder(
            name=name,
            parent_id=None,
            owner_id=ACTOR_ID,
        )

    # When a one-item page is requested after the best match.
    page = await media_harness.service.search(
        "report",
        actor_user_id=ACTOR_ID,
        limit=1,
        offset=1,
    )

    # Then total counts the full ranked match set, not only the slice.
    assert [record.name for record in page.items] == ["report-b"]
    assert page.total == 3
    assert page.has_more is True


@pytest.mark.asyncio
async def test_storage_object_browse_page_uses_sql_order_and_total(
    storage_service: tuple[StorageObjectService, StorageObjectRepository],
) -> None:
    service, repository = storage_service
    # Given three indexed objects in one provider root.
    for name in ("alpha.txt", "bravo.txt", "charlie.txt"):
        await repository.create(_object_data(name))

    # When the first two-item page is requested.
    page = await service.list_objects(
        CONNECTION_ID,
        parent_ref=None,
        filter_by_parent=True,
        limit=2,
        offset=0,
    )

    # Then SQL ordering and the unsliced count are preserved.
    assert [record.name for record in page.items] == ["alpha.txt", "bravo.txt"]
    assert page.total == 3
    assert page.has_more is True


@pytest.mark.asyncio
async def test_storage_object_search_pages_ranked_matches(
    storage_service: tuple[StorageObjectService, StorageObjectRepository],
) -> None:
    service, repository = storage_service
    # Given three indexed fuzzy matches.
    for name in ("report-a.txt", "report-b.txt", "report-c.txt"):
        await repository.create(_object_data(name))

    # When a later page of ranked results is requested.
    page = await service.search(
        CONNECTION_ID,
        query="report",
        limit=1,
        offset=2,
    )

    # Then the item slice and total describe the same ranked set.
    assert [record.name for record in page.items] == ["report-c.txt"]
    assert page.total == 3
    assert page.has_more is False
