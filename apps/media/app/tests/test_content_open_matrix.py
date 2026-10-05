"""Unit tests for the content open matrix (`can_open_content`)."""

from dataclasses import dataclass, field

from apps.media_files.permissions import (
    PermissionEnum,
    can_open_content,
    can_read,
    effective_permission,
)


@dataclass
class _Record:
    owner_id: str = "owner-1"
    permissions: list[dict] = field(default_factory=list)
    workspace_id: str | None = None
    public_permission: str = "none"


def test_owner_can_open_private_content() -> None:
    record = _Record()
    assert can_open_content(record, "owner-1")
    assert not can_open_content(record, None)
    assert not can_open_content(record, "stranger")


def test_acl_share_opens_at_read_level() -> None:
    record = _Record(
        permissions=[{"user_id": "friend", "permission": int(PermissionEnum.READ)}],
    )
    assert can_open_content(record, "friend")
    assert not can_open_content(record, "stranger")


def test_workspace_membership_opens_content() -> None:
    record = _Record(workspace_id="ws-9")
    assert can_open_content(
        record,
        "member",
        caller_workspace_ids=["ws-9"],
    )
    assert not can_open_content(
        record,
        "member",
        caller_workspace_ids=["other-ws"],
    )
    assert effective_permission(
        record,
        "member",
        caller_workspace_ids=["ws-9"],
    ) == PermissionEnum.READ


def test_public_permission_opens_for_anonymous() -> None:
    record = _Record(public_permission="read")
    assert can_open_content(record, None)
    assert can_open_content(record, "anyone")


def test_private_file_denies_anonymous_even_for_library_read_helpers() -> None:
    record = _Record()
    assert not can_read(record, None)
    assert not can_open_content(record, None)
