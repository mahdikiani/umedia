import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote, unquote, urlparse

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from fastapi import Request

from apps.user_access_keys.factory import build_user_access_key_service_from_state
from apps.user_access_keys.services import UserAccessKeyService
from server.config import Settings

from .exceptions import AccessDenied, InvalidAccessKeyId, SignatureDoesNotMatch

SIGNED_PATH_SCOPE_KEY = "s3_signed_path"


def _signed_path(request: Request) -> str:
    """Path the client signed. Virtual-host rewrite stashes the original."""
    original = request.scope.get(SIGNED_PATH_SCOPE_KEY)
    if isinstance(original, str) and original:
        return original
    return request.url.path


@dataclass(frozen=True)
class S3AuthIdentity:
    """Credential owner resolved from an active user access key."""

    access_key: str
    secret_key: str
    user_id: str


def _get_access_key_service(request: Request) -> UserAccessKeyService:
    return build_user_access_key_service_from_state(request.app.state)


async def _resolve_identity(request: Request, access_key: str) -> S3AuthIdentity:
    service = _get_access_key_service(request)
    key = await service.get_active_key(access_key)
    if key is None:
        raise InvalidAccessKeyId
    return S3AuthIdentity(
        access_key=key.access_key_id,
        secret_key=service.decrypt_secret_str(key),
        user_id=key.user_id,
    )


def _normalize_host(host: str) -> str:
    """Lowercase host, drop a single forwarded entry, strip default ports.

    Presigned URLs sign only `host`; a mint that saw `umedia.uln.me` and a
    browser request that sends `umedia.uln.me:443` would otherwise 403.
    """
    value = host.split(",", 1)[0].strip().lower()
    if value.endswith(":443") or value.endswith(":80"):
        value = value.rsplit(":", 1)[0]
    return value


def public_base_url(request: Request) -> str:
    """Client-facing origin for SigV4 Host signing behind a reverse proxy.

    Prefer `X-Forwarded-Host` / `Host`, then `Settings.root_url` (compose
    `DOMAIN`). Always normalize default ports so a mint behind Traefik
    and a browser GET agree on the signed `host` header.
    """
    forwarded_host = request.headers.get("x-forwarded-host")
    host = _normalize_host(
        forwarded_host
        or request.headers.get("host")
        or request.url.netloc
        or "",
    )
    if not host:
        raw = str(getattr(Settings, "root_url", "") or "")
        host = _normalize_host(
            raw.removeprefix("https://").removeprefix("http://"),
        )
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_proto:
        scheme = forwarded_proto.split(",", 1)[0].strip()
    else:
        # root_url is often bare (`umedia.uln.me`); default to https in
        # production-like deploys when the ASGI scheme is still http.
        raw = str(getattr(Settings, "root_url", "") or "")
        if raw.startswith("http://"):
            scheme = "http"
        elif host and host not in {"localhost", "127.0.0.1", "testserver"}:
            scheme = "https"
        else:
            scheme = request.url.scheme or "https"
    return f"{scheme}://{host}"


def _headers(headers: Mapping[str, str]) -> dict[str, str]:
    normalized = {
        name.lower().strip(): " ".join(value.strip().split())
        for name, value in headers.items()
    }
    if "host" in normalized:
        normalized["host"] = _normalize_host(normalized["host"])
    return normalized



def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _signing_key(secret_key: str, date: str, region: str, service: str) -> bytes:
    key = f"AWS4{secret_key}".encode()
    for value in (date, region, service, "aws4_request"):
        key = hmac.new(key, value.encode(), hashlib.sha256).digest()
    return key


def _canonical_query(query: Mapping[str, str], *, omit_signature: bool = True) -> str:
    encoded = [
        (quote(name, safe="-_.~"), quote(value, safe="-_.~"))
        for name, value in query.items()
        if not (omit_signature and name.lower() == "x-amz-signature")
    ]
    return "&".join(f"{name}={value}" for name, value in sorted(encoded))


def _canonical_request(
    *,
    method: str,
    path: str,
    query: Mapping[str, str],
    headers: Mapping[str, str],
    signed_headers: list[str],
    payload_hash: str,
) -> str:
    canonical_headers = "".join(
        f"{name}:{headers.get(name, '')}\n" for name in signed_headers
    )
    return (
        f"{method.upper()}\n"
        f"{quote(unquote(path), safe='/-_.~')}\n"
        f"{_canonical_query(query)}\n"
        f"{canonical_headers}\n"
        f"{';'.join(signed_headers)}\n"
        f"{payload_hash}"
    )


