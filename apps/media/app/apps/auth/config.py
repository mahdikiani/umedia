"""usso.lite configuration for the single-administrator installation."""

from usso.lite import LiteConfig

from server.config import Settings

#: 24h access tokens -- matches the previous hand-rolled session TTL, and
#: keeps the request-time auth check (`middleware.py`) a single cookie
#: verification with no silent-refresh logic to get wrong. A refresh-token
#: cookie is still issued and kept (see `routes.py`) so a longer-lived
#: "remember me" / silent-refresh flow can build on it later without a
#: cookie-shape migration.
ACCESS_TOKEN_MINUTES = 60 * 24
REFRESH_TOKEN_DAYS = 30


def build_lite_config(settings: Settings) -> LiteConfig:
    """Build the usso.lite configuration for this installation.

    Registration is always disabled: UMedia is single-tenant with exactly
    one bootstrap administrator, created once through `POST /auth/setup`
    (see `services.py`), never through usso.lite's own public register flow.
    """
    return LiteConfig(
        database_url=settings.database_uri,
        issuer="umedia",
        audience="umedia",
        access_token_minutes=ACCESS_TOKEN_MINUTES,
        refresh_token_days=REFRESH_TOKEN_DAYS,
        allow_registration=False,
    )
