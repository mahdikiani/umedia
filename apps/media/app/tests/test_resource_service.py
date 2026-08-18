"""Tests for ResourceService against fake repository/plugin-gateway --
written before apps/resources/models.py or repository.py exist (P4.2 is
next), per docs/08-implementation-plan.md's TDD process.
"""

import copy
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest

from apps.resources.errors import (
    ResourceNotFoundError,
    ResourceStateError,
    ResourceValidationError,
    ResourceWriteFailedError,
)
from apps.resources.permissions import can_read
from apps.resources.schemas import ResourceRecord
from apps.resources.services import ResourceService
from plugins.contracts import CreateResourceIn, UpdateResourceIn
from plugins.contracts import Resource as PluginResource

CONNECTION_ID = "connection-1"
OWNER_ID = "user-1"


class FakeResourceRepository:
    """In-memory stand-in for the real SQL repository (P4.2).

    Returns copies, not the stored object, from every read/write --
    mirroring the real repository (each method opens its own session, so
    two calls never hand back the same Python object). A caller holding an
    older reference must not see it mutate out from under it just because
    the fake happens to keep everything in one dict.
    """

    def __init__(self) -> None:
        self._rows: dict[str, ResourceRecord] = {}

    @staticmethod
    def _copy(record: ResourceRecord) -> ResourceRecord:
        return copy.deepcopy(record)

    async def create(self, data: dict[str, Any]) -> ResourceRecord:
        now = datetime.now()
        record = ResourceRecord(
            uid=str(uuid.uuid4()),
            metadata={},
            history=[],
            access_at=now,
            deleted_at=None,
            is_deleted=False,
            created_at=now,
            updated_at=now,
            **data,
        )
        self._rows[record.uid] = record
        return self._copy(record)

    async def get(self, uid: str) -> ResourceRecord | None:
        row = self._rows.get(uid)
        return None if row is None else self._copy(row)

    async def list(
        self, *, parent_id: str | None, include_deleted: bool = False,
    ) -> list[ResourceRecord]:
        return [
            self._copy(row) for row in self._rows.values()
            if row.parent_id == parent_id and (include_deleted or not row.is_deleted)
        ]

    async def find_by_content_hash(
        self,
        *,
        provider_connection_id: str,
        parent_id: str | None,
        content_hash: str,
        owner_id: str,
    ) -> ResourceRecord | None:
        for row in self._rows.values():
            if (
                not row.is_deleted
                and row.provider_connection_id == provider_connection_id
                and row.parent_id == parent_id
                and row.content_hash == content_hash
                and row.owner_id == owner_id
            ):
                return self._copy(row)
        return None

    async def list_for_actor(
        self,
        *,
        actor_user_id: str,
        parent_id: str | None = None,
        scope: str = "owned",
        include_deleted: bool = False,
        # Quoted: in this class body the name `list` is the `list()`
        # *method* above, not the builtin.
    ) -> "list[ResourceRecord]":
        rows = [*self._rows.values()]
        if scope == "owned":
            matches = [
                row for row in rows
                if row.owner_id == actor_user_id
                and row.parent_id == parent_id
                and (include_deleted or not row.is_deleted)
            ]
        elif scope == "shared_with_me":
            matches = [
                row for row in rows
                if not row.is_deleted
                and row.owner_id != actor_user_id
                and can_read(row, actor_user_id)
            ]
        elif scope == "shared_by_me":
            matches = [
                row for row in rows
                if not row.is_deleted
                and row.owner_id == actor_user_id
                and row.permissions
            ]
        else:  # all_visible
            matches = [
                row for row in rows
                if not row.is_deleted
                and row.parent_id == parent_id
                and can_read(row, actor_user_id)
            ]
        return [self._copy(row) for row in matches]

    async def update(self, uid: str, changes: dict[str, Any]) -> ResourceRecord:
        row = self._rows[uid]
        for key, value in changes.items():
            setattr(row, key, value)
        row.updated_at = datetime.now()
        return self._copy(row)

    async def touch_access(self, uid: str) -> None:
        self._rows[uid].access_at = datetime.now()

    async def soft_delete(self, uid: str) -> None:
        row = self._rows[uid]
        row.is_deleted = True
        row.deleted_at = datetime.now()

    async def hard_delete(self, uid: str) -> None:
        del self._rows[uid]

    async def restore(self, uid: str) -> None:
        row = self._rows[uid]
        row.is_deleted = False
        row.deleted_at = None

    async def volume_stats(self) -> dict[str, int]:
        active = [r for r in self._rows.values() if not r.is_deleted]
        deleted = [r for r in self._rows.values() if r.is_deleted]
        return {
            "active_size": sum(r.size for r in active),
            "active_count": len(active),
            "deleted_size": sum(r.size for r in deleted),
            "deleted_count": len(deleted),
        }


