"""MediaFile persistence -- docs/11-dual-layer-library.md "Schema".

The user library layer: what `/files` browses. The folder tree
(`parent_id`) lives *only* here; bytes live behind a `media_file_objects`
link to a `StorageObject` (apps/storage_objects). Folders need no link.
"""

from datetime import datetime

from fastapi_mongo_base.sql.models import BaseEntity
from sqlalchemy import JSON, Index, Text, UniqueConstraint
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
    # Folders are bound to one connection. Files may store the same as a
    # denormalized hint; the StorageObject join remains the source of
    # truth for linked bytes.
    provider_connection_id: Mapped[str | None] = mapped_column(
        nullable=True,
        index=True,
    )
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
    """The MediaFile -> StorageObject link. v1: `role="primary"` only.

    One StorageObject may link to multiple MediaFiles (same-storage
    library copy shares bytes). Import idempotency still uses
    `get_by_storage_object` (first link) to avoid re-importing.
    """

    __tablename__ = "media_file_objects"
    # Every library join filters on all three columns. Without this,
    # SQLite picks the single-column `role` index -- useless, since every
    # row is "primary" -- and the join turns quadratic: ~58k files took
    # minutes to list (S3 ListObjects, the Files page). Migration 0016.
    __table_args__ = (
        Index(
            "ix_media_file_objects_file_role_live",
            "media_file_id",
            "role",
            "is_deleted",
        ),
    )

    media_file_id: Mapped[str] = mapped_column(index=True)
    storage_object_id: Mapped[str] = mapped_column(index=True)
    role: Mapped[str] = mapped_column(default="primary", index=True)


class LibraryTransfer(BaseEntity):
    """A bulk library move/copy job with progress."""

    __tablename__ = "library_transfers"

    owner_id: Mapped[str] = mapped_column(index=True)
    operation: Mapped[str] = mapped_column()  # move | copy
    status: Mapped[str] = mapped_column(index=True)  # queued|running|…
    source_ids: Mapped[list] = mapped_column(JSON, default=list)
    dest_parent_id: Mapped[str | None] = mapped_column(nullable=True)
    conflict: Mapped[str] = mapped_column(default="rename")  # rename | skip
    total_items: Mapped[int] = mapped_column(default=0)
    done_items: Mapped[int] = mapped_column(default=0)
    failed_items: Mapped[int] = mapped_column(default=0)
    current_name: Mapped[str | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)


class OperationNotification(BaseEntity):
    __tablename__ = "operation_notifications"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "source_type",
            "source_uid",
            name="uq_operation_notification_source",
        ),
    )

    owner_id: Mapped[str] = mapped_column(index=True)
    operation: Mapped[str] = mapped_column()
    item_name: Mapped[str] = mapped_column()
    error: Mapped[str] = mapped_column(Text())
    source_type: Mapped[str] = mapped_column()
    source_uid: Mapped[str] = mapped_column()
    read_at: Mapped[datetime | None] = mapped_column(nullable=True)


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


class MediaFileTemporaryItem(BaseEntity):
    __tablename__ = "media_file_temporary_items"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "media_file_id",
            name="uq_media_file_temporary_items_user_file",
        ),
    )

    user_id: Mapped[str] = mapped_column(index=True)
    media_file_id: Mapped[str] = mapped_column(index=True)


class InstanceSettings(BaseEntity):
    """Singleton instance config. Placement decides where root uploads
    and new root folders land when the parent folder is not bound."""

    __tablename__ = "instance_settings"

    placement_policy: Mapped[str] = mapped_column(default="default")
    default_connection_id: Mapped[str | None] = mapped_column(nullable=True)
    fill_order: Mapped[list] = mapped_column(JSON, default=list)
