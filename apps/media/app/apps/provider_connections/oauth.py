from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from fastapi_mongo_base.core.exceptions import BaseHTTPException

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_URL = "https://oauth2.googleapis.com/token"
GOOGLE_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
OAUTH_PROVIDERS = {
    "google_drive": {
        "authorization_endpoint": GOOGLE_AUTH_ENDPOINT,
        "token_endpoint": GOOGLE_OAUTH_URL,
        "scopes": (GOOGLE_DRIVE_SCOPE,),
        "authorization_params": {"access_type": "offline", "prompt": "consent"},
    },
    "onedrive": {
        "authorization_endpoint": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_endpoint": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scopes": ("offline_access", "Files.ReadWrite", "User.Read"),
        "authorization_params": {"prompt": "consent"},
    },
    "dropbox": {
        "authorization_endpoint": "https://www.dropbox.com/oauth2/authorize",
        "token_endpoint": "https://api.dropboxapi.com/oauth2/token",
        "scopes": (
            "files.content.read",
            "files.content.write",
            "files.metadata.read",
            "files.metadata.write",
        ),
        "authorization_params": {"token_access_type": "offline"},
    },
}
OAUTH_STATE_TTL_SECONDS = 20 * 60
SUPPORTED_OAUTH_PROVIDERS = frozenset(OAUTH_PROVIDERS)


class OAuthConfigurationError(BaseHTTPException):
    def __init__(self, detail: str = "OAuth client is not configured") -> None:
        super().__init__(
            status_code=422,
            error_code="oauth_not_configured",
            detail=detail,
            message=detail,
        )


class OAuthCallbackError(BaseHTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=422,
            error_code="invalid_oauth_callback",
            detail=detail,
            message=detail,
        )


class OAuthExchangeError(BaseHTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=400,
            error_code="oauth_exchange_failed",
            detail=detail,
            message="Could not exchange the OAuth authorization code",
        )


@dataclass(frozen=True)
class GoogleOAuthCredentials:
    client_id: str
    client_secret: str
    redirect_uri: str = "http://localhost"
    provider_type: str = "google_drive"

    @property
    def configured(self) -> bool:
        return bool(self.client_id.strip() and self.client_secret.strip())


@dataclass(frozen=True)
class ParsedCallback:
    """Result of parsing a user-pasted OAuth callback string."""

    code: str | None = None
    state: str | None = None
    token: dict[str, Any] | None = None


@dataclass
class _PendingState:
    provider_type: str
    created_at: float


