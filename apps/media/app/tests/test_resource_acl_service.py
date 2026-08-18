"""Multi-user ACL tests for ResourceService -- written before the ACL
implementation itself, per the project's TDD process.

Covers docs/04-data-model.md's multi-user resolution ladder as enforced
at the *service* layer (the only place ACL logic is allowed to live --
never routes): owner isolation, explicit per-user shares, scope-filtered
listing, and the `/f/{uid}` public-link rules. Reuses the fakes from
test_resource_service.py so both files exercise the exact same repository
contract.
"""

import pytest

from apps.resources.errors import (
    ResourceNotFoundError,
    ResourcePermissionError,
    ResourceValidationError,
)
from apps.resources.permissions import (
    PermissionEnum,
    can_read,
    can_write,
    effective_permission,
    filter_visible,
)
from apps.resources.schemas import ResourceRecord
from apps.resources.services import ResourceService
from tests.test_resource_service import (
    CONNECTION_ID,
    FakePluginGateway,
    FakeResourceRepository,
)

OWNER = "user-owner"
OTHER = "user-other"


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


async def _create(service: ResourceService, **overrides: object) -> ResourceRecord:
    defaults: dict = {
        "provider_connection_id": CONNECTION_ID,
        "parent_id": None,
        "name": "file.txt",
        "type_": "file",
        "owner_id": OWNER,
        "content": b"some bytes",
    }
    defaults.update(overrides)
    return await service.create(**defaults)


# ----------------------------------------------------------------------
# Pure permission helpers
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_effective_permission_ladder(service: ResourceService) -> None:
    record = await _create(service)

    assert effective_permission(record, OWNER) == PermissionEnum.OWNER
    assert effective_permission(record, OTHER) == PermissionEnum.NONE
    assert effective_permission(record, None) == PermissionEnum.NONE

    record.permissions = [{"user_id": OTHER, "permission": 20}]
    assert effective_permission(record, OTHER) == PermissionEnum.WRITE
    assert can_read(record, OTHER)
    assert can_write(record, OTHER)

    assert filter_visible([record], OTHER) == [record]
    assert filter_visible([record], "user-stranger") == []


# ----------------------------------------------------------------------
# Owner isolation
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_sets_the_owner(service: ResourceService) -> None:
    record = await _create(service)

    assert record.owner_id == OWNER
    assert record.permissions == []
    assert record.workspace_id is None


@pytest.mark.asyncio
async def test_get_is_not_found_for_a_non_owner_even_an_admin(
    service: ResourceService,
) -> None:
    """404, not 403 -- and no admin override: nobody sees an unshared
    resource but its owner."""
    record = await _create(service)

    assert (await service.get(record.uid, actor_user_id=OWNER)).uid == record.uid
    with pytest.raises(ResourceNotFoundError):
        await service.get(record.uid, actor_user_id=OTHER)


@pytest.mark.asyncio
async def test_owned_listing_only_shows_the_actors_resources(
    service: ResourceService,
) -> None:
    mine = await _create(service, name="mine.txt", content=b"mine")
    await _create(service, name="theirs.txt", owner_id=OTHER, content=b"theirs")

    owned = await service.list_children(None, actor_user_id=OWNER)
    assert [r.uid for r in owned] == [mine.uid]

    assert await service.list_children(None, actor_user_id="user-stranger") == []


@pytest.mark.asyncio
async def test_read_content_requires_read_access(service: ResourceService) -> None:
    record = await _create(service)

    with pytest.raises(ResourceNotFoundError):
        await service.read_content(record.uid, actor_user_id=OTHER)


@pytest.mark.asyncio
async def test_identical_content_is_not_deduplicated_across_owners(
    service: ResourceService,
) -> None:
    """Dedup returning another user's row would leak its existence and
    grant access the ACL never did."""
    first = await _create(service, name="a.txt", content=b"same bytes")
    second = await _create(
        service, name="b.txt", owner_id=OTHER, content=b"same bytes",
    )

    assert second.uid != first.uid
    assert second.owner_id == OTHER


