"""MediaFile persistence -- docs/11-dual-layer-library.md "Schema".

The user library layer: what `/files` browses. The folder tree
(`parent_id`) lives *only* here; bytes live behind a `media_file_objects`
link to a `StorageObject` (apps/storage_objects). Folders need no link.
"""

from datetime import datetime

from fastapi_mongo_base.sql.models import BaseEntity
from sqlalchemy import JSON, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


class MediaFile(BaseEntity):
    """A user-facing library entry. `uid` (also the `/f/{uid}` link),
    `created_at`, `updated_at`, `is_deleted` come from `BaseEntity`."""

    __tablename__ = "media_files"

    # No default on purpose: every creation path must say who owns the
    # file (`MediaFileService` always passes the actor).
    owner_id: Mapped[str] = mapped_column(index=True)
    type: Mapped[str] = mapped_column(index=True)  # "file" | "folder" | ...
    name: Mapped[str] = mapped_column(index=True)
    parent_id: Mapped[str | None] = mapped_column(nullable=True, index=True)
    # Python attribute can't be named `metadata` (reserved by SQLAlchemy's
    # declarative base); the column itself is still named "metadata".
    # Carries e.g. the `{"import_root": <connection uid>}` marker on a
    # connection's library root folder.
    file_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    status: Mapped[str] = mapped_column(default="processing", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    public_permission: Mapped[str] = mapped_column(default="none")
    # `[{"user_id": str, "permission": int}, ...]` -- levels are
    # `apps.media_files.permissions.PermissionEnum` values.
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    # Stored but not yet consulted (see permissions.py's workspace hook).
    workspace_id: Mapped[str | None] = mapped_column(nullable=True, index=True)
    access_at: Mapped[datetime] = mapped_column(default=datetime.now)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # Version snapshots of linked storage when content is replaced.
    history: Mapped[list] = mapped_column(JSON, default=list)


class MediaFileObject(BaseEntity):
    """The MediaFile -> StorageObject link. v1: `role="primary"` only,
    and one link per StorageObject (unique below)."""

    __tablename__ = "media_file_objects"
    __table_args__ = (
        UniqueConstraint(
            "storage_object_id",
            name="uq_media_file_objects_storage_object",
        ),
    )

    media_file_id: Mapped[str] = mapped_column(index=True)
    storage_object_id: Mapped[str] = mapped_column(index=True)
    role: Mapped[str] = mapped_column(default="primary", index=True)


class MediaFileStar(BaseEntity):
    __tablename__ = "media_file_stars"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "media_file_id",
            name="uq_media_file_stars_user_file",
        ),
    )

    user_id: Mapped[str] = mapped_column(index=True)
    media_file_id: Mapped[str] = mapped_column(index=True)
