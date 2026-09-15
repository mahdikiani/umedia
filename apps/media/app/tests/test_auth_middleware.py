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
    assert _is_public(request("/api/v1/auth/refresh"))
    assert _is_public(request("/api/v1/auth/refresh", "POST"))
    assert _is_public(request("/api/v1/auth/oidc/start", "POST"))
    assert _is_public(request("/api/v1/auth/oidc/complete", "POST"))


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


def test_file_type_statics_are_public_for_safe_methods_only() -> None:
    """MIME icons are local files this app serves; <img> tags must load
    them without a session, including on public share pages."""
    assert _is_public(request("/api/v1/statics/folder-1485.svg"))
    assert _is_public(request("/api/v1/statics/text_color_pdf.svg", "HEAD"))
    assert not _is_public(request("/api/v1/statics/folder-1485.svg", "POST"))


def test_resource_routes_require_authentication() -> None:
    assert not _is_public(request("/api/v1/resources"))
    assert not _is_public(request("/api/v1/resources/some-uid"))
    assert not _is_public(request("/api/v1/resources/some-uid/content"))


def test_file_content_routes_are_public_for_safe_methods_only() -> None:
    """Content URLs are session-optional so the route can 404 on deny
    (existence stays private). Middleware must not 401 first."""
    assert _is_public(request("/api/v1/files/some-uid/content"))
    assert _is_public(request("/api/v1/files/some-uid/content", "HEAD"))
    assert _is_public(request("/api/v1/files/some-uid/content/logo.png"))
    assert not _is_public(request("/api/v1/files/some-uid/content/a/b.png", "HEAD"))
    assert not _is_public(request("/api/v1/files/some-uid/content", "DELETE"))
    assert not _is_public(request("/api/v1/files/some-uid"))
    assert not _is_public(request("/api/v1/files"))