# ----------------------------------------------------------------------
# Sharing
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_share_read_lets_the_target_get_but_not_update(
    service: ResourceService,
) -> None:
    record = await _create(service)

    shared = await service.set_user_permission(
        record.uid,
        actor_user_id=OWNER,
        target_user_id=OTHER,
        permission=int(PermissionEnum.READ),
    )
    assert shared.permissions == [
        {"user_id": OTHER, "permission": int(PermissionEnum.READ)},
    ]

    fetched = await service.get(record.uid, actor_user_id=OTHER)
    assert fetched.uid == record.uid

    with pytest.raises(ResourcePermissionError):
        await service.update(record.uid, name="renamed.txt", actor_user_id=OTHER)


@pytest.mark.asyncio
async def test_share_write_allows_update_but_not_delete_or_sharing(
    service: ResourceService,
) -> None:
    record = await _create(service)
    await service.set_user_permission(
        record.uid,
        actor_user_id=OWNER,
        target_user_id=OTHER,
        permission=int(PermissionEnum.WRITE),
    )

    renamed = await service.update(
        record.uid, name="renamed.txt", actor_user_id=OTHER,
    )
    assert renamed.name == "renamed.txt"

    with pytest.raises(ResourcePermissionError):
        await service.soft_delete(record.uid, actor_user_id=OTHER)
    with pytest.raises(ResourcePermissionError):
        await service.set_user_permission(
            record.uid,
            actor_user_id=OTHER,
            target_user_id="user-third",
            permission=int(PermissionEnum.READ),
        )
    with pytest.raises(ResourcePermissionError):
        await service.set_public_permission(
            record.uid, "read", actor_user_id=OTHER,
        )
    with pytest.raises(ResourcePermissionError):
        await service.list_permissions(record.uid, actor_user_id=OTHER)


@pytest.mark.asyncio
async def test_share_delete_allows_soft_delete(service: ResourceService) -> None:
    record = await _create(service)
    await service.set_user_permission(
        record.uid,
        actor_user_id=OWNER,
        target_user_id=OTHER,
        permission=int(PermissionEnum.DELETE),
    )

    await service.soft_delete(record.uid, actor_user_id=OTHER)

    with pytest.raises(ResourceNotFoundError):
        await service.get(record.uid, actor_user_id=OWNER)


@pytest.mark.asyncio
async def test_permission_zero_removes_the_entry(service: ResourceService) -> None:
    record = await _create(service)
    await service.set_user_permission(
        record.uid,
        actor_user_id=OWNER,
        target_user_id=OTHER,
        permission=int(PermissionEnum.READ),
    )

    revoked = await service.set_user_permission(
        record.uid, actor_user_id=OWNER, target_user_id=OTHER, permission=0,
    )

    assert revoked.permissions == []
    with pytest.raises(ResourceNotFoundError):
        await service.get(record.uid, actor_user_id=OTHER)


@pytest.mark.asyncio
async def test_regranting_replaces_rather_than_duplicates_the_entry(
    service: ResourceService,
) -> None:
    record = await _create(service)
    for level in (PermissionEnum.READ, PermissionEnum.WRITE):
        updated = await service.set_user_permission(
            record.uid,
            actor_user_id=OWNER,
            target_user_id=OTHER,
            permission=int(level),
        )

    assert updated.permissions == [
        {"user_id": OTHER, "permission": int(PermissionEnum.WRITE)},
    ]


@pytest.mark.asyncio
async def test_set_user_permission_rejects_invalid_levels_and_the_owner(
    service: ResourceService,
) -> None:
    record = await _create(service)

    with pytest.raises(ResourceValidationError):
        await service.set_user_permission(
            record.uid, actor_user_id=OWNER, target_user_id=OTHER, permission=15,
        )
    with pytest.raises(ResourceValidationError):
        await service.set_user_permission(
            record.uid, actor_user_id=OWNER, target_user_id=OWNER, permission=10,
        )


@pytest.mark.asyncio
async def test_list_permissions_shows_grants_to_a_manager(
    service: ResourceService,
) -> None:
    record = await _create(service)
    await service.set_user_permission(
        record.uid,
        actor_user_id=OWNER,
        target_user_id=OTHER,
        permission=int(PermissionEnum.READ),
    )

    entries = await service.list_permissions(record.uid, actor_user_id=OWNER)

    assert entries == [{"user_id": OTHER, "permission": int(PermissionEnum.READ)}]


