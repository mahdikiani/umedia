from dataclasses import dataclass
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from starlette.requests import Request

from apps.s3 import auth as s3_auth
from apps.s3.auth import generate_presigned_url, verify_request_signature_for_identity
from apps.s3.exceptions import InvalidAccessKeyId, SignatureDoesNotMatch

ACCESS_KEY = "um_test_access_key"
SECRET_KEY = "test-secret-key"
USER_ID = "user-1"
REGION = "us-east-1"


@dataclass(frozen=True)
class _Key:
    uid: str = "key-1"
    user_id: str = USER_ID
    access_key_id: str = ACCESS_KEY
    encrypted_secret: str = "encrypted"
    label: str = "default"
    is_active: bool = True


class _AccessKeys:
    def __init__(self, *, active: bool = True) -> None:
        self._active = active

    async def get_active_key(self, access_key_id: str) -> _Key | None:
        if self._active and access_key_id == ACCESS_KEY:
            return _Key()
        return None

    def decrypt_secret_str(self, key: _Key) -> str:
        assert key.access_key_id == ACCESS_KEY
        return SECRET_KEY


def _request(
    *,
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
) -> Request:
    parsed = urlsplit(url)
    raw_headers = [
        (key.lower().encode(), value.encode())
        for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": method,
        "path": parsed.path,
        "raw_path": parsed.path.encode(),
        "root_path": "",
        "scheme": parsed.scheme,
        "query_string": parsed.query.encode(),
        "headers": raw_headers,
        "server": (parsed.hostname or "testserver", parsed.port),
        "client": ("127.0.0.1", 50000),
        "http_version": "1.1",
        "app": SimpleNamespace(state=SimpleNamespace()),
    }
    return Request(scope)


def _signed_headers(*, url: str, secret_key: str = SECRET_KEY) -> dict[str, str]:
    request = AWSRequest(
        method="GET",
        url=url,
        headers={"Host": "testserver", "x-amz-content-sha256": "UNSIGNED-PAYLOAD"},
    )
    SigV4Auth(Credentials(ACCESS_KEY, secret_key), "s3", REGION).add_auth(request)
    return {**dict(request.headers), "Host": "testserver"}


@pytest.mark.asyncio
async def test_header_signature_resolves_active_database_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "http://testserver/api/v1/s3/file-1/photo.jpg"
    monkeypatch.setattr(s3_auth, "_get_access_key_service", lambda _: _AccessKeys())

    identity = await verify_request_signature_for_identity(
        _request(method="GET", url=url, headers=_signed_headers(url=url)),
        b"",
    )

    assert identity.access_key == ACCESS_KEY
    assert identity.secret_key == SECRET_KEY
    assert identity.user_id == USER_ID


@pytest.mark.asyncio
async def test_presigned_url_round_trip_uses_explicit_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(s3_auth, "_get_access_key_service", lambda _: _AccessKeys())
    url = generate_presigned_url(
        method="GET",
        key="file-1/photo.jpg",
        expires_in=3600,
        base_url="http://testserver",
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        region=REGION,
    )

    identity = await verify_request_signature_for_identity(
        _request(method="GET", url=url, headers={"Host": "testserver"}),
        b"",
    )

    assert identity.user_id == USER_ID
    assert "X-Amz-SignedHeaders=host" in url
    assert "X-Amz-Signature=" in url


@pytest.mark.asyncio
async def test_deactivated_key_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "http://testserver/api/v1/s3/file-1/photo.jpg"
    monkeypatch.setattr(
        s3_auth,
        "_get_access_key_service",
        lambda _: _AccessKeys(active=False),
    )

    with pytest.raises(InvalidAccessKeyId):
        await verify_request_signature_for_identity(
            _request(method="GET", url=url, headers=_signed_headers(url=url)),
            b"",
        )


@pytest.mark.asyncio
async def test_wrong_secret_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "http://testserver/api/v1/s3/file-1/photo.jpg"
    monkeypatch.setattr(s3_auth, "_get_access_key_service", lambda _: _AccessKeys())

    with pytest.raises(SignatureDoesNotMatch):
        await verify_request_signature_for_identity(
            _request(
                method="GET",
                url=url,
                headers=_signed_headers(url=url, secret_key="wrong-secret"),
            ),
            b"",
        )


