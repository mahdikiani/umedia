"""Per-file access control -- who may see or touch a `MediaFileRecord`.

Ported from `apps/resources/permissions.py` (the Resource ACL): one pure
module, no I/O, called by both `MediaFileService` and
`MediaFileRepository` so the two layers can never disagree about what
"can read" means. Stored integer levels are unchanged across the
Resource -> MediaFile migration, so migrated `permissions` JSON stays
meaningful.

Resolution order (docs/04-data-model.md, multi-user):
  1. `record.owner_id == user_id`            -> OWNER
  2. an entry in `record.permissions`        -> that entry's level
  3. (workspace claim -- see the commented hook in `effective_permission`)
  4. otherwise                               -> NONE

`public_permission` is deliberately *not* part of this ladder -- it only
gates the anonymous `/f/{uid}` share link
(`MediaFileService.get_via_public_link`), never `/files/*` visibility.
Admins get no implicit override either: an administrator sees other
users' files only when explicitly shared, same as anyone else.
"""

from enum import IntEnum
from typing import Protocol


class PermissionEnum(IntEnum):
    """Permission levels for media file access control."""

    NONE = 0
    READ = 10
    WRITE = 20
    MANAGE = 30
    DELETE = 40
    OWNER = 100


class _AccessControlled(Protocol):
    """The slice of `MediaFileRecord` these helpers read -- kept
    structural so `models.MediaFile` rows (same attribute names) satisfy
    it too."""

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
                # go through `MediaFileService.set_user_permission`, which
                # validates) fails closed rather than granting anything.
                return PermissionEnum.NONE
    # Workspace grants are schema-only for now: `workspace_id` is stored
    # but not yet consulted. When workspace claims land, this is the hook:
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
