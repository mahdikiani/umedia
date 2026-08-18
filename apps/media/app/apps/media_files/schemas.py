"""Plain data shapes the MediaFile service works with.

Deliberately not SQLAlchemy-specific -- `MediaFileService` depends on
repository protocols, not concrete ORM models, so its unit tests run
against whatever produces this shape.

A `MediaFileRecord` is one `media_files` row *joined with its primary
StorageObject* (when linked): `size`/`content_type`/`content_hash`/
`provider_connection_id`/`content_reference`/`storage_object_uid` are
derived from the physical layer, resolved by the repository in the same
local SQL query -- listing stays SQLite-only (docs/11-dual-layer-library.md).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

DIRECTORY_CONTENT_TYPE = "inode/directory"
DEFAULT_CONTENT_TYPE = "application/octet-stream"


@dataclass
class HistoryEntry:
    """A prior linked-content snapshot, kept when content is replaced."""

    storage_object_uid: str | None
    content_hash: str | None
    content_type: str
    size: int


@dataclass
class MediaFileRecord:
    """One user-library entry -- docs/11-dual-layer-library.md `media_files`,
    plus the primary-StorageObject join described in the module docstring."""

    uid: str
    owner_id: str
    type: str
    name: str
    parent_id: str | None
    status: str  # "processing" | "completed" | "failed"
    error: str | None
    public_permission: str  # "none" | "read"
    access_at: datetime
    deleted_at: datetime | None
    is_deleted: bool
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)
    history: list[HistoryEntry] = field(default_factory=list)
    # Per-user grants: `[{"user_id": str, "permission": int}, ...]` --
    # levels are `apps.media_files.permissions.PermissionEnum` values.
    permissions: list[dict[str, Any]] = field(default_factory=list)
    workspace_id: str | None = None
    starred: bool = False

    # ------------------------------------------------------------------
    # Derived from the primary StorageObject (all None/defaults for
    # folders and never-linked files).
    # ------------------------------------------------------------------
    storage_object_uid: str | None = None
    provider_connection_id: str | None = None
    content_reference: str | None = None
    content_hash: str | None = None
    content_type: str = DEFAULT_CONTENT_TYPE
    size: int = 0
