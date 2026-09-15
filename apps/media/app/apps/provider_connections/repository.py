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

    async def list(self, *, owner_id: str | None = None) -> list[ProviderConnection]:
        async with self._session_factory() as session:
            query = (
                select(ProviderConnection)
                .where(ProviderConnection.is_deleted.is_(False))
                .order_by(ProviderConnection.created_at.desc())
            )
            if owner_id is not None:
                query = query.where(ProviderConnection.owner_id == owner_id)
            result = await session.execute(query)
            return list(result.scalars())

    async def get(
        self,
        uid: str,
        *,
        owner_id: str | None = None,
    ) -> ProviderConnection | None:
        async with self._session_factory() as session:
            clauses = [
                ProviderConnection.uid == uid,
                ProviderConnection.is_deleted.is_(False),
            ]
            if owner_id is not None:
                clauses.append(ProviderConnection.owner_id == owner_id)
            result = await session.execute(
                select(ProviderConnection).where(*clauses),
            )
            return result.scalar_one_or_none()

    async def update(
        self,
        uid: str,
        changes: dict,
        *,
        owner_id: str | None = None,
    ) -> ProviderConnection | None:
        async with self._session_factory() as session:
            clauses = [
                ProviderConnection.uid == uid,
                ProviderConnection.is_deleted.is_(False),
            ]
            if owner_id is not None:
                clauses.append(ProviderConnection.owner_id == owner_id)
            result = await session.execute(
                select(ProviderConnection).where(*clauses),
            )
            connection = result.scalar_one_or_none()
            if connection is None:
                return None
            for key, value in changes.items():
                setattr(connection, key, value)
            await session.commit()
            await session.refresh(connection)
            return connection

    async def delete(self, uid: str, *, owner_id: str | None = None) -> bool:
        async with self._session_factory() as session:
            clauses = [
                ProviderConnection.uid == uid,
                ProviderConnection.is_deleted.is_(False),
            ]
            if owner_id is not None:
                clauses.append(ProviderConnection.owner_id == owner_id)
            result = await session.execute(
                select(ProviderConnection).where(*clauses),
            )
            connection = result.scalar_one_or_none()
            if connection is None:
                return False
            connection.is_deleted = True
            await session.commit()
            return True