# ----------------------------------------------------------------------
# Scoped listing
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shared_with_me_lists_only_resources_shared_to_the_actor(
    service: ResourceService,
) -> None:
    shared = await _create(service, name="shared.txt", content=b"shared")
    await _create(service, name="private.txt", content=b"private")
    await service.set_user_permission(
        shared.uid,
        actor_user_id=OWNER,
        target_user_id=OTHER,
        permission=int(PermissionEnum.READ),
    )

    listed = await service.list_children(
        None, actor_user_id=OTHER, scope="shared_with_me",
    )

    assert [r.uid for r in listed] == [shared.uid]


@pytest.mark.asyncio
async def test_shared_by_me_lists_the_owners_outgoing_shares(
    service: ResourceService,
) -> None:
    shared = await _create(service, name="shared.txt", content=b"shared")
    await _create(service, name="private.txt", content=b"private")
    await service.set_user_permission(
        shared.uid,
        actor_user_id=OWNER,
        target_user_id=OTHER,
        permission=int(PermissionEnum.READ),
    )

    listed = await service.list_children(
        None, actor_user_id=OWNER, scope="shared_by_me",
    )

    assert [r.uid for r in listed] == [shared.uid]


@pytest.mark.asyncio
async def test_all_visible_combines_owned_and_shared_in_a_parent(
    service: ResourceService,
) -> None:
    mine = await _create(service, name="mine.txt", content=b"mine")
    shared = await _create(
        service, name="shared.txt", owner_id=OTHER, content=b"shared",
    )
    await _create(service, name="hidden.txt", owner_id=OTHER, content=b"hidden")
    await service.set_user_permission(
        shared.uid,
        actor_user_id=OTHER,
        target_user_id=OWNER,
        permission=int(PermissionEnum.READ),
    )

    listed = await service.list_children(
        None, actor_user_id=OWNER, scope="all_visible",
    )

    assert {r.uid for r in listed} == {mine.uid, shared.uid}


@pytest.mark.asyncio
async def test_list_children_rejects_an_unknown_scope(
    service: ResourceService,
) -> None:
    with pytest.raises(ResourceValidationError):
        await service.list_children(None, actor_user_id=OWNER, scope="everything")


@pytest.mark.asyncio
async def test_include_deleted_is_owner_scoped_trash_only(
    service: ResourceService,
) -> None:
    record = await _create(service)
    await service.soft_delete(record.uid, actor_user_id=OWNER)

    trash = await service.list_children(
        None, actor_user_id=OWNER, include_deleted=True,
    )
    assert [r.uid for r in trash] == [record.uid]

    assert await service.list_children(
        None, actor_user_id=OTHER, include_deleted=True,
    ) == []
    with pytest.raises(ResourceValidationError):
        await service.list_children(
            None, actor_user_id=OWNER, scope="all_visible", include_deleted=True,
        )


# ----------------------------------------------------------------------
# Public link (`/f/{uid}`) rules at the service level
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_link_visibility(service: ResourceService) -> None:
    record = await _create(service)

    # Not public: anonymous and unrelated users get 404; the owner and a
    # user with an ACL grant still resolve it.
    with pytest.raises(ResourceNotFoundError):
        await service.get_via_public_link(record.uid, actor_user_id=None)
    with pytest.raises(ResourceNotFoundError):
        await service.get_via_public_link(record.uid, actor_user_id=OTHER)
    assert (
        await service.get_via_public_link(record.uid, actor_user_id=OWNER)
    ).uid == record.uid

    await service.set_user_permission(
        record.uid,
        actor_user_id=OWNER,
        target_user_id=OTHER,
        permission=int(PermissionEnum.READ),
    )
    assert (
        await service.get_via_public_link(record.uid, actor_user_id=OTHER)
    ).uid == record.uid

    # Public: anonymous access opens up -- unchanged single-admin-era
    # behavior for share links.
    await service.set_public_permission(record.uid, "read", actor_user_id=OWNER)
    assert (
        await service.get_via_public_link(record.uid, actor_user_id=None)
    ).uid == record.uid


@pytest.mark.asyncio
async def test_public_permission_does_not_leak_into_private_routes(
    service: ResourceService,
) -> None:
    """Rule 4: `public_permission` gates only `/f/{uid}` -- a public file
    still doesn't show up in another user's `/resources` world."""
    record = await _create(service)
    await service.set_public_permission(record.uid, "read", actor_user_id=OWNER)

    with pytest.raises(ResourceNotFoundError):
        await service.get(record.uid, actor_user_id=OTHER)
    assert await service.list_children(
        None, actor_user_id=OTHER, scope="all_visible",
    ) == []
