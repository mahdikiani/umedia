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


class OAuthStartRequest(BaseModel):
    provider_type: str = Field(min_length=1)


class OAuthStartResponse(BaseModel):
    provider_type: str
    authorization_url: str
    state: str
    redirect_uri: str


class OAuthCompleteRequest(BaseModel):
    """Finish the localhost-redirect paste flow and create the connection."""

    provider_type: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=120)
    callback: str = Field(min_length=1)
    # Optional when the pasted URL/query already carries `state=...`.
    state: str | None = None
    root_folder_id: str | None = None
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
    owner_id: str | None = None
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
    # "token" (single-step create with config fields), "oauth" (Google
    # Drive localhost-redirect paste flow via `/providers/oauth/start`
    # + `/providers/oauth/complete`), or "session" (Telegram multi-step;
    # not built yet).
    connect_flow: str
