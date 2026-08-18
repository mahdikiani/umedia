from starlette.requests import Request

from apps.auth.middleware import _is_public


def request(path: str, method: str = "GET") -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [],
            "query_string": b"",
            "server": ("umedia.uln.me", 443),
            "scheme": "https",
        },
    )


def test_health_and_auth_bootstrap_routes_are_public() -> None:
    assert _is_public(request("/api/v1/health"))
    assert _is_public(request("/api/v1/auth/state"))
    assert _is_public(request("/api/v1/auth/setup", "POST"))
    assert _is_public(request("/api/v1/auth/sessions", "POST"))


def test_api_docs_are_public() -> None:
    """The docs page/schema must be reachable *before* logging in -- an
    admin has no session yet the first time they open the app, and the
    OpenAPI schema isn't sensitive (it's just the shape of the API, not
    data). Otherwise the docs page 401s on its own JS/schema fetch and is
    simply unusable as a way to discover or exercise the API."""
    assert _is_public(request("/api/v1/docs"))
    assert _is_public(request("/api/v1/openapi.json"))
    assert _is_public(request("/api/v1/redoc"))


def test_provider_and_connection_routes_require_authentication() -> None:
    assert not _is_public(request("/api/v1/provider-types"))
    assert not _is_public(request("/api/v1/providers"))


def test_public_link_route_is_public_for_safe_methods_only() -> None:
    """`/f/{uid}` and `/f/{uid}/{filename}` share links: readable/headable
    without a session (the route itself enforces the resource's own
    `public_permission`), but never for a mutating method -- no such
    route exists anyway. Trailing filename is cosmetic."""
    assert _is_public(request("/api/v1/f/some-uid"))
    assert _is_public(request("/api/v1/f/some-uid", "HEAD"))
    assert _is_public(request("/api/v1/f/some-uid/photo.jpg"))
    assert _is_public(request("/api/v1/f/some-uid/photo.jpg", "HEAD"))
    assert not _is_public(request("/api/v1/f/some-uid", "DELETE"))
    assert not _is_public(request("/api/v1/f/some-uid/photo.jpg", "DELETE"))


def test_s3_gateway_is_public_to_sigv4_for_every_method() -> None:
    for method in ("GET", "HEAD", "PUT", "POST", "DELETE"):
        assert _is_public(request("/api/v1/s3/file-1/photo.jpg", method))


def test_resource_routes_require_authentication() -> None:
    assert not _is_public(request("/api/v1/resources"))
    assert not _is_public(request("/api/v1/resources/some-uid"))
    assert not _is_public(request("/api/v1/resources/some-uid/content"))
