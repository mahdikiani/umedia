"""Plain data shape the StorageObject layers exchange -- deliberately not
SQLAlchemy-specific, same rationale as `apps/media_files/schemas.py`."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class StorageObjectRecord:
    """One `storage_objects` row -- docs/11-dual-layer-library.md."""

    uid: str
    provider_connection_id: str
    content_reference: str
    provider_parent_ref: str | None
    type: str
    name: str
    content_hash: str | None
    content_type: str
    size: int
    status: str
    last_seen_at: datetime
    deleted_at: datetime | None
    is_deleted: bool
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)
