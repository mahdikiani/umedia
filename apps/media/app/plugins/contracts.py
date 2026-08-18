"""Typed request/response models for the provider plugin REST contract.

Shared between the core-side client (`client.py`) and the plugin-author SDK
(`sdk.py`) so both sides of the socket agree on shapes without either
importing the other. See docs/03-provider-system.md for the contract.
"""

import base64
import json
from typing import Any, Literal

from pydantic import BaseModel, Field

ResourceType = Literal["file", "folder", "object", "document", "message"]


class Resource(BaseModel):
    """A provider-neutral resource -- docs/04-data-model.md's `Resource`,
    as returned by a plugin. Filesystem is only one possible provider:
    `parent_id` is optional, `type` is an open string, not just "file"."""

    id: str
    type: str
    name: str
    parent_id: str | None = None
    metadata: dict = Field(default_factory=dict)
    content_reference: str | None = None
    size: int | None = None
    content_type: str | None = None


class StatusOut(BaseModel):
    """`GET /status` response: plugin-reported backend health."""

    healthy: bool
    detail: str | None = None


class CreateResourceIn(BaseModel):
    """`POST /resources` request metadata (content, if any, is the raw
    streamed request body, not part of this JSON)."""

    name: str
    type: ResourceType = "file"
    parent_id: str | None = None


class UpdateResourceIn(BaseModel):
    """`PUT /resources/{id}` request: only the fields being changed."""

    name: str | None = None
    parent_id: str | None = None
    overwrite_content: bool = False


class PluginBackendError(RuntimeError):
    """Base error a `PluginBackend` implementation may raise.

    The SDK (`sdk.py`) translates these to the right HTTP status instead
    of a generic 500, so the core-side client (and eventually the
    `Resource` service, Phase 4) can distinguish "config was invalid",
    "resource doesn't exist", and "something unexpected broke" without
    parsing error strings.
    """

    http_status = 502  # the plugin's own upstream call failed


class ConnectionFailedError(PluginBackendError):
    """`connect()`/config validation failed against the real backend."""

    http_status = 400


class ResourceNotFoundError(PluginBackendError):
    """The requested resource doesn't exist at the provider."""

    http_status = 404


def encode_resource_id(resource_id: str) -> str:
    """URL-path-safe encoding for a resource id that may itself contain
    `/` -- path-addressed providers (`local`, and `rclone`'s remote
    types) return ids shaped like `folder/child.txt`, which FastAPI's
    default `{resource_id}` path converter can't match (it's a
    single-segment matcher), and percent-encoding the `/` as `%2F`
    doesn't help either: ASGI servers decode it to a literal slash
    *before* routing, same as if it had never been encoded (verified
    empirically, not just per spec). So the id travels the wire
    URL-safe-base64 encoded and is decoded straight back on the plugin
    side (`decode_resource_id`) -- see `client.py`'s call sites and
    `sdk.py`'s route handlers. Caught by `tests/test_resource_routes.py`'s
    nested-folder test: a *second*-level-deep resource is the first thing
    in this whole project to exercise a `/`-bearing id through the actual
    HTTP/socket layer, not just in-process against a `PluginBackend`.
    """
    return base64.urlsafe_b64encode(resource_id.encode()).decode().rstrip("=")


def decode_resource_id(encoded: str) -> str:
    padding = "=" * (-len(encoded) % 4)
    return base64.urlsafe_b64decode(encoded + padding).decode()


def encode_header_json(payload: dict[str, Any] | str) -> str:
    """ASCII-safe JSON for plugin IPC headers.

    httpx encodes header values as ASCII. `CreateResourceIn.name` (and
    connection config such as a non-ASCII `root_path`) therefore cannot
    travel as raw JSON -- Cyberduck uploads of e.g. `سیدیوسف_درسته-fa.pdf`
    failed with `'ascii' codec can't encode characters in position 9-15`.
    The JSON is UTF-8 then URL-safe base64, same trick as resource ids.
    """
    raw = (
        payload.encode("utf-8") if isinstance(payload, str)
        else json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8",
        )
    )
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_header_json(value: str) -> dict[str, Any]:
    """Accept the base64 form, and still parse legacy raw JSON."""
    stripped = value.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        loaded = json.loads(stripped)
        if not isinstance(loaded, dict):
            raise ValueError("plugin header JSON must be an object")
        return loaded
    padding = "=" * (-len(stripped) % 4)
    loaded = json.loads(base64.urlsafe_b64decode(stripped + padding))
    if not isinstance(loaded, dict):
        raise ValueError("plugin header JSON must be an object")
    return loaded
