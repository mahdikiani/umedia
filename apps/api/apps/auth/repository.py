"""Authentication database access."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import AdminSettings

INSTALLATION_SETTINGS_UID = "installation"


class AuthRepository:
    """Persist the singleton administrator settings."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def get(self) -> tuple[str, str, int] | None:
        """Return configured password state."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(AdminSettings).where(
                    AdminSettings.uid == INSTALLATION_SETTINGS_UID,
                    AdminSettings.is_deleted.is_(False),
                ),
            )
            settings = result.scalar_one_or_none()
            if settings is None:
                return None
            return (
                settings.email,
                settings.password_hash,
                settings.password_version,
            )

    async def create(
        self,
        email: str,
        password_hash: str,
    ) -> tuple[str, str, int]:
        """Create the initial administrator settings."""
        async with self._session_factory() as session:
            settings = AdminSettings(
                uid=INSTALLATION_SETTINGS_UID,
                email=email,
                password_hash=password_hash,
                password_version=1,
            )
            session.add(settings)
            await session.commit()
            return (
                settings.email,
                settings.password_hash,
                settings.password_version,
            )

    async def change_password(
        self,
        password_hash: str,
    ) -> tuple[str, str, int]:
        """Update the password and invalidate existing sessions."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(AdminSettings).where(
                    AdminSettings.uid == INSTALLATION_SETTINGS_UID,
                    AdminSettings.is_deleted.is_(False),
                ),
            )
            settings = result.scalar_one()
            settings.password_hash = password_hash
            settings.password_version += 1
            await session.commit()
            return (
                settings.email,
                settings.password_hash,
                settings.password_version,
            )