def _signature(
    *,
    secret_key: str,
    amz_date: str,
    credential_scope: str,
    canonical_request: str,
) -> str:
    scope_parts = credential_scope.split("/")
    if len(scope_parts) != 4:
        raise SignatureDoesNotMatch
    date, region, service, terminal = scope_parts
    if service != "s3" or terminal != "aws4_request":
        raise SignatureDoesNotMatch
    string_to_sign = (
        "AWS4-HMAC-SHA256\n"
        f"{amz_date}\n"
        f"{credential_scope}\n"
        f"{_sha256(canonical_request.encode())}"
    )
    return hmac.new(
        _signing_key(secret_key, date, region, service),
        string_to_sign.encode(),
        hashlib.sha256,
    ).hexdigest()


def _authorization_parts(value: str) -> dict[str, str]:
    if not value.startswith("AWS4-HMAC-SHA256 "):
        raise SignatureDoesNotMatch("Unsupported authorization scheme")
    parts: dict[str, str] = {}
    try:
        for item in value.removeprefix("AWS4-HMAC-SHA256 ").split(","):
            name, part_value = item.strip().split("=", 1)
            parts[name] = part_value
    except ValueError as exc:
        raise SignatureDoesNotMatch("Malformed Authorization header") from exc
    return parts


def _access_key_from_request(request: Request) -> str:
    query = dict(request.query_params.multi_items())
    normalized_query = {name.lower(): value for name, value in query.items()}
    credential = normalized_query.get("x-amz-credential")
    if credential is None:
        authorization = request.headers.get("authorization")
        if authorization is None:
            raise AccessDenied
        credential = _authorization_parts(authorization).get("Credential")
    if credential is None or not credential.split("/", 1)[0]:
        raise InvalidAccessKeyId
    return credential.split("/", 1)[0]


def _header_signature(
    *,
    request: Request,
    body: bytes,
    identity: S3AuthIdentity,
    credential_parts: list[str],
    signed_headers: list[str],
    canonical_headers: dict[str, str],
) -> str:
    payload_hash = canonical_headers.get("x-amz-content-sha256")
    if payload_hash is None:
        request_headers = _headers(request.headers)
        payload_hash = request_headers.get("x-amz-content-sha256") or _sha256(body)
    amz_date = (
        canonical_headers.get("x-amz-date")
        or _headers(request.headers).get("x-amz-date")
        or _headers(request.headers).get("date", "")
    )
    canonical = _canonical_request(
        method=request.method,
        path=_signed_path(request),
        query=dict(request.query_params.multi_items()),
        headers=canonical_headers,
        signed_headers=signed_headers,
        payload_hash=payload_hash,
    )
    return _signature(
        secret_key=identity.secret_key,
        amz_date=amz_date,
        credential_scope="/".join(credential_parts[1:]),
        canonical_request=canonical,
    )


def _verify_header(request: Request, body: bytes, identity: S3AuthIdentity) -> None:
    request_headers = _headers(request.headers)
    authorization = request_headers.get("authorization")
    if authorization is None:
        raise AccessDenied
    parts = _authorization_parts(authorization)
    credential = parts.get("Credential", "")
    credential_parts = credential.split("/")
    if len(credential_parts) != 5 or credential_parts[0] != identity.access_key:
        raise InvalidAccessKeyId
    provided = parts.get("Signature")
    if provided is None:
        raise SignatureDoesNotMatch("Missing signature")
    signed_headers = sorted(
        name.strip().lower()
        for name in parts.get("SignedHeaders", "").split(";")
        if name.strip()
    )
    canonical_headers = {
        name: request_headers.get(name, "") for name in signed_headers
    }
    expected = _header_signature(
        request=request,
        body=body,
        identity=identity,
        credential_parts=credential_parts,
        signed_headers=signed_headers,
        canonical_headers=canonical_headers,
    )
    if hmac.compare_digest(expected, provided):
        return
    # Cloudflare / some proxies rewrite `Accept-Encoding` after the client
    # signed it (rclone + AWS SDK Go: often `identity` on list, `gzip` on
    # GetObject). Retry with the values those clients actually sign.
    if "accept-encoding" in signed_headers:
        for candidate in ("identity", "gzip", ""):
            if canonical_headers.get("accept-encoding") == candidate:
                continue
            retry_headers = dict(canonical_headers)
            retry_headers["accept-encoding"] = candidate
            expected = _header_signature(
                request=request,
                body=body,
                identity=identity,
                credential_parts=credential_parts,
                signed_headers=signed_headers,
                canonical_headers=retry_headers,
            )
            if hmac.compare_digest(expected, provided):
                return
    raise SignatureDoesNotMatch


