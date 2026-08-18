"""Plain data shape the access-key service works with -- same rationale
as `apps/media_files/schemas.py`: services depend on this dataclass, not
on the ORM row, so their unit tests run against anything producing it."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class UserAccessKeyRecord:
    """One `user_access_keys` row. `encrypted_secret` is the at-rest
    Fernet token -- callers get the raw secret only through
    `UserAccessKeyService.decrypt_secret`."""

    uid: str
    user_id: str
    access_key_id: str
    encrypted_secret: str
    label: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
