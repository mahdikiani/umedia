"""Authentication persistence model."""

from fastapi_mongo_base.sql.models import BaseEntity
from sqlalchemy.orm import Mapped, mapped_column


class AdminSettings(BaseEntity):
    """Singleton installation authentication state."""

    __tablename__ = "admin_settings"

    email: Mapped[str] = mapped_column(nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(nullable=False)
    password_version: Mapped[int] = mapped_column(default=1, nullable=False)
