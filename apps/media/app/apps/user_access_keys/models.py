"""`user_access_keys` persistence -- docs/04-data-model.md.

One row per key pair. The secret is *never* a column in plaintext:
`encrypted_secret` holds the Fernet token produced by
`CredentialCipher.encrypt`; only `UserAccessKeyService` decrypts it, in
memory, at sign/verify time.
"""

from fastapi_mongo_base.sql.models import BaseEntity
from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column


class UserAccessKey(BaseEntity):
    """A user's S3-style key pair. `uid`, `created_at`, `updated_at`,
    `is_deleted` come from `BaseEntity`."""

    __tablename__ = "user_access_keys"

    #: The owning LocalUser's uid (usso.lite's table -- an opaque string
    #: here, same convention as `media_files.owner_id`).
    user_id: Mapped[str] = mapped_column(index=True)
    #: The public half, carried in signed URLs (`?key_id=`).
    access_key_id: Mapped[str] = mapped_column(unique=True, index=True)
    #: Fernet token of the raw urlsafe secret (CredentialCipher.encrypt).
    encrypted_secret: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(default="default")
    #: Deactivating a key revokes every link it ever signed.
    is_active: Mapped[bool] = mapped_column(default=True, index=True)