@pytest.mark.asyncio
async def test_accept_encoding_mutated_by_proxy_still_verifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """rclone / AWS SDK Go signs `Accept-Encoding: identity`; Cloudflare
    often replaces it with `gzip, br` before the origin sees the request.
    Verification must still succeed (try the signed `identity` value)."""
    monkeypatch.setattr(s3_auth, "_get_access_key_service", lambda _: _AccessKeys())
    url = "http://testserver/api/v1/s3?x-id=ListBuckets"
    request = AWSRequest(
        method="GET",
        url=url,
        headers={
            "Host": "testserver",
            "Accept-Encoding": "identity",
            "amz-sdk-invocation-id": "inv-1",
            "amz-sdk-request": "attempt=1; max=10",
            "x-amz-content-sha256": (
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
            ),
        },
    )
    SigV4Auth(Credentials(ACCESS_KEY, SECRET_KEY), "s3", REGION).add_auth(request)
    headers = {**dict(request.headers), "Host": "testserver"}
    # Simulate the reverse-proxy mutation.
    headers["Accept-Encoding"] = "gzip, br"

    identity = await verify_request_signature_for_identity(
        _request(method="GET", url=url, headers=headers),
        b"",
    )
    assert identity.access_key == ACCESS_KEY


@pytest.mark.asyncio
async def test_accept_encoding_gzip_mutated_still_verifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GetObject from rclone signs `Accept-Encoding: gzip`; proxies may
    rewrite it to `gzip, br`."""
    monkeypatch.setattr(s3_auth, "_get_access_key_service", lambda _: _AccessKeys())
    url = "http://testserver/api/v1/s3/umedia/file-1/photo.jpg?x-id=GetObject"
    request = AWSRequest(
        method="GET",
        url=url,
        headers={
            "Host": "testserver",
            "Accept-Encoding": "gzip",
            "amz-sdk-invocation-id": "inv-2",
            "amz-sdk-request": "attempt=1; max=10",
            "x-amz-content-sha256": (
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
            ),
        },
    )
    SigV4Auth(Credentials(ACCESS_KEY, SECRET_KEY), "s3", REGION).add_auth(request)
    headers = {**dict(request.headers), "Host": "testserver"}
    headers["Accept-Encoding"] = "gzip, br"

    identity = await verify_request_signature_for_identity(
        _request(method="GET", url=url, headers=headers),
        b"",
    )
    assert identity.access_key == ACCESS_KEY


@pytest.mark.asyncio
async def test_presigned_url_uses_path_style_bucket_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(s3_auth, "_get_access_key_service", lambda _: _AccessKeys())
    from server.config import Settings

    url = generate_presigned_url(
        method="GET",
        key="file-1/photo.jpg",
        expires_in=3600,
        base_url="http://testserver",
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        region=REGION,
    )
    assert f"/api/v1/s3/{Settings.S3_COMPAT_BUCKET}/file-1/photo.jpg?" in url
    identity = await verify_request_signature_for_identity(
        _request(method="GET", url=url, headers={"Host": "testserver"}),
        b"",
    )
    assert identity.user_id == USER_ID


@pytest.mark.asyncio
async def test_presigned_url_accepts_default_https_port_on_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Browsers / proxies sometimes send `Host: example.com:443`; the
    signature must still match a mint that used the bare hostname."""
    monkeypatch.setattr(s3_auth, "_get_access_key_service", lambda _: _AccessKeys())
    url = generate_presigned_url(
        method="GET",
        key="file-1/photo.jpg",
        expires_in=3600,
        base_url="https://umedia.uln.me",
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        region=REGION,
    )

    identity = await verify_request_signature_for_identity(
        _request(method="GET", url=url, headers={"Host": "umedia.uln.me:443"}),
        b"",
    )
    assert identity.user_id == USER_ID


@pytest.mark.asyncio
async def test_presigned_url_survives_hash_and_query_chars_in_filename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.s3.paths import encode_key

    monkeypatch.setattr(s3_auth, "_get_access_key_service", lambda _: _AccessKeys())
    url = generate_presigned_url(
        method="GET",
        key=encode_key("folder/a#b?.pdf"),
        expires_in=3600,
        base_url="http://testserver",
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        region=REGION,
    )
    path = urlsplit(url).path
    assert "%23" in path and "%3F" in path
    assert "#" not in path
    assert path.endswith(".pdf")

    identity = await verify_request_signature_for_identity(
        _request(method="GET", url=url, headers={"Host": "testserver"}),
        b"",
    )
    assert identity.user_id == USER_ID
