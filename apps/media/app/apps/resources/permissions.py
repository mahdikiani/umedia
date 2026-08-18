"""Per-resource access control -- who may see or touch a `ResourceRecord`.

One pure module, no I/O: `ResourceService` and `ResourceRepository` both
call these helpers so the two layers can never disagree about what "can
read" means. The levels mirror `apps/files/schemas.py`'s `PermissionEnum`
(the pre-rebuild media app) so stored integers stay meaningful across the
migration.

Resolution order (docs/04-data-model.md, multi-user):
  1. `record.owner_id == user_id`            -> OWNER
  2. an entry in `record.permissions`        -> that entry's level
  3. (workspace claim -- see the commented hook in `effective_permission`)
  4. otherwise                               -> NONE

`public_permission` is deliberately *not* part of this ladder -- it only
gates the anonymous `/f/{uid}` share link (`ResourceService.
get_via_public_link`), never `/resources/*` visibility. Admins get no
implicit override either: an administrator sees other users' files only
when explicitly shared, same as anyone else.
"""

from enum import IntEnum
from typing import Protocol


class PermissionEnum(IntEnum):
    """Permission levels for resource access control."""

    NONE = 0
    READ = 10
    WRITE = 20
    MANAGE = 30
    DELETE = 40
    OWNER = 100


class _AccessControlled(Protocol):
    """The slice of `ResourceRecord` these helpers read -- kept structural
    so `models.Resource` rows (same attribute names) satisfy it too."""

    owner_id: str
    permissions: list[dict]
    workspace_id: str | None


def effective_permission(
    record: _AccessControlled,
    user_id: str | None,
    *,
    caller_workspace_ids: list[str] | None = None,
) -> PermissionEnum:
    """The strongest permission `user_id` holds on `record`."""
    if user_id is None:
        return PermissionEnum.NONE
    if record.owner_id == user_id:
        return PermissionEnum.OWNER
    for entry in record.permissions or []:
        if entry.get("user_id") == user_id:
            try:
                return PermissionEnum(int(entry.get("permission", 0)))
            except ValueError:
                # An unknown stored level (should be impossible -- writes
                # go through `ResourceService.set_user_permission`, which
                # validates) fails closed rather than granting anything.
                return PermissionEnum.NONE
    # Workspace grants are schema-only for now: `workspace_id` is stored
    # (models.py / migration 0004) but not yet consulted. When workspace
    # claims land, this is the hook:
    #
    # if (
    #     record.workspace_id is not None
    #     and caller_workspace_ids
    #     and record.workspace_id in caller_workspace_ids
    # ):
    #     return PermissionEnum.READ
    return PermissionEnum.NONE


def can_read(record: _AccessControlled, user_id: str | None) -> bool:
    return effective_permission(record, user_id) >= PermissionEnum.READ


def can_write(record: _AccessControlled, user_id: str | None) -> bool:
    return effective_permission(record, user_id) >= PermissionEnum.WRITE


def can_manage(record: _AccessControlled, user_id: str | None) -> bool:
    return effective_permission(record, user_id) >= PermissionEnum.MANAGE


def can_delete(record: _AccessControlled, user_id: str | None) -> bool:
    return effective_permission(record, user_id) >= PermissionEnum.DELETE


def filter_visible[R: _AccessControlled](
    records: list[R], user_id: str | None,
) -> list[R]:
    """Only the records `user_id` may see (READ or better)."""
    return [record for record in records if can_read(record, user_id)]
