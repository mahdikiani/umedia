from datetime import datetime

from pydantic import BaseModel, ConfigDict

from .schemas import UserAccessKeyRecord


class AccessKeyCreateIn(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str = "key"


class AccessKeyOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    uid: str
    access_key_id: str
    label: str
    is_active: bool
    created_at: datetime

    @classmethod
    def from_record(cls, record: UserAccessKeyRecord) -> "AccessKeyOut":
        return cls(
            uid=record.uid,
            access_key_id=record.access_key_id,
            label=record.label,
            is_active=record.is_active,
            created_at=record.created_at,
        )


class AccessKeyCreatedOut(AccessKeyOut):
    secret_access_key: str


class S3EndpointOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    endpoint: str
    region: str
    bucket: str
    force_path_style: bool = True
