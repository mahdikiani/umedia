"""Virtual-hosted-style S3: `{bucket}.{endpoint-host}` instead of `/{bucket}`.

Cyberduck's Amazon S3 profile lists on the endpoint host, then talks to
`{bucket}.{endpoint}` for the bucket. Cloudflare Universal SSL does not
cover that nested name; DNS-only + this Host check maps it back onto the
path-style routes.

Object GET/HEAD/PUT on that host use the library-path key as the URL path.
Those are rewritten onto `/api/v1/s3/{bucket}/{library-path}` so the existing
path-style routes handle them. SigV4 signed the original path;
`s3_signed_path` on the ASGI scope preserves it for auth.
"""

from collections.abc import MutableMapping

from fastapi import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from server.config import Settings

from .auth import SIGNED_PATH_SCOPE_KEY, _normalize_host


def endpoint_hostname() -> str:
    raw = str(getattr(Settings, "root_url", "") or "")
    host = _normalize_host(
        raw.removeprefix("https://").removeprefix("http://").split("/", 1)[0],
    )
    return host


def bucket_from_hostname(host: str) -> str | None:
    """Bucket name when `host` is `{bucket}.{endpoint}`, else `None`."""
    endpoint = endpoint_hostname()
    if not endpoint:
        return None
    normalized = _normalize_host(host)
    suffix = f".{endpoint}"
    if not normalized.endswith(suffix):
        return None
    bucket = normalized[: -len(suffix)]
    if not bucket or "." in bucket:
        return None
    return bucket


def virtual_host_bucket(request: Request) -> str | None:
    """Bucket name when `Host` is `{bucket}.{endpoint}`, else `None`."""
    return bucket_from_hostname(
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or "",
    )


def _scope_header(scope: MutableMapping, name: str) -> str:
    want = name.lower().encode()
    for key, value in scope.get("headers") or []:
        if key.lower() == want:
            return value.decode()
    return ""


def rewrite_virtual_host_scope(scope: MutableMapping) -> None:
    """Map `{bucket}.{endpoint}/{key}` onto `/api/v1/s3/{bucket}/{key}`.

    Leaves `/` alone (ListObjects / ListBuckets already live there) and
    never rewrites `/api/...` so the versioned API stays untouched.
    """
    if scope.get("type") != "http":
        return
    path = scope.get("path") or ""
    if path in {"/", ""} or path.startswith("/api/"):
        return
    bucket = bucket_from_hostname(
        _scope_header(scope, "x-forwarded-host")
        or _scope_header(scope, "host"),
    )
    if not bucket:
        return
    scope[SIGNED_PATH_SCOPE_KEY] = path
    new_path = f"{Settings.base_path}/s3/{bucket}{path}"
    scope["path"] = new_path
    scope["raw_path"] = new_path.encode()


class S3VirtualHostRewriteMiddleware:
    """ASGI wrapper so the rewrite runs before FastAPI builds the Request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") == "http":
            rewrite_virtual_host_scope(scope)
        await self.app(scope, receive, send)