class FakePluginGateway:
    """Controllable fake for the plugin-calling side -- can be told to
    fail create/update/delete to exercise the failure lifecycle."""

    def __init__(self) -> None:
        self.fail_create = False
        self.fail_update = False
        self.fail_verify = False
        self.deleted: list[str] = []
        self._next_id = 1
        self._store: dict[str, bytes] = {}

    async def create_resource(
        self, _provider_connection_id: str, metadata: CreateResourceIn, content: bytes,
    ) -> PluginResource:
        if self.fail_create:
            raise RuntimeError("simulated plugin crash on create")
        resource_id = str(self._next_id)
        self._next_id += 1
        self._store[resource_id] = content
        return PluginResource(
            id=resource_id,
            type=metadata.type,
            name=metadata.name,
            parent_id=metadata.parent_id,
            size=len(content),
            content_type="application/octet-stream",
        )

    async def get_resource(
        self, _provider_connection_id: str, content_reference: str,
    ) -> PluginResource:
        if self.fail_verify:
            raise RuntimeError("simulated verification timeout")
        return PluginResource(
            id=content_reference,
            type="file",
            name="whatever",
            size=len(self._store.get(content_reference, b"")),
        )

    async def update_resource(
        self,
        _provider_connection_id: str,
        content_reference: str,
        changes: UpdateResourceIn,
        content: bytes | None,
    ) -> PluginResource:
        if self.fail_update:
            raise RuntimeError("simulated plugin crash on update")
        new_id = content_reference
        if content is not None:
            new_id = f"{content_reference}-v2"
            self._store[new_id] = content
        return PluginResource(
            id=new_id,
            type="file",
            name=changes.name or "whatever",
            size=len(content) if content is not None else len(
                self._store.get(content_reference, b""),
            ),
        )

    async def delete_resource(
        self, _provider_connection_id: str, content_reference: str,
    ) -> None:
        self.deleted.append(content_reference)
        self._store.pop(content_reference, None)

    async def read_content(
        self,
        _provider_connection_id: str,
        content_reference: str,
        *,
        range_header: str | None,
    ) -> AsyncIterator[bytes]:
        # An async *generator* function (has `yield`, no `return` of a
        # value) -- calling it returns the iterator directly, matching
        # the protocol's calling convention (`stream = self._plugins.
        # read_content(...)`, no `await`). Writing this with an inner
        # helper + `return _iter()` instead would make the outer call a
        # plain coroutine needing `await` first -- caught by this file's
        # own test failing with "'async for' ... got coroutine".
        yield self._store.get(content_reference, b"")


@pytest.fixture
def repository() -> FakeResourceRepository:
    return FakeResourceRepository()


@pytest.fixture
def plugins() -> FakePluginGateway:
    return FakePluginGateway()


@pytest.fixture
def service(
    repository: FakeResourceRepository, plugins: FakePluginGateway,
) -> ResourceService:
    return ResourceService(repository, plugins)


@pytest.mark.asyncio
async def test_create_completes_and_stores_the_plugins_content_reference(
    service: ResourceService,
) -> None:
    created = await service.create(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="hello.txt",
        type_="file",
        owner_id=OWNER_ID,
        content=b"hello",
    )

    assert created.status == "completed"
    assert created.content_reference == "1"
    assert created.size == 5


@pytest.mark.asyncio
async def test_create_deduplicates_identical_content_in_the_same_parent(
    service: ResourceService,
) -> None:
    first = await service.create(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="a.txt",
        type_="file",
        owner_id=OWNER_ID,
        content=b"same bytes",
    )
    second = await service.create(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="b.txt",  # different name, same content+parent+connection
        type_="file",
        owner_id=OWNER_ID,
        content=b"same bytes",
    )

    assert second.uid == first.uid
    assert second.name == "a.txt"  # the original, not overwritten


@pytest.mark.asyncio
async def test_create_does_not_deduplicate_across_different_parents(
    service: ResourceService,
) -> None:
    folder = await service.create(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="folder",
        type_="folder",
        owner_id=OWNER_ID,
        content=b"",
    )
    first = await service.create(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="a.txt",
        type_="file",
        owner_id=OWNER_ID,
        content=b"same",
    )
    second = await service.create(
        provider_connection_id=CONNECTION_ID,
        parent_id=folder.uid,
        name="a.txt",
        type_="file",
        owner_id=OWNER_ID,
        content=b"same",
    )

    assert second.uid != first.uid


