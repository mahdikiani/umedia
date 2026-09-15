"""HTTP request/response shapes for `apps.media_files.routes`.

Kept separate from `schemas.py` (the service layer's plain
`MediaFileRecord` dataclass) -- these are Pydantic, FastAPI-facing, and
free to diverge from the storage shape. `content_reference` is
deliberately not exposed on files: it's the plugin's own opaque pointer;
the provider-index browse (`StorageObjectOut`) does expose it, that view
*is* the physical layer.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from apps.storage_objects.schemas import StorageObjectRecord
from utils.pagination import Page

from .permissions import PermissionEnum
from .schemas import MediaFileRecord


class PageOut[ItemT: BaseModel](BaseModel):
    """The offset/limit envelope every list endpoint returns -- `total`
    counts the full filtered/ranked set, never just this slice."""

    items: list[ItemT]
    total: int
    limit: int
    offset: int
    has_more: bool

    @classmethod
    def from_page(
        cls, page: Page[Any], items: "list[ItemT]",
    ) -> "PageOut[ItemT]":
        return cls(
            items=items,
            total=page.total,
            limit=page.limit,
            offset=page.offset,
            has_more=page.has_more,
        )


class PermissionOut(BaseModel):
    """One ACL entry on a media file."""

    user_id: str
    permission: int


class PermissionIn(BaseModel):
    """`PUT /files/{uid}/permissions` body. `permission` accepts the
    integer level or its name (`"read"`/`"WRITE"`/...); `0`/`"none"`
    removes the target's entry."""

    user_id: str
    permission: int

    @field_validator("permission", mode="before")
    @classmethod
    def _coerce_permission(cls, value: object) -> object:
        if isinstance(value, str) and not value.lstrip("-").isdigit():
            try:
                return int(PermissionEnum[value.upper()])
            except KeyError:
                raise ValueError(
                    f"unknown permission name '{value}', expected one of "
                    f"{[member.name for member in PermissionEnum]}",
                ) from None
        return value


class MediaFileUpdateIn(BaseModel):
    """`PATCH /files/{uid}` body. `parent_id: null` means "move to the
    library root" -- routes distinguish it from "not sent" via
    `model_fields_set`."""

    name: str | None = None
    parent_id: str | None = None
    public_permission: str | None = None
    starred: bool | None = None


class LinkIn(BaseModel):
    """`POST /files/{uid}/link` body."""

    storage_object_id: str


class TemporaryLinkIn(BaseModel):
    """`POST /files/{uid}/temporary-link` body -- how long the signed
    link stays valid, bounded to [1 minute, 7 days]."""

    expires_in: int = Field(default=3600, ge=60, le=604800)


class TemporaryLinkOut(BaseModel):
    """A minted temporary link. `url` is a path-absolute SigV4-presigned
    GET on the S3 gateway (`/api/v1/s3/{bucket}/{library-path}?X-Amz-...`);
    the web client prefixes `window.location.origin`. `key_id` is the
    public id of the access key that signed it -- never the secret."""

    url: str
    key_id: str
    expires: int
    expires_at: datetime


class MediaFileOut(BaseModel):
    uid: str
    owner_id: str
    type: str
    name: str
    parent_id: str | None
    metadata: dict[str, Any]
    content_type: str
    size: int
    status: str
    error: str | None
    public_permission: str
    starred: bool
    permissions: list[PermissionOut]
    workspace_id: str | None
    provider_connection_id: str | None
    storage_object_id: str | None
    access_at: datetime
    #: When the file was soft-deleted (null while live) -- what the trash
    #: view's "days left before permanent removal" is computed from.
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: MediaFileRecord) -> "MediaFileOut":
        return cls(
            uid=record.uid,
            owner_id=record.owner_id,
            type=record.type,
            name=record.name,
            parent_id=record.parent_id,
            metadata=record.metadata,
            content_type=record.content_type,
            size=record.size,
            status=record.status,
            error=record.error,
            public_permission=record.public_permission,
            starred=record.starred,
            permissions=[PermissionOut(**entry) for entry in record.permissions],
            workspace_id=record.workspace_id,
            provider_connection_id=record.provider_connection_id,
            storage_object_id=record.storage_object_uid,
            access_at=record.access_at,
            deleted_at=record.deleted_at,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class StorageObjectOut(BaseModel):
    """One physical-index entry -- `GET /providers/{uid}/objects`."""

    uid: str
    provider_connection_id: str
    content_reference: str
    provider_parent_ref: str | None
    type: str
    name: str
    content_hash: str | None
    content_type: str
    size: int
    metadata: dict[str, Any]
    status: str
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: StorageObjectRecord) -> "StorageObjectOut":
        return cls(
            uid=record.uid,
            provider_connection_id=record.provider_connection_id,
            content_reference=record.content_reference,
            provider_parent_ref=record.provider_parent_ref,
            type=record.type,
            name=record.name,
            content_hash=record.content_hash,
            content_type=record.content_type,
            size=record.size,
            metadata=record.metadata,
            status=record.status,
            last_seen_at=record.last_seen_at,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class PlacementOut(BaseModel):
    """`GET /settings/placement` -- where root uploads land."""

    policy: Literal["default", "fill_order", "most_free"]
    default_connection_id: str | None
    fill_order: list[str]


class PlacementIn(BaseModel):
    """`PATCH /settings/placement` body -- omitted fields stay as-is."""

    policy: Literal["default", "fill_order", "most_free"] | None = None
    default_connection_id: str | None = None
    fill_order: list[str] | None = None


class VolumeStatsOut(BaseModel):
    """`GET /files/stats` -- owned library usage for the sidebar card."""

    used_bytes: int
    file_count: int
    folder_count: int


class SyncAcceptedOut(BaseModel):
    """`POST /providers/{uid}/sync` — job accepted; work runs in background."""

    status: Literal["started"] = "started"
    connection_id: str


class SyncStatusOut(BaseModel):
    """`GET /providers/{uid}/sync` — whether a background sync is in flight."""

    status: Literal["idle", "running"]
    connection_id: str


class SyncResultOut(BaseModel):
    """Internal/outcome counters from a finished sync job (logged, not the
    HTTP accept body). Kept for service return typing and tests."""

    imported: int
    updated: int = 0
    pushed: int = 0
    seen: int


class TransferCreateIn(BaseModel):
    """`POST /files/transfers` body."""

    operation: Literal["move", "copy"]
    source_ids: list[str] = Field(min_length=1)
    dest_parent_id: str | None = None
    conflict: Literal["rename", "skip"] = "rename"


class TemporaryAddIn(BaseModel):
    media_file_ids: list[str]


class TransferOut(BaseModel):
    """One library transfer job (always returned with 202 on create)."""

    uid: str
    operation: str
    status: str
    source_ids: list[str]
    dest_parent_id: str | None
    total_items: int
    done_items: int
    failed_items: int
    progress_pct: int
    current_name: str | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def from_record(cls, record: object) -> "TransferOut":
        from .transfer_repository import TransferRecord

        assert isinstance(record, TransferRecord)  # noqa: S101
        return cls(
            uid=record.uid,
            operation=record.operation,
            status=record.status,
            source_ids=list(record.source_ids),
            dest_parent_id=record.dest_parent_id,
            total_items=record.total_items,
            done_items=record.done_items,
            failed_items=record.failed_items,
            progress_pct=record.progress_pct,
            current_name=record.current_name,
            error=record.error,
            created_at=record.created_at,
            started_at=record.started_at,
            finished_at=record.finished_at,
        )
