"""OAuth connect-flow orchestration (business logic, not routes)."""

from __future__ import annotations

from typing import Any

from fastapi_mongo_base.core.exceptions import BaseHTTPException

from .oauth import (
    SUPPORTED_OAUTH_PROVIDERS,
    GoogleOAuthCredentials,
    OAuthCallbackError,
    OAuthConfigurationError,
    OAuthStateStore,
    build_auth_url,
    exchange_code,
    parse_callback,
    resolve_callback_tokens,
    serialize_rclone_token,
)
from .services import ProviderConnectionService


class UnsupportedOAuthProvider(BaseHTTPException):
    def __init__(self, provider_type: str) -> None:
        detail = f"OAuth is not supported for provider type '{provider_type}'"
        super().__init__(
            status_code=422,
            error_code="unsupported_oauth_provider",
            detail=detail,
            message=detail,
        )


class ProviderOAuthService:
    """Start/complete the localhost-redirect paste OAuth flow."""

    def __init__(
        self,
        *,
        credentials: GoogleOAuthCredentials,
        state_store: OAuthStateStore,
        connections: ProviderConnectionService,
    ) -> None:
        self._credentials = credentials
        self._states = state_store
        self._connections = connections

    def start(self, *, provider_type: str) -> dict[str, str]:
        if provider_type not in SUPPORTED_OAUTH_PROVIDERS:
            raise UnsupportedOAuthProvider(provider_type)
        if not self._credentials.configured:
            raise OAuthConfigurationError()
        state = self._states.create(provider_type)
        return {
            "provider_type": provider_type,
            "authorization_url": build_auth_url(
                self._credentials, state=state,
            ),
            "state": state,
            "redirect_uri": self._credentials.redirect_uri,
        }

    async def complete(
        self,
        *,
        provider_type: str,
        name: str,
        callback: str,
        state: str | None = None,
        root_folder_id: str | None = None,
        import_existing: bool = False,
        mirror_structure: bool = False,
        owner_id: str,
        is_admin: bool = False,
    ) -> object:
        if provider_type not in SUPPORTED_OAUTH_PROVIDERS:
            raise UnsupportedOAuthProvider(provider_type)
        if not self._credentials.configured:
            raise OAuthConfigurationError()

        parsed = parse_callback(callback)
        token_dict, code = resolve_callback_tokens(
            parsed,
            expected_state=state,
            store=self._states,
            provider_type=provider_type,
        )
        if token_dict is None:
            if not code:
                raise OAuthCallbackError("Callback is missing an authorization code")
            token_dict = await exchange_code(self._credentials, code=code)

        token_blob = serialize_rclone_token(token_dict)
        config: dict[str, Any] = {
            "token": token_blob,
            "client_id": self._credentials.client_id,
            "client_secret": self._credentials.client_secret,
        }
        if root_folder_id:
            config["root_folder_id"] = root_folder_id

        return await self._connections.create(
            provider_type=provider_type,
            name=name,
            config=config,
            owner_id=owner_id,
            is_admin=is_admin,
            import_existing=import_existing,
            mirror_structure=mirror_structure,
        )
