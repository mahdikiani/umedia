"""Provider connection API schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ProviderConnectionCreate(BaseModel):
    provider_type: str
    name: str = Field(min_length=1, max_length=120)
    config: dict[str, Any]
    # Dual-layer flags (docs/11-dual-layer-library.md), both default off.
    import_existing: bool = False
    mirror_structure: bool = False


class ProviderConnectionUpdate(BaseModel):
    """`PATCH /providers/{uid}` -- rename, enable/disable, and/or toggle
    the dual-layer flags; not a config change (see
    `ProviderConnectionService.update`'s docstring)."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None
    import_existing: bool | None = None
    mirror_structure: bool | None = None


class ProviderConnectionResponse(BaseModel):
    uid: str
    provider_type: str
    name: str
    status: str
    enabled: bool
    import_existing: bool
    mirror_structure: bool
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
    # "token" (today's single-step create-and-validate flow, unchanged),
    # "oauth", or "session" -- which sub-flow a frontend should offer for
    # this provider type. Purely descriptive for now: the oauth/session
    # sub-flow *endpoints* (docs/09-tasks.md) aren't built yet, so every
    # provider type still only supports the "token" flow in practice --
    # this field exists so the frontend and those upcoming routes have
    # something authoritative to read once they do exist.
    connect_flow: str
