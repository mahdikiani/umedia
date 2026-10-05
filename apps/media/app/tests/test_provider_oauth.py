"""Google Drive OAuth paste-flow unit + service tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest

from apps.provider_connections.oauth import (
    GoogleOAuthCredentials,
    OAuthCallbackError,
    OAuthConfigurationError,
    OAuthExchangeError,
    OAuthStateStore,
    build_auth_url,
    exchange_code,
    parse_callback,
    resolve_onedrive_drive,
    serialize_rclone_token,
)
from apps.provider_connections.oauth_service import (
    ProviderOAuthService,
    UnsupportedOAuthProvider,
)
from apps.provider_connections.services import ProviderConnectionService
from plugins.manifest import ConfigField, PluginManifest

CREDS = GoogleOAuthCredentials(
    client_id="client-id",
    client_secret="client-secret",
    redirect_uri="http://localhost",
)


def test_parse_callback_full_url() -> None:
    parsed = parse_callback(
        "http://localhost/?code=abc123&state=xyz&scope=drive",
    )
    assert parsed.code == "abc123"
    assert parsed.state == "xyz"
    assert parsed.token is None


def test_parse_callback_url_with_hash_query() -> None:
    parsed = parse_callback("http://localhost/#code=fromhash&state=s1")
    assert parsed.code == "fromhash"
    assert parsed.state == "s1"


def test_parse_callback_bare_query_string() -> None:
    parsed = parse_callback("code=thecode&state=thest")
    assert parsed.code == "thecode"
    assert parsed.state == "thest"


def test_parse_callback_bare_code() -> None:
    parsed = parse_callback("4/0AeanS...")
    assert parsed.code == "4/0AeanS..."
    assert parsed.state is None


def test_parse_callback_token_json_object() -> None:
    blob = {
        "access_token": "ya29.a",
        "token_type": "Bearer",
        "refresh_token": "1//r",
        "expires_in": 3600,
    }
    parsed = parse_callback(json.dumps(blob))
    assert parsed.token is not None
    assert parsed.token["access_token"] == "ya29.a"
    assert parsed.code is None


def test_parse_callback_token_json_string_wrapped() -> None:
    inner = json.dumps({"access_token": "tok", "token_type": "Bearer"})
    parsed = parse_callback(json.dumps(inner))
    assert parsed.token is not None
    assert parsed.token["access_token"] == "tok"


def test_parse_callback_rejects_empty() -> None:
    with pytest.raises(OAuthCallbackError):
        parse_callback("   ")


def test_build_auth_url_includes_required_params() -> None:
    url = build_auth_url(CREDS, state="state-1")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=client-id" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost" in url
    assert "response_type=code" in url
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "state=state-1" in url
    assert "googleapis.com%2Fauth%2Fdrive" in url


def test_build_auth_url_requires_credentials() -> None:
    with pytest.raises(OAuthConfigurationError):
        build_auth_url(
            GoogleOAuthCredentials(client_id="", client_secret=""),
            state="x",
        )


@pytest.mark.parametrize(
    ("provider_type", "host", "scope"),
    [
        ("onedrive", "login.microsoftonline.com", "offline_access"),
        ("dropbox", "www.dropbox.com", "token_access_type=offline"),
    ],
)
def test_build_auth_url_supports_microsoft_and_dropbox(
    provider_type: str,
    host: str,
    scope: str,
) -> None:
    credentials = GoogleOAuthCredentials(
        client_id="client-id",
        client_secret="client-secret",
        provider_type=provider_type,
    )
    url = build_auth_url(credentials, state="csrf-state")
    assert host in url
    assert scope in url
    assert "state=csrf-state" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost" in url


def test_serialize_rclone_token_computes_expiry_from_expires_in() -> None:
    now = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
    blob = serialize_rclone_token(
        {
            "access_token": "a",
            "refresh_token": "r",
            "token_type": "Bearer",
            "expires_in": 3600,
        },
        now=now,
    )
    parsed = json.loads(blob)
    assert parsed["access_token"] == "a"
    assert parsed["refresh_token"] == "r"
    assert parsed["token_type"] == "Bearer"
    assert parsed["expiry"] == "2026-08-20T13:00:00Z"


def test_serialize_rclone_token_requires_refresh_token() -> None:
    with pytest.raises(OAuthCallbackError, match="refresh_token"):
        serialize_rclone_token({"access_token": "a", "expires_in": 60})


def test_oauth_state_store_consume_once() -> None:
    store = OAuthStateStore()
    state = store.create("google_drive")
    assert store.consume(state, provider_type="google_drive") is True
    assert store.consume(state, provider_type="google_drive") is False


def test_oauth_state_store_rejects_wrong_provider() -> None:
    store = OAuthStateStore()
    state = store.create("google_drive")
    assert store.consume(state, provider_type="s3") is False


@pytest.mark.asyncio
async def test_exchange_code_posts_form_and_returns_json() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 3599,
                "token_type": "Bearer",
            },
        ),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        data = await exchange_code(CREDS, code="auth-code", client=client)
    assert data["access_token"] == "access"
    assert data["refresh_token"] == "refresh"


@pytest.mark.asyncio
async def test_exchange_code_raises_on_error_response() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            400,
            json={"error": "invalid_grant", "error_description": "bad code"},
        ),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(OAuthExchangeError, match="bad code"):
            await exchange_code(CREDS, code="bad", client=client)


@pytest.mark.asyncio
async def test_resolve_onedrive_drive_uses_graph_me_drive() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://graph.microsoft.com/v1.0/me/drive"
        assert request.headers["Authorization"] == "Bearer graph-token"
        return httpx.Response(
            200,
            json={"id": "b!drive", "driveType": "business"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        drive = await resolve_onedrive_drive("graph-token", client=client)
    assert drive == {"id": "b!drive", "driveType": "business"}


def _drive_manifest() -> PluginManifest:
    return PluginManifest(
        id="google_drive",
        name="google-drive",
        description="Drive",
        entrypoint=["python", "-m", "plugins.rclone.main"],
        process_id="rclone",
        remote_type="google_drive",
        connect_flow="oauth",
        config_fields=(
            ConfigField(
                key="token",
                label="OAuth token",
                required=False,
                secret=True,
            ),
            ConfigField(
                key="client_id",
                label="Client ID",
                required=False,
                secret=True,
            ),
            ConfigField(
                key="client_secret",
                label="Client secret",
                required=False,
                secret=True,
            ),
            ConfigField(
                key="root_folder_id",
                label="Root folder ID",
                required=False,
            ),
        ),
        capabilities=("list", "read", "write", "delete", "move", "copy"),
    )


class _FakeRepo:
    def __init__(self) -> None:
        self.created: dict | None = None

    async def create(self, data: dict) -> dict:
        self.created = data
        return data | {
            "uid": "conn-1",
            "enabled": True,
            "status": "configured",
            "created_at": datetime(2026, 8, 20, tzinfo=UTC),
            "last_tested_at": None,
            "last_error": None,
            "import_existing": data.get("import_existing", False),
            "mirror_structure": data.get("mirror_structure", False),
        }

    async def list(self, *, owner_id: str | None = None) -> list:
        return []

    async def get(self, uid: str, *, owner_id: str | None = None) -> None:
        return None

    async def update(
        self,
        uid: str,
        changes: dict,
        *,
        owner_id: str | None = None,
    ) -> None:
        return None


class _FakeCipher:
    def encrypt_json(self, value: dict) -> str:
        return "enc:" + json.dumps(value, sort_keys=True)


class _FakeRegistry:
    def get(self, provider_type: str) -> PluginManifest | None:
        if provider_type in {"google_drive", "onedrive", "dropbox"}:
            return PluginManifest(
                id=provider_type,
                name=provider_type,
                description=provider_type,
                entrypoint=["python", "-m", "plugins.rclone.main"],
                process_id="rclone",
                remote_type=provider_type,
                connect_flow="oauth",
                config_fields=(
                    ConfigField(
                        key="token",
                        label="OAuth token",
                        required=False,
                        secret=True,
                    ),
                    ConfigField(
                        key="client_id",
                        label="Client ID",
                        required=False,
                        secret=True,
                    ),
                    ConfigField(
                        key="client_secret",
                        label="Client secret",
                        required=False,
                        secret=True,
                    ),
                    ConfigField(
                        key="drive_id",
                        label="Drive ID",
                        required=False,
                    ),
                    ConfigField(
                        key="drive_type",
                        label="Drive type",
                        required=False,
                    ),
                    ConfigField(
                        key="root_folder_id",
                        label="Root folder ID",
                        required=False,
                    ),
                ),
            )
        return None


def _oauth_service(
    *,
    connect: AsyncMock | None = None,
    credentials: GoogleOAuthCredentials = CREDS,
) -> tuple[ProviderOAuthService, OAuthStateStore, _FakeRepo]:
    repo = _FakeRepo()
    store = OAuthStateStore()
    connections = ProviderConnectionService(
        repo,
        _FakeCipher(),
        _FakeRegistry(),
        connect or AsyncMock(return_value=None),
    )
    return (
        ProviderOAuthService(
            credentials=credentials,
            state_store=store,
            connections=connections,
        ),
        store,
        repo,
    )


def test_oauth_start_requires_configuration() -> None:
    service, _, _ = _oauth_service(
        credentials=GoogleOAuthCredentials(client_id="", client_secret=""),
    )
    with pytest.raises(OAuthConfigurationError):
        service.start(provider_type="google_drive")


def test_oauth_start_rejects_unknown_provider() -> None:
    service, _, _ = _oauth_service()
    with pytest.raises(UnsupportedOAuthProvider):
        service.start(provider_type="s3")


def test_oauth_start_returns_url_and_stores_state() -> None:
    service, store, _ = _oauth_service()
    started = service.start(provider_type="google_drive")
    assert started["provider_type"] == "google_drive"
    assert started["redirect_uri"] == "http://localhost"
    assert "accounts.google.com" in started["authorization_url"]
    assert store.consume(started["state"], provider_type="google_drive")


@pytest.mark.parametrize("provider_type", ["onedrive", "dropbox"])
def test_oauth_service_start_uses_provider_credentials(provider_type: str) -> None:
    service, _, _ = _oauth_service()
    service._provider_credentials[provider_type] = GoogleOAuthCredentials(
        client_id=f"{provider_type}-id",
        client_secret=f"{provider_type}-secret",
        provider_type=provider_type,
    )
    started = service.start(provider_type=provider_type)
    assert started["provider_type"] == provider_type
    assert started["authorization_url"]
    assert started["redirect_uri"] == "http://localhost"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", ["onedrive", "dropbox"])
async def test_oauth_complete_creates_cloud_connection(
    provider_type: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, store, repo = _oauth_service()
    service._provider_credentials[provider_type] = GoogleOAuthCredentials(
        client_id=f"{provider_type}-id",
        client_secret=f"{provider_type}-secret",
        provider_type=provider_type,
    )
    state = store.create(provider_type)

    async def fake_exchange(
        credentials: GoogleOAuthCredentials,
        *,
        code: str,
        client: httpx.AsyncClient | None = None,
    ) -> dict:
        assert credentials.provider_type == provider_type
        assert code == "provider-code"
        return {
            "access_token": "provider-access",
            "refresh_token": "provider-refresh",
            "expires_in": 3600,
        }

    async def fake_drive(_token: str) -> dict[str, str]:
        return {"id": "drive-123", "driveType": "business"}

    monkeypatch.setattr(
        "apps.provider_connections.oauth_service.exchange_code",
        fake_exchange,
    )
    monkeypatch.setattr(
        "apps.provider_connections.oauth_service.resolve_onedrive_drive",
        fake_drive,
    )
    await service.complete(
        provider_type=provider_type,
        name=f"{provider_type.replace('_', '-')}-storage",
        callback=f"code=provider-code&state={state}",
        state=state,
        owner_id="user-1",
    )

    assert repo.created is not None
    config = json.loads(repo.created["encrypted_config"][4:])
    assert config["client_id"] == f"{provider_type}-id"
    assert json.loads(config["token"])["refresh_token"] == "provider-refresh"
    if provider_type == "onedrive":
        assert config["drive_id"] == "drive-123"
        assert config["drive_type"] == "business"


@pytest.mark.asyncio
async def test_oauth_complete_exchanges_code_and_creates_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, store, repo = _oauth_service()
    state = store.create("google_drive")

    async def fake_exchange(
        credentials: GoogleOAuthCredentials,
        *,
        code: str,
        client: httpx.AsyncClient | None = None,
    ) -> dict:
        assert code == "pasted-code"
        assert credentials.client_id == "client-id"
        return {
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

    monkeypatch.setattr(
        "apps.provider_connections.oauth_service.exchange_code",
        fake_exchange,
    )

    connection = await service.complete(
        provider_type="google_drive",
        name="my-drive",
        callback=f"http://localhost/?code=pasted-code&state={state}",
        state=state,
        root_folder_id="root-1",
        import_existing=True,
        owner_id="user-1",
    )
    assert connection["uid"] == "conn-1"
    assert repo.created is not None
    assert repo.created["provider_type"] == "google_drive"
    assert repo.created["name"] == "my-drive"
    assert repo.created["import_existing"] is True
    encrypted = repo.created["encrypted_config"]
    assert "access" in encrypted
    assert "client-id" in encrypted
    assert "client-secret" in encrypted
    assert "root-1" in encrypted


@pytest.mark.asyncio
async def test_oauth_complete_accepts_token_json_without_state() -> None:
    service, _, repo = _oauth_service()
    connection = await service.complete(
        provider_type="google_drive",
        name="drive",
        callback=json.dumps(
            {
                "access_token": "ya29",
                "refresh_token": "1//x",
                "token_type": "Bearer",
                "expires_in": 60,
            },
        ),
        owner_id="user-1",
    )
    assert connection["uid"] == "conn-1"
    assert repo.created is not None
    assert "ya29" in repo.created["encrypted_config"]


@pytest.mark.asyncio
async def test_oauth_complete_rejects_state_mismatch() -> None:
    service, store, _ = _oauth_service()
    store.create("google_drive")
    with pytest.raises(OAuthCallbackError, match="invalid or expired"):
        await service.complete(
            provider_type="google_drive",
            name="drive",
            callback="http://localhost/?code=c&state=wrong-state",
            state="wrong-state",
            owner_id="user-1",
        )


# ---------------------------------------------------------------------------
# HTTP route tests (real app; mock plugin connect + token exchange)
# ---------------------------------------------------------------------------


async def _authenticated(client: httpx.AsyncClient) -> None:
    credentials = {"email": "admin@example.com", "password": "a secure first password"}
    state = (await client.get("/auth/state")).json()
    if state["configured"]:
        response = await client.post("/auth/sessions", json=credentials)
    else:
        response = await client.post("/auth/setup", json=credentials)
    assert response.status_code == 201, response.text


def _configure_oauth(client: httpx.AsyncClient) -> None:
    settings = client._transport.app.state.settings  # type: ignore[attr-defined]
    settings.google_oauth_client_id = "test-client-id"
    settings.google_oauth_client_secret = "test-client-secret"
    settings.google_oauth_redirect_uri = "http://localhost"


@pytest.mark.asyncio
async def test_cloud_oauth_start_routes_use_environment_credentials(
    client: httpx.AsyncClient,
) -> None:
    await _authenticated(client)
    settings = client._transport.app.state.settings  # type: ignore[attr-defined]
    settings.onedrive_oauth_client_id = "onedrive-id"
    settings.onedrive_oauth_client_secret = "onedrive-secret"
    settings.dropbox_oauth_client_id = "dropbox-id"
    settings.dropbox_oauth_client_secret = "dropbox-secret"

    for provider_type, host in (
        ("onedrive", "login.microsoftonline.com"),
        ("dropbox", "www.dropbox.com"),
    ):
        response = await client.post(
            "/providers/oauth/start",
            json={"provider_type": provider_type},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert host in body["authorization_url"]
        assert body["state"]


@pytest.mark.asyncio
async def test_oauth_start_route_requires_config(
    client: httpx.AsyncClient,
) -> None:
    await _authenticated(client)
    settings = client._transport.app.state.settings  # type: ignore[attr-defined]
    settings.google_oauth_client_id = ""
    settings.google_oauth_client_secret = ""

    response = await client.post(
        "/providers/oauth/start",
        json={"provider_type": "google_drive"},
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "oauth_not_configured"


@pytest.mark.asyncio
async def test_oauth_start_and_complete_routes(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _authenticated(client)
    _configure_oauth(client)

    # Avoid talking to a real rclone google_drive connect during create.
    async def fake_connect(_manifest: object, _config: dict) -> None:
        return None

    monkeypatch.setattr(
        "apps.provider_connections.services.build_plugin_connector",
        lambda _pm: fake_connect,
    )
    # Re-bind is awkward because routes build the connector per request —
    # patch ProviderConnectionService.create's connect via the service's
    # _connect by swapping the connector factory used in routes.
    from apps.provider_connections import routes as routes_mod

    original_service = routes_mod._service

    def patched_service(request: object) -> ProviderConnectionService:
        service = original_service(request)
        service._connect = fake_connect
        return service

    monkeypatch.setattr(routes_mod, "_service", patched_service)

    async def fake_exchange(
        credentials: GoogleOAuthCredentials,
        *,
        code: str,
        client: httpx.AsyncClient | None = None,
    ) -> dict:
        assert code == "route-code"
        return {
            "access_token": "route-access",
            "refresh_token": "route-refresh",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

    monkeypatch.setattr(
        "apps.provider_connections.oauth_service.exchange_code",
        fake_exchange,
    )

    start = await client.post(
        "/providers/oauth/start",
        json={"provider_type": "google_drive"},
    )
    assert start.status_code == 200, start.text
    body = start.json()
    assert body["authorization_url"]
    assert body["state"]
    assert body["redirect_uri"] == "http://localhost"

    complete = await client.post(
        "/providers/oauth/complete",
        json={
            "provider_type": "google_drive",
            "name": "gdrive",
            "callback": f"code=route-code&state={body['state']}",
            "state": body["state"],
            "import_existing": False,
            "mirror_structure": False,
        },
    )
    assert complete.status_code == 201, complete.text
    created = complete.json()
    assert created["provider_type"] == "google_drive"
    assert created["name"] == "gdrive"
    assert created["status"] == "configured"