class OAuthStateStore:
    """In-memory CSRF state map with TTL (single-container only)."""

    def __init__(self, *, ttl_seconds: int = OAUTH_STATE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._pending: dict[str, _PendingState] = {}

    def create(self, provider_type: str) -> str:
        self._purge_expired()
        state = secrets.token_urlsafe(32)
        self._pending[state] = _PendingState(
            provider_type=provider_type,
            created_at=time.monotonic(),
        )
        return state

    def consume(self, state: str, *, provider_type: str) -> bool:
        self._purge_expired()
        pending = self._pending.pop(state, None)
        if pending is None:
            return False
        return pending.provider_type == provider_type

    def _purge_expired(self) -> None:
        cutoff = time.monotonic() - self._ttl
        expired = [
            key for key, value in self._pending.items() if value.created_at < cutoff
        ]
        for key in expired:
            del self._pending[key]


def build_auth_url(
    credentials: GoogleOAuthCredentials,
    *,
    state: str,
) -> str:
    if not credentials.configured:
        raise OAuthConfigurationError(
            f"{credentials.provider_type} OAuth is not configured",
        )
    provider = OAUTH_PROVIDERS.get(credentials.provider_type)
    if provider is None:
        raise OAuthConfigurationError("OAuth provider is not supported")
    params = {
        "client_id": credentials.client_id,
        "redirect_uri": credentials.redirect_uri,
        "response_type": "code",
        "scope": " ".join(provider["scopes"]),
        "state": state,
        **provider["authorization_params"],
    }
    return f"{provider['authorization_endpoint']}?{urlencode(params)}"


def parse_callback(raw: str) -> ParsedCallback:
    """Accept full URL, query string, bare code, or token JSON."""
    text = raw.strip()
    if not text:
        raise OAuthCallbackError("Callback paste is empty")

    as_json = _try_parse_token_json(text)
    if as_json is not None:
        return ParsedCallback(token=as_json)

    if "://" in text or text.startswith("//"):
        return _parse_url(text)

    if "=" in text and ("code=" in text or "state=" in text or "access_token=" in text):
        return _parse_query(text.lstrip("?#"))

    # Bare authorization code (no query delimiters).
    if all(ch not in text for ch in " \n\t{}[]"):
        return ParsedCallback(code=text)

    raise OAuthCallbackError("Could not parse OAuth callback paste")


def _try_parse_token_json(text: str) -> dict[str, Any] | None:
    candidate = text
    if not (candidate.startswith("{") or candidate.startswith('"')):
        return None
    try:
        value: Any = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    # JSON string wrapping an object: "\"{...}\"" already handled by loads;
    # a string whose content is itself JSON is also accepted.
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if isinstance(value, dict) and value.get("access_token"):
        return value
    return None


def _parse_url(url: str) -> ParsedCallback:
    parsed = urlparse(url)
    query = parsed.query or ""
    # Prefer query; fall back to hash (some clients put params in the fragment).
    if not query and parsed.fragment:
        fragment = parsed.fragment
        if fragment.startswith("/"):
            # e.g. localhost/#/...?code=... — take the query part of the fragment
            frag_parsed = urlparse(fragment if "://" in fragment else f"x:{fragment}")
            query = frag_parsed.query or fragment.lstrip("?#")
        else:
            query = fragment.lstrip("?")
    if not query:
        raise OAuthCallbackError("Redirect URL has no OAuth parameters")
    return _parse_query(query)


def _parse_query(query: str) -> ParsedCallback:
    params = parse_qs(query, keep_blank_values=False)
    # Flatten single-value lists.
    flat = {key: values[0] for key, values in params.items() if values}

    if flat.get("access_token"):
        return ParsedCallback(token=dict(flat), state=flat.get("state"))

    code = flat.get("code")
    if not code:
        error = flat.get("error") or flat.get("error_description")
        if error:
            raise OAuthCallbackError(f"OAuth provider returned an error: {error}")
        raise OAuthCallbackError("Callback is missing an authorization code")
    return ParsedCallback(code=code, state=flat.get("state"))


def serialize_rclone_token(
    token: dict[str, Any],
    *,
    now: datetime | None = None,
) -> str:
    """Normalize to rclone's golang oauth2 JSON blob (stringified)."""
    current = now or datetime.now(UTC)
    access_token = token.get("access_token")
    if not access_token:
        raise OAuthCallbackError("Token JSON is missing access_token")

    expiry = token.get("expiry") or token.get("expires_at")
    if not expiry and token.get("expires_in") is not None:
        try:
            seconds = int(token["expires_in"])
        except (TypeError, ValueError) as error:
            raise OAuthCallbackError("Token expires_in is invalid") from error
        expiry = (current + timedelta(seconds=seconds)).strftime(
            "%Y-%m-%dT%H:%M:%SZ",
        )
    elif isinstance(expiry, (int, float)):
        expiry = datetime.fromtimestamp(expiry, tz=UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ",
        )
    elif isinstance(expiry, str) and expiry.endswith("+00:00"):
        expiry = expiry.replace("+00:00", "Z")

    blob = {
        "access_token": access_token,
        "token_type": token.get("token_type") or "Bearer",
        "refresh_token": token.get("refresh_token") or "",
        "expiry": expiry or "",
    }
    if not blob["refresh_token"]:
        raise OAuthCallbackError(
            "Google did not return a refresh_token. Re-authorize with consent "
            "(prompt=consent) or revoke prior access and try again.",
        )
    return json.dumps(blob)


async def exchange_code(
    credentials: GoogleOAuthCredentials,
    *,
    code: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    if not credentials.configured:
        raise OAuthConfigurationError()
    provider = OAUTH_PROVIDERS.get(credentials.provider_type)
    if provider is None:
        raise OAuthConfigurationError("OAuth provider is not supported")

    payload = {
        "code": code,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "redirect_uri": credentials.redirect_uri,
        "grant_type": "authorization_code",
    }

    async def _post(http: httpx.AsyncClient) -> httpx.Response:
        return await http.post(provider["token_endpoint"], data=payload)

    try:
        if client is None:
            async with httpx.AsyncClient(timeout=30.0) as http:
                response = await _post(http)
        else:
            response = await _post(client)
    except httpx.HTTPError as error:
        raise OAuthExchangeError(str(error)) from error

    data: dict[str, Any] = {}
    with_content = bool(response.content)
    if with_content:
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                data = parsed
        except ValueError:
            data = {}

    if response.status_code >= 400 or not data.get("access_token"):
        detail = (
            data.get("error_description")
            or data.get("error")
            or f"token endpoint returned HTTP {response.status_code}"
        )
        raise OAuthExchangeError(str(detail))
    return data


async def resolve_onedrive_drive(
    access_token: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, str]:
    async def _get(http: httpx.AsyncClient) -> httpx.Response:
        return await http.get(
            "https://graph.microsoft.com/v1.0/me/drive",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    try:
        if client is None:
            async with httpx.AsyncClient(timeout=30.0) as http:
                response = await _get(http)
        else:
            response = await _get(client)
    except httpx.HTTPError as error:
        raise OAuthExchangeError(str(error)) from error

    try:
        data = response.json()
    except ValueError as error:
        raise OAuthExchangeError(
            "Microsoft Graph returned invalid drive data",
        ) from error
    if response.status_code >= 400 or not isinstance(data, dict) or not data.get("id"):
        api_error = data.get("error") if isinstance(data, dict) else None
        detail = api_error.get("message") if isinstance(api_error, dict) else None
        raise OAuthExchangeError(
            str(detail or f"Microsoft Graph returned HTTP {response.status_code}"),
        )
    return {"id": str(data["id"]), "driveType": str(data.get("driveType", ""))}


def resolve_callback_tokens(
    parsed: ParsedCallback,
    *,
    expected_state: str | None,
    store: OAuthStateStore,
    provider_type: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate CSRF state and return (ready_token | None, code | None).

    Token-JSON pastes skip state validation. Code pastes require a matching
    pending state (from body or the pasted URL).
    """
    if parsed.token is not None:
        return parsed.token, None

    state = expected_state or parsed.state
    if not state:
        raise OAuthCallbackError("OAuth state is required")
    if not store.consume(state, provider_type=provider_type):
        raise OAuthCallbackError("OAuth state is invalid or expired")
    if not parsed.code:
        raise OAuthCallbackError("Callback is missing an authorization code")
    return None, parsed.code
