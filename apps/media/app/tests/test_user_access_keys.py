"""Per-user S3-style access key pairs (`apps/user_access_keys`).

Each user owns key pairs: a public `access_key_id` (carried in SigV4
credentials) and a secret that exists in plaintext only in memory -- at
rest it is a Fernet token under the installation `CredentialCipher`.
Temporary share links are SigV4-presigned with the *user's* secret
(tests/test_signed_links + test_s3_api cover that side); here we test
the key lifecycle itself:

- generation format (`um_` prefix, urlsafe, unique) and encryption at rest;
- `ensure_default_key` idempotency + lazy re-creation after revocation;
- `get_active_key` hiding missing/inactive keys;
- every new user (bootstrap admin and `create_user`) gets a default key.
"""

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from apps.user_access_keys.repository import UserAccessKeyRepository
from apps.user_access_keys.services import (
    ACCESS_KEY_ID_PREFIX,
    UserAccessKeyService,
)
from utils.crypto import CredentialCipher

USER_ID = "user-1"
OTHER_USER_ID = "user-2"


@pytest_asyncio.fixture
async def service(tmp_path: Path) -> AsyncGenerator[UserAccessKeyService]:
    """Real SQLite repository + a throwaway Fernet cipher."""
    from fastapi_mongo_base.sql.models import BaseEntity

    from server.database import create_engine, create_session_factory

    engine = create_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'access-keys.sqlite3'}",
    )
    async with engine.begin() as conn:
        await conn.run_sync(BaseEntity.metadata.create_all)
    cipher = CredentialCipher(Fernet.generate_key())
    yield UserAccessKeyService(
        UserAccessKeyRepository(create_session_factory(engine)), cipher,
    )
    await engine.dispose()


# ----------------------------------------------------------------------
# CredentialCipher string mode (what encrypted_secret is stored with)
# ----------------------------------------------------------------------


def test_cipher_encrypts_and_decrypts_plain_strings() -> None:
    cipher = CredentialCipher(Fernet.generate_key())
    encrypted = cipher.encrypt("a raw secret")

    assert encrypted != "a raw secret"
    assert cipher.decrypt(encrypted) == "a raw secret"


# ----------------------------------------------------------------------
# Key generation + encryption at rest
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_key_generates_an_s3_style_pair(
    service: UserAccessKeyService,
) -> None:
    created = await service.create_key(USER_ID)
    key = created.record

    assert key.user_id == USER_ID
    assert key.access_key_id.startswith(ACCESS_KEY_ID_PREFIX)
    # `um_` + urlsafe(16..20 bytes) -- long enough to be unguessable.
    assert len(key.access_key_id) >= len(ACCESS_KEY_ID_PREFIX) + 20
    assert key.label == "default"
    assert key.is_active is True
    assert created.secret_access_key == service.decrypt_secret_str(key)


@pytest.mark.asyncio
async def test_secret_is_encrypted_at_rest_and_long_enough(
    service: UserAccessKeyService,
) -> None:
    created = await service.create_key(USER_ID)
    key = created.record
    secret = service.decrypt_secret(key)
    secret_text = service.decrypt_secret_str(key)

    assert isinstance(secret, bytes)
    assert isinstance(secret_text, str)
    assert secret_text == secret.decode()
    # urlsafe of >= 32 random bytes.
    assert len(secret) >= 40
    # The stored value is a Fernet token, never the raw secret.
    assert key.encrypted_secret.encode() != secret
    assert secret.decode() not in key.encrypted_secret


@pytest.mark.asyncio
async def test_key_ids_and_secrets_are_unique_per_key(
    service: UserAccessKeyService,
) -> None:
    first = await service.create_key(USER_ID)
    second = await service.create_key(OTHER_USER_ID)

    assert first.record.access_key_id != second.record.access_key_id
    assert first.secret_access_key != second.secret_access_key


