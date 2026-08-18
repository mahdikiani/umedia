"""HTTP request/response shapes for `apps.resources.routes`.

Kept separate from `schemas.py` (the service layer's plain `ResourceRecord`
dataclass, see that module's docstring) -- these are Pydantic, FastAPI-facing,
and free to diverge from the storage shape (e.g. `content_reference` is
deliberately not exposed here, it is the plugin's own opaque pointer).
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_validator

from .permissions import PermissionEnum
from .schemas import ResourceRecord


class PermissionOut(BaseModel):
    """One ACL entry on a resource."""

    user_id: str
    permission: int


class PermissionIn(BaseModel):
    """`PUT /resources/{uid}/permissions` body. `permission` accepts the
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


class ResourceOut(BaseModel):
    uid: str
    owner_id: str
    provider_connection_id: str
    type: str
    name: str
    parent_id: str | None
    metadata: dict[str, Any]
    content_type: str
    size: int
    status: str
    error: str | None
    public_permission: str
    permissions: list[PermissionOut]
    workspace_id: str | None
    access_at: datetime
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: ResourceRecord) -> "ResourceOut":
        return cls(
            uid=record.uid,
            owner_id=record.owner_id,
            provider_connection_id=record.provider_connection_id,
            type=record.type,
            name=record.name,
            parent_id=record.parent_id,
            metadata=record.metadata,
            content_type=record.content_type,
            size=record.size,
            status=record.status,
            error=record.error,
            public_permission=record.public_permission,
            permissions=[
                PermissionOut(**entry) for entry in record.permissions
            ],
            workspace_id=record.workspace_id,
            access_at=record.access_at,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class VolumeStatsOut(BaseModel):
    active_size: int
    active_count: int
    deleted_size: int
    deleted_count: int
