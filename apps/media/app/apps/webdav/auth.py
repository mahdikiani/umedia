"""WebDAV credentials.

Basic: username = the user's access key id (`um_...`), password = its
secret -- the same pair the S3 gateway uses, shown once on creation and
revocable on the Access keys page. The account's login password is never
accepted here. Bearer: a normal usso-lite access token (embedding hosts).
"""

import base64
import binascii
import hmac
from dataclasses import dataclass

from fastapi import Request
from usso.exceptions import USSOException
from usso.lite.dependency import resolve_current_user

from apps.user_access_keys.factory import build_user_access_key_service_from_state

CHALLENGE = 'Basic realm="UMedia", charset="UTF-8"'


class UnauthorizedError(Exception):
    """Missing or wrong credentials."""


@dataclass(frozen=True)
class DavIdentity:
    user_id: str
    is_admin: bool


def _basic_credentials(value: str) -> tuple[str, str]:
    try:
        decoded = base64.b64decode(value, validate=True).decode()
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise UnauthorizedError from exc
    username, separator, password = decoded.partition(":")
    if not separator or not username or not password:
        raise UnauthorizedError
    return username, password


async def _identity_for_user(request: Request, user_id: str) -> DavIdentity:
    summary = await request.app.state.auth_service.get_user_summary(user_id)
    if summary is None or not summary.is_active:
        raise UnauthorizedError
    return DavIdentity(user_id=user_id, is_admin="admin" in (summary.roles or []))


async def _from_basic(request: Request, value: str) -> DavIdentity:
    key_id, secret = _basic_credentials(value)
    service = build_user_access_key_service_from_state(request.app.state)
    key = await service.get_active_key(key_id)
    if key is None:
        raise UnauthorizedError
    expected = service.decrypt_secret_str(key)
    if not hmac.compare_digest(expected.encode(), secret.encode()):
        raise UnauthorizedError
    return await _identity_for_user(request, key.user_id)


async def _from_bearer(request: Request) -> DavIdentity:
    service = request.app.state.auth_service
    async with service.database.session_maker() as session:
        try:
            user = await resolve_current_user(request, session, service.lite_auth)
        except USSOException as exc:
            raise UnauthorizedError from exc
    return DavIdentity(user_id=user.uid, is_admin="admin" in (user.roles or []))


async def authenticate(request: Request) -> DavIdentity:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    scheme = scheme.lower()
    if scheme == "basic":
        return await _from_basic(request, value.strip())
    if scheme == "bearer":
        return await _from_bearer(request)
    raise UnauthorizedError