@pytest.mark.asyncio
async def test_create_rejects_a_parent_from_a_different_connection(
    service: ResourceService,
) -> None:
    folder = await service.create(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="folder",
        type_="folder",
        owner_id=OWNER_ID,
        content=b"",
    )

    with pytest.raises(ResourceValidationError):
        await service.create(
            provider_connection_id="a-different-connection",
            parent_id=folder.uid,
            name="a.txt",
            type_="file",
            owner_id=OWNER_ID,
            content=b"x",
        )


@pytest.mark.asyncio
async def test_update_move_rejects_a_parent_from_a_different_connection(
    service: ResourceService,
) -> None:
    file_ = await service.create(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="a.txt",
        type_="file",
        owner_id=OWNER_ID,
        content=b"x",
    )
    foreign_folder = await service.create(
        provider_connection_id="a-different-connection",
        parent_id=None,
        name="foreign-folder",
        type_="folder",
        owner_id=OWNER_ID,
        content=b"",
    )

    with pytest.raises(ResourceValidationError):
        await service.update(
            file_.uid, parent_id=foreign_folder.uid, actor_user_id=OWNER_ID,
        )


@pytest.mark.asyncio
async def test_create_failure_leaves_the_row_failed_not_processing_or_lost(
    service: ResourceService,
    repository: FakeResourceRepository,
    plugins: FakePluginGateway,
) -> None:
    plugins.fail_create = True

    with pytest.raises(ResourceWriteFailedError):
        await service.create(
            provider_connection_id=CONNECTION_ID,
            parent_id=None,
            name="doomed.txt",
            type_="file",
            owner_id=OWNER_ID,
            content=b"x",
        )

    # The row exists (not silently lost) and is in a terminal, truthful
    # state -- never left stuck in "processing".
    rows = list(repository._rows.values())
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert rows[0].error is not None


@pytest.mark.asyncio
async def test_create_verification_failure_also_marks_failed(
    service: ResourceService, plugins: FakePluginGateway,
) -> None:
    plugins.fail_verify = True

    with pytest.raises(ResourceWriteFailedError):
        await service.create(
            provider_connection_id=CONNECTION_ID,
            parent_id=None,
            name="unverifiable.txt",
            type_="file",
            owner_id=OWNER_ID,
            content=b"x",
        )


@pytest.mark.asyncio
async def test_update_rename_keeps_the_same_content_reference_and_no_history(
    service: ResourceService,
) -> None:
    created = await service.create(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="old.txt",
        type_="file",
        owner_id=OWNER_ID,
        content=b"content",
    )

    renamed = await service.update(
        created.uid, name="new.txt", actor_user_id=OWNER_ID,
    )

    assert renamed.name == "new.txt"
    assert renamed.content_reference == created.content_reference
    assert renamed.history == []


@pytest.mark.asyncio
async def test_update_with_new_content_keeps_history_and_gets_a_new_reference(
    service: ResourceService,
) -> None:
    created = await service.create(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="a.txt",
        type_="file",
        owner_id=OWNER_ID,
        content=b"v1",
    )

    updated = await service.update(
        created.uid, content=b"v2 longer", actor_user_id=OWNER_ID,
    )

    assert updated.content_reference != created.content_reference
    assert updated.size == len(b"v2 longer")
    assert len(updated.history) == 1
    assert updated.history[0].content_reference == created.content_reference
    assert updated.history[0].size == created.size


@pytest.mark.asyncio
async def test_update_failure_marks_failed_and_does_not_touch_history(
    service: ResourceService, plugins: FakePluginGateway,
) -> None:
    created = await service.create(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="a.txt",
        type_="file",
        owner_id=OWNER_ID,
        content=b"v1",
    )
    plugins.fail_update = True

    with pytest.raises(ResourceWriteFailedError):
        await service.update(created.uid, content=b"v2", actor_user_id=OWNER_ID)

    failed = await service.get(created.uid, actor_user_id=OWNER_ID)
    assert failed.status == "failed"
    assert failed.history == []


@pytest.mark.asyncio
async def test_get_raises_not_found_for_a_missing_or_deleted_resource(
    service: ResourceService,
) -> None:
    with pytest.raises(ResourceNotFoundError):
        await service.get("does-not-exist", actor_user_id=OWNER_ID)


@pytest.mark.asyncio
async def test_soft_delete_then_get_reports_not_found(
    service: ResourceService,
) -> None:
    created = await service.create(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="a.txt",
        type_="file",
        owner_id=OWNER_ID,
        content=b"x",
    )

    await service.soft_delete(created.uid, actor_user_id=OWNER_ID)

    with pytest.raises(ResourceNotFoundError):
        await service.get(created.uid, actor_user_id=OWNER_ID)


