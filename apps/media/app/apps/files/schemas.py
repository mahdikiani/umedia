from datetime import datetime
from enum import IntEnum, StrEnum

from fastapi_mongo_base.schemas import UserOwnedEntitySchema
from fastapi_mongo_base.utils import timezone
from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from server.config import Settings

from . import statics


class FileStatus(StrEnum):
    processing = "processing"
    completed = "completed"
    failed = "failed"


class PermissionEnum(IntEnum):
    """
    Permission levels for file access control.
    """

    NONE = 0
    READ = 10
    WRITE = 20
    MANAGE = 30
    DELETE = 40
    OWNER = 100


class PermissionSchema(BaseModel):
    """
    Schema for file access permissions.
    """

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.tz),
        json_schema_extra={"index": True},
        description="Date and time the entity was created",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.tz),
        json_schema_extra={"index": True},
        description="Date and time the entity was last updated",
    )
    meta_data: dict | None = Field(
        default=None,
        description="Additional metadata for the entity",
    )

    permission: PermissionEnum = Field(default=PermissionEnum.NONE)

    @field_validator("permission", mode="before")
    @classmethod
    def validate_permission(cls, v: str | PermissionEnum) -> PermissionEnum:
        if isinstance(v, str):
            return PermissionEnum[v.upper()]
        return v

    @property
    def read(self) -> bool:
        return self.permission >= PermissionEnum.READ

    @property
    def write(self) -> bool:
        return self.permission >= PermissionEnum.WRITE

    @property
    def manage(self) -> bool:
        return self.permission >= PermissionEnum.MANAGE

    @property
    def delete(self) -> bool:
        return self.permission >= PermissionEnum.DELETE

    @property
    def owner(self) -> bool:
        return self.permission >= PermissionEnum.OWNER


class Permission(PermissionSchema):
    user_id: str


class FileMetaDataCreate(BaseModel):
    """
    Schema for creating new file metadata.
    """

    parent_id: str | None = None
    is_directory: bool = False
    filename: str

    permissions: list[Permission] = []
    public_permission: PermissionSchema = PermissionSchema()
    workspace_id: str | None = None


class FileMetaDataHistory(BaseModel):
    """
    Schema for file metadata history.
    """

    key: str
    filehash: str
    filename: str
    content_type: str
    size: int

    model_config = ConfigDict(from_attributes=True, validate_assignment=True)


class FileMetaDataSchema(FileMetaDataCreate, UserOwnedEntitySchema):
    """
    Schema for file metadata.
    """

    key: str | None = None

    # url: str | None = None

    filehash: str | None = None

    access_at: datetime = Field(default_factory=datetime.now)

    content_type: str
    size: int = 4096
    deleted_at: datetime | None = None

    status: FileStatus = FileStatus.completed
    error: str | None = None

    history: list[FileMetaDataHistory] = Field(default_factory=list)

    @computed_field
    @property
    def url(self) -> str:
        return "/".join([
            f"https://{Settings.root_url}{Settings.base_path}/f",
            self.uid,
            self.filename,
        ])

    @computed_field
    @property
    def icon(self) -> str:
        return statics.get_icon_from_mime_type(self.content_type)

    @computed_field
    @property
    def preview(self) -> str:
        if self.content_type.startswith("image/"):
            return self.url

        if self.content_type.startswith("video/"):
            return self.url

        return self.icon

    @property
    def real_size(self) -> int:
        return self.size + sum(item.get("size", 0) for item in self.history)


class FileMetaDataUpdate(BaseModel):
    """
    Schema for updating file metadata.
    """

    is_deleted: bool | None = None
    parent_id: str | None = None
    filename: str | None = None

    permissions: list[Permission] = []
    public_permission: PermissionSchema | None = None

    @property
    def need_manage_permissions(self) -> bool:
        return self.permissions or self.public_permission


class MultiPartOut(BaseModel):
    """
    Schema for multipart upload response.
    """

    upload_id: str


class PartUploadOut(BaseModel):
    """
    Schema for part upload response.
    """

    part_number: int
    etag: str


class VolumeOut(BaseModel):
    """
    Schema for volume information.
    """

    total_size: int = Field(
        description=(
            "Total size in bytes of all files (active + deleted) owned by the user"
        )
    )
    total_files: int = Field(
        description="Total count of all files (active + deleted) owned by the user"
    )
    active_size: int = Field(
        description=(
            "Total size in bytes of active (non-deleted) files owned by the user"
        )
    )
    active_files: int = Field(
        description="Total count of active (non-deleted) files owned by the user"
    )
    deleted_size: int = Field(
        description="Total size in bytes of deleted files owned by the user"
    )
    deleted_files: int = Field(
        description="Total count of deleted files owned by the user"
    )
    history_size: int = Field(
        description="Total size in bytes of all files in history records"
    )
    history_files: int = Field(
        description="Total count of all files in history records"
    )
    max_volume: int = Field(
        default=0,
        description="Maximum size in bytes allowed for the user",
    )
