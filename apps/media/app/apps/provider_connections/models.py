"""Provider connection persistence."""

from datetime import datetime

from fastapi_mongo_base.sql.models import BaseEntity
from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column


class ProviderConnection(BaseEntity):
    """Encrypted configuration for one storage provider connection."""

    __tablename__ = "provider_connections"

    # Creating user's uid — each authenticated user only sees and manages
    # their own connections (local storage is additionally admin-only).
    owner_id: Mapped[str] = mapped_column(index=True)
    provider_type: Mapped[str] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(index=True)
    encrypted_config: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(default="configured", index=True)
    # Gates whether resource operations may use this connection (checked
    # in apps/resources/plugin_gateway.py) -- a way to temporarily turn
    # one off without losing its config or the resources already created
    # through it. Distinct from a plugin *process* being spawned at all,
    # which stays a manifest-level (docs/03-provider-system.md) decision:
    # one process commonly serves many connections of the same
    # provider_type, so there's no single connection to key that off of.
    enabled: Mapped[bool] = mapped_column(default=True, index=True)
    # Dual-layer flags (docs/11-dual-layer-library.md) -- orthogonal:
    # `import_existing`: on connect (and explicit sync) pull the remote's
    # preexisting objects into the StorageObject index + the user library.
    # `mirror_structure`: reflect library moves/renames of linked files
    # back onto the provider when the plugin supports structure.
    import_existing: Mapped[bool] = mapped_column(default=False)
    mirror_structure: Mapped[bool] = mapped_column(default=False)
    last_tested_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
