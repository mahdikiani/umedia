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
            "server": ("drive.uln.me", 443),
            "scheme": "https",
        },
    )


def test_only_explicit_auth_and_health_routes_are_public() -> None:
    assert _is_public(request("/api/v1/health"))
    assert _is_public(request("/api/v1/auth/setup", "POST"))
    assert _is_public(request("/api/v1/auth/sessions", "POST"))
    assert not _is_public(request("/api/v1/media-files"))
    assert not _is_public(request("/api/v1/media-files/file-id/content"))


def test_public_share_download_is_method_limited() -> None:
    path = "/api/v1/shares/public/secure-token/content"

    assert _is_public(request(path, "GET"))
    assert _is_public(request(path, "HEAD"))
    assert not _is_public(request(path, "DELETE"))
