"""Access-key business rules: generation, default resolution, revocation.

The invariants this module owns:

- **Secrets exist in plaintext only in memory.** Generation encrypts
  immediately (`CredentialCipher.encrypt`); `decrypt_secret` is the one
  door back for HMAC sign/verify. A plaintext secret may leave the process
  only in the create-key HTTP response, where it is shown once.
- **`default` is the user's oldest active key.** `ensure_default_key`
  creates one exactly when none is active -- this is also the lazy
  backfill for accounts that predate the access-key migration.
- **Deactivation is revocation.** A link's signature is only as alive as
  the key that made it; `get_active_key` never returns inactive keys, so
  the verify side fails closed.
"""

import secrets
from dataclasses import dataclass
from typing import Any, Protocol

from .schemas import UserAccessKeyRecord

ACCESS_KEY_ID_PREFIX = "um_"
#: Random bytes behind each half of the pair. 18 urlsafe bytes -> a
#: 24-char public id (within the spec'd 16-20 byte window); 32 bytes of
#: secret matches the usual HMAC-SHA256 key-length guidance.
ACCESS_KEY_ID_BYTES = 18
SECRET_BYTES = 32

DEFAULT_LABEL = "default"


@dataclass(frozen=True, slots=True)
class CreatedAccessKey:
    record: UserAccessKeyRecord
    secret_access_key: str


class CipherProtocol(Protocol):
    """The slice of `utils.crypto.CredentialCipher` this service uses."""

    def encrypt(self, value: str) -> str: ...
    def decrypt(self, token: str) -> str: ...


class UserAccessKeyRepositoryProtocol(Protocol):
    async def create(self, data: dict[str, Any]) -> UserAccessKeyRecord: ...
    async def get(self, uid: str) -> UserAccessKeyRecord | None: ...
    async def list_for_user(self, user_id: str) -> list[UserAccessKeyRecord]: ...
    async def get_by_access_key_id(
        self, access_key_id: str,
    ) -> UserAccessKeyRecord | None: ...
    async def first_active_for_user(
        self, user_id: str,
    ) -> UserAccessKeyRecord | None: ...
    async def set_active(
        self, uid: str, *, is_active: bool,
    ) -> UserAccessKeyRecord | None: ...


class UserAccessKeyService:
    def __init__(
        self,
        keys: UserAccessKeyRepositoryProtocol,
        cipher: CipherProtocol,
    ) -> None:
        self._keys = keys
        self._cipher = cipher

    async def create_key(
        self, user_id: str, *, label: str = DEFAULT_LABEL,
    ) -> CreatedAccessKey:
        """Generate and persist a fresh pair for `user_id`."""
        secret = secrets.token_urlsafe(SECRET_BYTES)
        record = await self._keys.create(
            {
                "user_id": user_id,
                "access_key_id": (
                    ACCESS_KEY_ID_PREFIX
                    + secrets.token_urlsafe(ACCESS_KEY_ID_BYTES)
                ),
                "encrypted_secret": self._cipher.encrypt(secret),
                "label": label,
                "is_active": True,
            },
        )
        return CreatedAccessKey(record=record, secret_access_key=secret)

    async def list_for_user(self, user_id: str) -> list[UserAccessKeyRecord]:
        return await self._keys.list_for_user(user_id)

    async def find_default_key(
        self, user_id: str,
    ) -> UserAccessKeyRecord | None:
        """The user's default (oldest active) key, or None."""
        return await self._keys.first_active_for_user(user_id)

    async def ensure_default_key(self, user_id: str) -> UserAccessKeyRecord:
        """The default key, created on the spot when the user has no
        active one -- the lazy backfill for pre-migration accounts."""
        existing = await self._keys.first_active_for_user(user_id)
        if existing is not None:
            return existing
        return (await self.create_key(user_id)).record

    async def get_active_key(
        self, access_key_id: str,
    ) -> UserAccessKeyRecord | None:
        """Resolve a public key id to its record -- None when unknown OR
        deactivated, so signature verification fails closed."""
        key = await self._keys.get_by_access_key_id(access_key_id)
        if key is None or not key.is_active:
            return None
        return key

    async def deactivate(self, uid: str) -> UserAccessKeyRecord | None:
        """Revoke a key: every link it ever signed stops verifying."""
        return await self._keys.set_active(uid, is_active=False)

    async def deactivate_for_user(
        self,
        uid: str,
        user_id: str,
    ) -> UserAccessKeyRecord | None:
        key = await self._keys.get(uid)
        if key is None or key.user_id != user_id:
            return None
        return await self._keys.set_active(uid, is_active=False)

    def decrypt_secret(self, key: UserAccessKeyRecord) -> bytes:
        """The raw secret bytes -- exactly what HMAC signing keys on."""
        return self.decrypt_secret_str(key).encode()

    def decrypt_secret_str(self, key: UserAccessKeyRecord) -> str:
        return self._cipher.decrypt(key.encrypted_secret)
