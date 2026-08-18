"""StorageObject persistence -- docs/11-dual-layer-library.md "Schema".

The physical layer: 1:1 with bytes/keys on a provider. No user-facing
`parent_id` here -- the library tree lives exclusively on `MediaFile`
(apps/media_files); `provider_parent_ref` below is the *provider's own*
containment (e.g. the POSIX parent dir for `local`), kept for the
provider-index browse and for mirroring.
"""

from datetime import datetime

from fastapi_mongo_base.sql.models import BaseEntity
from sqlalchemy import JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


class StorageObject(BaseEntity):
    """One remote object as last seen at its provider. `uid`,
    `created_at`, `updated_at`, `is_deleted` come from `BaseEntity`."""

    __tablename__ = "storage_objects"
    __table_args__ = (
        # The provider-side identity: upserts key on this pair.
        UniqueConstraint(
            "provider_connection_id",
            "content_reference",
            name="uq_storage_objects_connection_reference",
        ),
    )

    provider_connection_id: Mapped[str] = mapped_column(index=True)
    content_reference: Mapped[str] = mapped_column(index=True)
    provider_parent_ref: Mapped[str | None] = mapped_column(
        nullable=True, index=True,
    )
    type: Mapped[str] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(index=True)
    content_hash: Mapped[str | None] = mapped_column(nullable=True, index=True)
    content_type: Mapped[str] = mapped_column(default="application/octet-stream")
    size: Mapped[int] = mapped_column(default=0)
    # Python attribute can't be named `metadata` (reserved by SQLAlchemy's
    # declarative base); the column itself is still named "metadata".
    object_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    status: Mapped[str] = mapped_column(default="active", index=True)
    last_seen_at: Mapped[datetime] = mapped_column(default=datetime.now)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)