@pytest.mark.asyncio
async def test_list_for_user_returns_active_and_revoked_keys_newest_first(
    service: UserAccessKeyService,
) -> None:
    first = await service.create_key(USER_ID, label="first")
    second = await service.create_key(USER_ID, label="second")
    await service.create_key(OTHER_USER_ID, label="other")
    await service.deactivate(first.record.uid)

    listed = await service.list_for_user(USER_ID)

    assert [key.uid for key in listed] == [second.record.uid, first.record.uid]
    assert [key.is_active for key in listed] == [True, False]


@pytest.mark.asyncio
async def test_deactivate_for_user_rejects_a_key_owned_by_someone_else(
    service: UserAccessKeyService,
) -> None:
    created = await service.create_key(OTHER_USER_ID)

    result = await service.deactivate_for_user(created.record.uid, USER_ID)

    assert result is None
    assert await service.get_active_key(created.record.access_key_id) is not None


@pytest.mark.asyncio
async def test_deactivate_for_user_revokes_an_owned_key(
    service: UserAccessKeyService,
) -> None:
    created = await service.create_key(USER_ID)

    result = await service.deactivate_for_user(created.record.uid, USER_ID)

    assert result is not None
    assert result.is_active is False
    assert await service.get_active_key(created.record.access_key_id) is None


# ----------------------------------------------------------------------
# ensure_default_key: create-if-missing, reuse otherwise
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_default_key_is_idempotent(
    service: UserAccessKeyService,
) -> None:
    assert await service.find_default_key(USER_ID) is None

    created = await service.ensure_default_key(USER_ID)
    again = await service.ensure_default_key(USER_ID)

    assert again.uid == created.uid
    assert again.access_key_id == created.access_key_id


@pytest.mark.asyncio
async def test_ensure_default_key_replaces_a_revoked_key(
    service: UserAccessKeyService,
) -> None:
    """Deactivating the only key must not lock the user out of minting:
    the next ensure creates a fresh pair (the revoked one stays dead)."""
    old = await service.ensure_default_key(USER_ID)
    await service.deactivate(old.uid)

    fresh = await service.ensure_default_key(USER_ID)

    assert fresh.uid != old.uid
    assert fresh.access_key_id != old.access_key_id
    assert fresh.is_active is True
    assert await service.get_active_key(old.access_key_id) is None


# ----------------------------------------------------------------------
# get_active_key: the verify-side lookup
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_active_key_round_trips(
    service: UserAccessKeyService,
) -> None:
    created = await service.create_key(USER_ID)
    loaded = await service.get_active_key(created.record.access_key_id)

    assert loaded is not None
    assert loaded.uid == created.record.uid
    assert loaded.user_id == USER_ID
    assert service.decrypt_secret(loaded) == service.decrypt_secret(created.record)


@pytest.mark.asyncio
async def test_get_active_key_hides_missing_and_inactive_keys(
    service: UserAccessKeyService,
) -> None:
    created = await service.create_key(USER_ID)

    assert await service.get_active_key("um_never-issued") is None

    await service.deactivate(created.record.uid)
    assert await service.get_active_key(created.record.access_key_id) is None


# ----------------------------------------------------------------------
# Lifecycle wiring: every new user gets a default key
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_new_user_gets_a_default_key(
    tmp_path: Path, service: UserAccessKeyService,
) -> None:
    """Both creation paths -- the bootstrap `setup` admin and an admin's
    `create_user` -- must leave the account with one active key pair."""
    from usso.lite import LiteConfig

    from apps.auth.services import AuthService

    auth = AuthService(
        LiteConfig(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'auth.sqlite3'}",
            issuer="umedia-test",
            audience="umedia-test",
            access_token_minutes=60,
            refresh_token_days=1,
            allow_registration=False,
        ),
        access_keys=service,
    )
    await auth.ensure_initialized()
    try:
        _, admin = await auth.setup(
            "admin@example.com",
            "a secure first password",
            user_agent=None,
            ip=None,
        )
        admin_key = await service.find_default_key(admin.uid)
        assert admin_key is not None
        assert admin_key.is_active is True

        member = await auth.create_user(
            email="member@example.com",
            password="another good password",
            role="user",
        )
        member_key = await service.find_default_key(member.uid)
        assert member_key is not None
        assert member_key.access_key_id != admin_key.access_key_id
    finally:
        await auth.dispose()
