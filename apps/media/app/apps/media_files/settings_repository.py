"""Persist the singleton `instance_settings` row."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import InstanceSettings
from .placement import POLICIES, PlacementSettings


class InstanceSettingsRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self) -> PlacementSettings:
        row = await self._row()
        policy = (
            row.placement_policy
            if row.placement_policy in POLICIES
            else "default"
        )
        return PlacementSettings(
            policy=policy,  # type: ignore[arg-type]
            default_connection_id=row.default_connection_id,
            fill_order=list(row.fill_order or []),
        )

    async def update(self, changes: dict) -> PlacementSettings:
        async with self._session_factory() as session:
            row = await self._row_in(session)
            for key, value in changes.items():
                setattr(row, key, value)
            await session.commit()
            await session.refresh(row)
            policy = (
                row.placement_policy
                if row.placement_policy in POLICIES
                else "default"
            )
            return PlacementSettings(
                policy=policy,  # type: ignore[arg-type]
                default_connection_id=row.default_connection_id,
                fill_order=list(row.fill_order or []),
            )

    async def clear_connection_references(self, connection_id: str) -> None:
        """Drop a removed connection from placement settings so later
        uploads never resolve to a deleted provider uid."""
        current = await self.get()
        changes: dict = {}
        if current.default_connection_id == connection_id:
            changes["default_connection_id"] = None
        if connection_id in current.fill_order:
            changes["fill_order"] = [
                uid for uid in current.fill_order if uid != connection_id
            ]
        if changes:
            await self.update(changes)

    async def _row(self) -> InstanceSettings:
        async with self._session_factory() as session:
            return await self._row_in(session)

    async def _row_in(self, session: AsyncSession) -> InstanceSettings:
        result = await session.execute(
            select(InstanceSettings).where(InstanceSettings.is_deleted.is_(False)),
        )
        row = result.scalars().first()
        if row is not None:
            return row
        row = InstanceSettings(
            placement_policy="default",
            default_connection_id=None,
            fill_order=[],
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row
