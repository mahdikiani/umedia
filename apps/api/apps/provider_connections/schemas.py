"""Provider connection API schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ProviderConnectionCreate(BaseModel):
    provider_type: str
    name: str = Field(min_length=1, max_length=120)
    config: dict[str, Any]


class ProviderConnectionResponse(BaseModel):
    uid: str
    provider_type: str
    name: str
    status: str
    created_at: datetime
    last_tested_at: datetime | None = None
    last_error: str | None = None


class ProviderFieldResponse(BaseModel):
    key: str
    label: str
    input_type: str
    required: bool
    secret: bool
    placeholder: str | None


class ProviderTypeResponse(BaseModel):
    id: str
    name: str
    description: str
    adapter: str
    status: str
    capabilities: list[str]
    fields: list[ProviderFieldResponse]

