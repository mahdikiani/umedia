"""Per-file access control -- who may see or open a `MediaFileRecord`.

Ported from `apps/resources/permissions.py` (the Resource ACL): one pure
module, no I/O, called by both `MediaFileService` and
`MediaFileRepository` so the two layers can never disagree about what
"can read" means. Stored integer levels are unchanged across the
Resource -> MediaFile migration, so migrated `permissions` JSON stays
meaningful.

Library visibility ladder (`effective_permission` / `can_read`):
  1. `record.owner_id == user_id`                 -> OWNER
  2. an entry in `record.permissions`             -> that entry's level
  3. caller's workspace matches `record.workspace_id` -> READ
  4. otherwise                                    -> NONE

Content-URL open gate (`can_open_content`) -- used by
`GET /files/{uid}/content` and `GET /f/{uid}`:
  1. library visibility (owner / share / workspace) at READ+
  2. permanent public (`public_permission == "read"`)
  3. short-lived SigV4 links are *not* decided here -- they hit `/s3/...`
  4. otherwise deny; callers must answer **404** (never 401/403 that
     confirm the uid exists)

Admins get no implicit override: an administrator sees other users'
files only when explicitly shared, same as anyone else.
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
    public_permission: str


def effective_permission(
    record: _AccessControlled,
    user_id: str | None,
    *,
    caller_workspace_ids: list[str] | None = None,
) -> PermissionEnum:
    """The strongest library permission `user_id` holds on `record`."""
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
    if (
        record.workspace_id is not None
        and caller_workspace_ids
        and record.workspace_id in caller_workspace_ids
    ):
        return PermissionEnum.READ
    return PermissionEnum.NONE


def can_read(
    record: _AccessControlled,
    user_id: str | None,
    *,
    caller_workspace_ids: list[str] | None = None,
) -> bool:
    return (
        effective_permission(
            record,
            user_id,
            caller_workspace_ids=caller_workspace_ids,
        )
        >= PermissionEnum.READ
    )


def can_write(
    record: _AccessControlled,
    user_id: str | None,
    *,
    caller_workspace_ids: list[str] | None = None,
) -> bool:
    return (
        effective_permission(
            record,
            user_id,
            caller_workspace_ids=caller_workspace_ids,
        )
        >= PermissionEnum.WRITE
    )


def can_manage(
    record: _AccessControlled,
    user_id: str | None,
    *,
    caller_workspace_ids: list[str] | None = None,
) -> bool:
    return (
        effective_permission(
            record,
            user_id,
            caller_workspace_ids=caller_workspace_ids,
        )
        >= PermissionEnum.MANAGE
    )


def can_delete(
    record: _AccessControlled,
    user_id: str | None,
    *,
    caller_workspace_ids: list[str] | None = None,
) -> bool:
    return (
        effective_permission(
            record,
            user_id,
            caller_workspace_ids=caller_workspace_ids,
        )
        >= PermissionEnum.DELETE
    )


def can_open_content(
    record: _AccessControlled,
    user_id: str | None,
    *,
    caller_workspace_ids: list[str] | None = None,
) -> bool:
    """Whether a content URL may stream this file's bytes.

    Matches the product open matrix for `/files/{uid}/content` and
    `/f/{uid}`. Short-lived links are enforced by the S3 SigV4 gateway,
    not this helper.
    """
    if can_read(
        record,
        user_id,
        caller_workspace_ids=caller_workspace_ids,
    ):
        return True
    return record.public_permission == "read"


def filter_visible[R: _AccessControlled](
    records: list[R],
    user_id: str | None,
    *,
    caller_workspace_ids: list[str] | None = None,
) -> list[R]:
    """Only the records `user_id` may see (READ or better)."""
    return [
        record
        for record in records
        if can_read(
            record,
            user_id,
            caller_workspace_ids=caller_workspace_ids,
        )
    ]
