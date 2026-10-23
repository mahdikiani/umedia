"""Token-only authentication for installations that run inside a USSO setup.

UMedia normally runs its own accounts (`usso.lite`, see `services.py`). When
it is deployed as a building block of a larger product that already signs
users in with USSO, that product's token is enough: this module verifies the
USSO access token (cookie, `Authorization` header or API key) against the
USSO server's JWKS and turns it into the same minimal user object the rest of
the app reads from `request.state.user` (`uid`, `roles`, `name`, ...).

Nothing here stores users. Their files, connections and tokens are keyed by
the USSO user id (`sub`), and provider tokens (Drive, ...) stay encrypted in
UMedia's own database; they are never merged with USSO.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Protocol

from fastapi import Request
from usso import AuthConfig
from usso.exceptions import USSOException
from usso.integrations.fastapi import USSOAuthentication
from usso.user import UserData

from server.config import Settings


class UssoTokenVerifier(Protocol):
    """The part of `USSOAuthentication` this module relies on."""

    def usso_access_security(self, request: Request) -> UserData | None: ...


@dataclass(frozen=True)
class SessionUser:
    """The signed-in user as UMedia needs to see them."""

    uid: str
    roles: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    name: str | None = None
    email: str = ""
    tenant_id: str | None = None
    workspace_id: str | None = None
    is_active: bool = True


def build_verifier(base_url: str) -> UssoTokenVerifier:
    """A verifier that resolves signing keys from the USSO server."""
    base = base_url.rstrip("/")
    return USSOAuthentication(
        jwt_config=AuthConfig(jwks_url=f"{base}/.well-known/jwks.json"),
        from_usso_base_url=base,
        raise_exception=True,
    )


class UssoTokenAuthenticator:
    """Resolve a request's USSO credentials into a `SessionUser`."""

    def __init__(
        self,
        verifier: UssoTokenVerifier,
        *,
        tenant_id: str | None = None,
    ) -> None:
        self._verifier = verifier
        self._tenant_id = tenant_id

    async def resolve(self, request: Request) -> SessionUser:
        """Raise `USSOException` unless the request carries a valid identity.

        Verification can fetch the JWKS the first time, so it runs off the
        event loop.
        """
        data = await asyncio.to_thread(self._verifier.usso_access_security, request)
        uid = data.uid if data is not None else ""
        if data is None or not uid:
            raise USSOException(status_code=401, error_code="unauthorized")
        if self._tenant_id is not None and data.tenant_id != self._tenant_id:
            raise USSOException(status_code=403, error_code="wrong_tenant")
        claims = data.claims or {}
        return SessionUser(
            uid=uid,
            roles=list(data.roles or []),
            scopes=list(data.scopes or []),
            name=claims.get("name") or claims.get("display_name"),
            email=claims.get("email") or "",
            tenant_id=data.tenant_id,
            workspace_id=data.workspace_id,
        )


def build_usso_authenticator(settings: Settings) -> UssoTokenAuthenticator | None:
    """The authenticator for `UMEDIA_AUTH_MODE=usso`, else `None` (lite mode)."""
    if settings.auth_mode == "lite":
        return None
    if settings.auth_mode != "usso":
        msg = f"UMEDIA_AUTH_MODE must be 'lite' or 'usso', got {settings.auth_mode!r}"
        raise ValueError(msg)
    if not settings.usso_base_url:
        raise ValueError("UMEDIA_AUTH_MODE=usso requires USSO_BASE_URL")
    return UssoTokenAuthenticator(
        build_verifier(settings.usso_base_url),
        tenant_id=settings.usso_tenant_id,
    )