@pytest.mark.asyncio
async def test_soft_delete_cascades_into_folder_children(
    service: ResourceService, repository: FakeResourceRepository,
) -> None:
    folder = await service.create(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="folder",
        type_="folder",
        owner_id=OWNER_ID,
        content=b"",
    )
    child = await service.create(
        provider_connection_id=CONNECTION_ID,
        parent_id=folder.uid,
        name="child.txt",
        type_="file",
        owner_id=OWNER_ID,
        content=b"x",
    )

    await service.soft_delete(folder.uid, actor_user_id=OWNER_ID)

    assert (await repository.get(child.uid)).is_deleted is True


@pytest.mark.asyncio
async def test_hard_delete_requires_soft_delete_first(
    service: ResourceService,
) -> None:
    created = await service.create(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="a.txt",
        type_="file",
        owner_id=OWNER_ID,
        content=b"x",
    )

    with pytest.raises(ResourceStateError):
        await service.hard_delete(created.uid, actor_user_id=OWNER_ID)


@pytest.mark.asyncio
async def test_hard_delete_removes_the_row_and_tells_the_plugin_to_delete(
    service: ResourceService,
    repository: FakeResourceRepository,
    plugins: FakePluginGateway,
) -> None:
    created = await service.create(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="a.txt",
        type_="file",
        owner_id=OWNER_ID,
        content=b"x",
    )
    await service.soft_delete(created.uid, actor_user_id=OWNER_ID)

    await service.hard_delete(created.uid, actor_user_id=OWNER_ID)

    assert await repository.get(created.uid) is None
    assert plugins.deleted == [created.content_reference]


@pytest.mark.asyncio
async def test_restore_undoes_a_soft_delete_and_cascades(
    service: ResourceService, repository: FakeResourceRepository,
) -> None:
    folder = await service.create(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="folder",
        type_="folder",
        owner_id=OWNER_ID,
        content=b"",
    )
    child = await service.create(
        provider_connection_id=CONNECTION_ID,
        parent_id=folder.uid,
        name="child.txt",
        type_="file",
        owner_id=OWNER_ID,
        content=b"x",
    )
    await service.soft_delete(folder.uid, actor_user_id=OWNER_ID)

    await service.restore(folder.uid, actor_user_id=OWNER_ID)

    assert (await repository.get(folder.uid)).is_deleted is False
    assert (await repository.get(child.uid)).is_deleted is False


@pytest.mark.asyncio
async def test_read_content_streams_bytes_and_touches_access(
    service: ResourceService, repository: FakeResourceRepository,
) -> None:
    created = await service.create(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="a.txt",
        type_="file",
        owner_id=OWNER_ID,
        content=b"streamed content",
    )
    before = (await repository.get(created.uid)).access_at

    resource, stream = await service.read_content(
        created.uid, actor_user_id=OWNER_ID,
    )
    body = b"".join([chunk async for chunk in stream])

    assert body == b"streamed content"
    assert resource.uid == created.uid
    assert (await repository.get(created.uid)).access_at >= before


@pytest.mark.asyncio
async def test_set_public_permission_updates_the_row(
    service: ResourceService,
) -> None:
    created = await service.create(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="a.txt",
        type_="file",
        owner_id=OWNER_ID,
        content=b"x",
    )
    assert created.public_permission == "none"

    updated = await service.set_public_permission(
        created.uid, "read", actor_user_id=OWNER_ID,
    )

    assert updated.public_permission == "read"


@pytest.mark.asyncio
async def test_set_public_permission_rejects_an_unknown_value(
    service: ResourceService,
) -> None:
    created = await service.create(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="a.txt",
        type_="file",
        owner_id=OWNER_ID,
        content=b"x",
    )

    with pytest.raises(ResourceValidationError):
        await service.set_public_permission(
            created.uid, "public", actor_user_id=OWNER_ID,
        )


@pytest.mark.asyncio
async def test_volume_stats_reflects_active_and_deleted_resources(
    service: ResourceService,
) -> None:
    kept = await service.create(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="kept.txt",
        type_="file",
        owner_id=OWNER_ID,
        content=b"12345",
    )
    removed = await service.create(
        provider_connection_id=CONNECTION_ID,
        parent_id=None,
        name="removed.txt",
        type_="file",
        owner_id=OWNER_ID,
        content=b"1234567890",
    )
    await service.soft_delete(removed.uid, actor_user_id=OWNER_ID)

    stats = await service.volume_stats()

    assert stats["active_count"] == 1
    assert stats["active_size"] == 5
    assert stats["deleted_count"] == 1
    assert stats["deleted_size"] == 10
    assert kept.status == "completed"
