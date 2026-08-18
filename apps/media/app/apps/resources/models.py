"""Resource persistence -- docs/04-data-model.md."""

from datetime import datetime

from fastapi_mongo_base.sql.models import BaseEntity
from sqlalchemy import JSON, Text
from sqlalchemy.orm import Mapped, mapped_column


class Resource(BaseEntity):
    """A provider-neutral resource. `uid`, `created_at`, `updated_at`,
    `is_deleted` come from `BaseEntity`."""

    __tablename__ = "resources"

    provider_connection_id: Mapped[str] = mapped_column(index=True)
    # No default on purpose: every creation path must say who owns the
    # resource (`ResourceService.create(owner_id=...)`). Existing rows
    # from pre-ACL installs are backfilled by migration 0004 -- see that
    # file for the sentinel and why.
    owner_id: Mapped[str] = mapped_column(index=True)
    type: Mapped[str] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(index=True)
    parent_id: Mapped[str | None] = mapped_column(nullable=True, index=True)
    # Python attribute can't be named `metadata` -- that's the reserved
    # SQLAlchemy declarative attribute holding the schema's MetaData
    # object; mapped to a column that *is* still named "metadata".
    resource_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    content_reference: Mapped[str | None] = mapped_column(nullable=True)
    content_hash: Mapped[str | None] = mapped_column(nullable=True, index=True)
    content_type: Mapped[str] = mapped_column()
    size: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(default="processing", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    public_permission: Mapped[str] = mapped_column(default="none")
    # `[{"user_id": str, "permission": int}, ...]` -- levels are
    # `apps.resources.permissions.PermissionEnum` values.
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    # Stored but not yet consulted (see permissions.py's workspace hook).
    workspace_id: Mapped[str | None] = mapped_column(nullable=True, index=True)
    access_at: Mapped[datetime] = mapped_column(default=datetime.now)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    history: Mapped[list] = mapped_column(JSON, default=list)
