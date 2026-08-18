"""User access-key database access."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import UserAccessKey
from .schemas import UserAccessKeyRecord


def _to_record(row: UserAccessKey) -> UserAccessKeyRecord:
    return UserAccessKeyRecord(
        uid=row.uid,
        user_id=row.user_id,
        access_key_id=row.access_key_id,
        encrypted_secret=row.encrypted_secret,
        label=row.label,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class UserAccessKeyRepository:
    """Persist user access keys."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, data: dict[str, Any]) -> UserAccessKeyRecord:
        async with self._session_factory() as session:
            key = UserAccessKey(**data)
            session.add(key)
            await session.commit()
            await session.refresh(key)
            return _to_record(key)

    async def get_by_access_key_id(
        self, access_key_id: str,
    ) -> UserAccessKeyRecord | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(UserAccessKey).where(
                    UserAccessKey.access_key_id == access_key_id,
                    UserAccessKey.is_deleted.is_(False),
                ),
            )
            row = result.scalar_one_or_none()
            return None if row is None else _to_record(row)

    async def get(self, uid: str) -> UserAccessKeyRecord | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(UserAccessKey).where(
                    UserAccessKey.uid == uid,
                    UserAccessKey.is_deleted.is_(False),
                ),
            )
            row = result.scalar_one_or_none()
            return None if row is None else _to_record(row)

    async def list_for_user(self, user_id: str) -> list[UserAccessKeyRecord]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(UserAccessKey)
                .where(
                    UserAccessKey.user_id == user_id,
                    UserAccessKey.is_deleted.is_(False),
                )
                .order_by(UserAccessKey.created_at.desc(), UserAccessKey.uid.desc()),
            )
            return [_to_record(row) for row in result.scalars().all()]

    async def first_active_for_user(
        self, user_id: str,
    ) -> UserAccessKeyRecord | None:
        """The user's oldest active key -- what "default" resolves to."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(UserAccessKey)
                .where(
                    UserAccessKey.user_id == user_id,
                    UserAccessKey.is_active.is_(True),
                    UserAccessKey.is_deleted.is_(False),
                )
                .order_by(UserAccessKey.created_at.asc(), UserAccessKey.uid.asc())
                .limit(1),
            )
            row = result.scalar_one_or_none()
            return None if row is None else _to_record(row)

    async def set_active(
        self, uid: str, *, is_active: bool,
    ) -> UserAccessKeyRecord | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(UserAccessKey).where(
                    UserAccessKey.uid == uid,
                    UserAccessKey.is_deleted.is_(False),
                ),
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            row.is_active = is_active
            await session.commit()
            await session.refresh(row)
            return _to_record(row)
