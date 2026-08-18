from unittest.mock import Mock

from apps.s3.vhost import rewrite_virtual_host_scope, virtual_host_bucket
from server.config import Settings


def _request(*hosts: str) -> Mock:
    headers = {}
    if hosts:
        headers["host"] = hosts[0]
    if len(hosts) > 1:
        headers["x-forwarded-host"] = hosts[1]
    request = Mock()
    request.headers.get.side_effect = lambda key, default=None: headers.get(key, default)
    return request


def test_virtual_host_bucket_reads_nested_host() -> None:
    endpoint = str(Settings.root_url).removeprefix("https://").removeprefix("http://")
    bucket = Settings.S3_COMPAT_BUCKET
    request = _request(f"{bucket}.{endpoint}")
    assert virtual_host_bucket(request) == bucket


def test_virtual_host_bucket_ignores_the_endpoint_host() -> None:
    endpoint = str(Settings.root_url).removeprefix("https://").removeprefix("http://")
    assert virtual_host_bucket(_request(endpoint)) is None


def test_virtual_host_bucket_prefers_forwarded_host() -> None:
    endpoint = str(Settings.root_url).removeprefix("https://").removeprefix("http://")
    bucket = Settings.S3_COMPAT_BUCKET
    request = _request("localhost", f"{bucket}.{endpoint}")
    assert virtual_host_bucket(request) == bucket


def _endpoint() -> str:
    return str(Settings.root_url).removeprefix("https://").removeprefix("http://")


def _http_scope(path: str, host: str, *, forwarded: str | None = None) -> dict:
    headers = [(b"host", host.encode())]
    if forwarded is not None:
        headers.append((b"x-forwarded-host", forwarded.encode()))
    return {
        "type": "http",
        "path": path,
        "raw_path": path.encode(),
        "headers": headers,
    }


def test_rewrite_maps_virtual_host_object_onto_path_style_routes() -> None:
    bucket = Settings.S3_COMPAT_BUCKET
    path = "/docs/f72.txt"
    scope = _http_scope(path, f"{bucket}.{_endpoint()}")
    rewrite_virtual_host_scope(scope)
    assert scope["s3_signed_path"] == path
    assert scope["path"] == f"{Settings.base_path}/s3/{bucket}{path}"
    assert scope["raw_path"] == scope["path"].encode()


def test_rewrite_leaves_apex_and_bucket_root_alone() -> None:
    apex = _http_scope("/docs/f72.txt", _endpoint())
    rewrite_virtual_host_scope(apex)
    assert "s3_signed_path" not in apex
    assert apex["path"] == "/docs/f72.txt"

    listing = _http_scope("/", f"{Settings.S3_COMPAT_BUCKET}.{_endpoint()}")
    rewrite_virtual_host_scope(listing)
    assert "s3_signed_path" not in listing
    assert listing["path"] == "/"

    api = _http_scope("/api/v1/health", f"{Settings.S3_COMPAT_BUCKET}.{_endpoint()}")
    rewrite_virtual_host_scope(api)
    assert api["path"] == "/api/v1/health"
