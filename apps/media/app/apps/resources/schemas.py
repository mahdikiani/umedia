"""Plain data shapes the `Resource` service works with.

Deliberately not SQLAlchemy-specific -- `ResourceService` depends on
`ResourceRepositoryProtocol` (services.py), not a concrete ORM model, so
its tests (P4.1) run against a fake repository, and the real SQLAlchemy
model (P4.2, `models.py`) just needs to produce/consume this same shape.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class HistoryEntry:
    """A prior content snapshot, kept when `update()` replaces content."""

    content_reference: str | None
    content_hash: str | None
    content_type: str
    size: int


@dataclass
class ResourceRecord:
    """One `Resource` row, exactly as `docs/04-data-model.md` describes it."""

    uid: str
    owner_id: str
    provider_connection_id: str
    type: str
    name: str
    parent_id: str | None
    content_reference: str | None
    content_hash: str | None
    content_type: str
    size: int
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
    # levels are `apps.resources.permissions.PermissionEnum` values,
    # stored as plain ints/dicts because that's exactly the JSON shape
    # the `resources.permissions` column holds.
    permissions: list[dict[str, Any]] = field(default_factory=list)
    # Stored but not yet consulted -- see the commented workspace hook in
    # `apps.resources.permissions.effective_permission`.
    workspace_id: str | None = None
