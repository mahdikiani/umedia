"""Provider connection persistence."""

from datetime import datetime

from fastapi_mongo_base.sql.models import BaseEntity
from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column


class ProviderConnection(BaseEntity):
    """Encrypted configuration for one storage provider connection."""

    __tablename__ = "provider_connections"

    provider_type: Mapped[str] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(index=True)
    encrypted_config: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(default="configured", index=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