def _verify_presigned(request: Request, identity: S3AuthIdentity) -> None:
    query = dict(request.query_params.multi_items())
    normalized = {name.lower(): value for name, value in query.items()}
    if normalized.get("x-amz-algorithm") != "AWS4-HMAC-SHA256":
        raise SignatureDoesNotMatch
    credential = normalized.get("x-amz-credential", "")
    credential_parts = credential.split("/")
    if len(credential_parts) != 5 or credential_parts[0] != identity.access_key:
        raise InvalidAccessKeyId
    provided = normalized.get("x-amz-signature")
    if provided is None:
        raise SignatureDoesNotMatch("Missing signature")
    amz_date = normalized.get("x-amz-date", "")
    expires_value = normalized.get("x-amz-expires", "")
    try:
        request_time = datetime.strptime(amz_date, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=UTC,
        )
        expires = int(expires_value)
    except ValueError as exc:
        raise SignatureDoesNotMatch("Invalid presign date or expiry") from exc
    age = (datetime.now(tz=UTC) - request_time).total_seconds()
    if expires < 1 or expires > 604800 or age > expires or age < -900:
        raise AccessDenied
    signed_headers = sorted(
        name.strip().lower()
        for name in normalized.get("x-amz-signedheaders", "host").split(";")
        if name.strip()
    )
    request_headers = _headers(request.headers)
    canonical = _canonical_request(
        method=request.method,
        path=_signed_path(request),
        query=query,
        headers={name: request_headers.get(name, "") for name in signed_headers},
        signed_headers=signed_headers,
        payload_hash="UNSIGNED-PAYLOAD",
    )
    expected = _signature(
        secret_key=identity.secret_key,
        amz_date=amz_date,
        credential_scope="/".join(credential_parts[1:]),
        canonical_request=canonical,
    )
    if not hmac.compare_digest(expected, provided):
        raise SignatureDoesNotMatch


async def verify_request_signature_for_identity(
    request: Request,
    body: bytes,
) -> S3AuthIdentity:
    """Resolve the active key owner and verify header or query SigV4."""
    if not Settings.S3_COMPAT_ENABLED:
        raise AccessDenied
    identity = await _resolve_identity(request, _access_key_from_request(request))
    query_names = {name.lower() for name in request.query_params}
    if "x-amz-algorithm" in query_names:
        _verify_presigned(request, identity)
    else:
        _verify_header(request, body, identity)
    return identity


def generate_presigned_url(
    *,
    method: str,
    key: str,
    expires_in: int,
    base_url: str,
    access_key: str,
    secret_key: str,
    region: str | None = None,
) -> str:
    """Generate a SigV4 presigned URL from explicit credentials.

    Object URLs are path-style:
    `/api/v1/s3/{bucket}/{library-path}` so S3 clients (rclone, aws cli)
    that put the bucket in the path agree with ListObjects.
    """
    signing_region = region or Settings.S3_COMPAT_REGION
    bucket = Settings.S3_COMPAT_BUCKET
    path = (
        f"{Settings.base_path.rstrip('/')}/s3/"
        f"{bucket}/{key.lstrip('/')}"
    )
    parsed = urlparse(f"{base_url.rstrip('/')}{path}")
    amz_date = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    credential_scope = f"{amz_date[:8]}/{signing_region}/s3/aws4_request"
    query = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key}/{credential_scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(expires_in),
        "X-Amz-SignedHeaders": "host",
    }
    canonical = _canonical_request(
        method=method,
        path=parsed.path,
        query=query,
        headers={"host": _normalize_host(parsed.netloc)},
        signed_headers=["host"],
        payload_hash="UNSIGNED-PAYLOAD",
    )
    query["X-Amz-Signature"] = _signature(
        secret_key=secret_key,
        amz_date=amz_date,
        credential_scope=credential_scope,
        canonical_request=canonical,
    )
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{_canonical_query(query, omit_signature=False)}"


def _request_signing_url(request: Request) -> str:
    host = request.headers.get("host") or request.url.netloc
    forwarded = request.headers.get("x-forwarded-proto")
    scheme = forwarded.split(",", 1)[0].strip() if forwarded else request.url.scheme
    return f"{scheme}://{host}{request.url.path}"


def sign_request_with_botocore(
    *,
    method: str,
    url: str,
    access_key: str,
    secret_key: str,
    region: str | None = None,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> dict[str, str]:
    credentials = Credentials(access_key, secret_key)
    request = AWSRequest(method=method, url=url, data=body, headers=headers or {})
    SigV4Auth(credentials, "s3", region or Settings.S3_COMPAT_REGION).add_auth(request)
    return dict(request.headers)
