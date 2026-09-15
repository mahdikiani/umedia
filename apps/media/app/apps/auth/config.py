"""usso.lite configuration for the single-administrator installation."""

from usso.lite import LiteConfig, OidcProviderConfig

from server.config import Settings

#: 24h access tokens -- matches the previous hand-rolled session TTL. The
#: browser renews them through `GET /auth/refresh` (see `routes.py` and
#: apps/web/lib/api.ts) when an API call returns 401; refresh cookies
#: live for `REFRESH_TOKEN_DAYS`.
ACCESS_TOKEN_MINUTES = 60 * 24
REFRESH_TOKEN_DAYS = 30


def resolve_oidc_redirect_uri(settings: Settings) -> str:
    """Redirect URI for identity OIDC (may differ from Drive OAuth paste flow)."""
    if settings.google_oidc_redirect_uri:
        return settings.google_oidc_redirect_uri
    return settings.google_oauth_redirect_uri or "http://localhost"


def build_lite_config(settings: Settings) -> LiteConfig:
    """Build the usso.lite configuration for this installation.

    Registration is always disabled: UMedia is single-tenant with exactly
    one bootstrap administrator, created once through `POST /auth/setup`
    (see `services.py`), never through usso.lite's own public register flow.

    When Google OAuth client credentials are set (same env vars as Drive
    storage OAuth), they also enable identity OIDC login via openid/email/
    profile scopes — separate from Drive scopes in provider_connections.
    """
    oidc_providers: dict[str, OidcProviderConfig] = {}
    if settings.google_oauth_client_id and settings.google_oauth_client_secret:
        oidc_providers["google"] = OidcProviderConfig(
            client_id=settings.google_oauth_client_id,
            client_secret=settings.google_oauth_client_secret,
            redirect_uri=resolve_oidc_redirect_uri(settings),
        )
    return LiteConfig(
        database_url=settings.database_uri,
        issuer="umedia",
        audience="umedia",
        access_token_minutes=ACCESS_TOKEN_MINUTES,
        refresh_token_days=REFRESH_TOKEN_DAYS,
        allow_registration=False,
        oidc_providers=oidc_providers,
        oidc_allow_signup=False,
        oidc_default_roles=["user"],
    )
