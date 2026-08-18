"""Provider connection database access."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import ProviderConnection


class ProviderConnectionRepository:
    """Persist provider connections."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, data: dict) -> ProviderConnection:
        async with self._session_factory() as session:
            connection = ProviderConnection(**data)
            session.add(connection)
            await session.commit()
            await session.refresh(connection)
            return connection

    async def list(self) -> list[ProviderConnection]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ProviderConnection)
                .where(ProviderConnection.is_deleted.is_(False))
                .order_by(ProviderConnection.created_at.desc()),
            )
            return list(result.scalars())

    async def get(self, uid: str) -> ProviderConnection | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ProviderConnection).where(
                    ProviderConnection.uid == uid,
                    ProviderConnection.is_deleted.is_(False),
                ),
            )
            return result.scalar_one_or_none()

    async def delete(self, uid: str) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ProviderConnection).where(
                    ProviderConnection.uid == uid,
                    ProviderConnection.is_deleted.is_(False),
                ),
            )
            connection = result.scalar_one_or_none()
            if connection is None:
                return False
            connection.is_deleted = True
            await session.commit()
            return True

