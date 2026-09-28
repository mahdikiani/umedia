"""Async SQLAlchemy configuration.

Replaces the old Beanie/MongoDB `db.py` -- see `docs/08-implementation-plan.md`
for why (`apps/media` moves to the same SQLite foundation `apps/api` proved
out, per the single-container/SQLite-only constraint).
"""

from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

SQLITE_BUSY_TIMEOUT_MS = 30_000


def _enable_sqlite_pragmas(dbapi_connection: Any, _: Any) -> None:  # noqa: ANN401
    """Set WAL journaling + a busy timeout on every new SQLite connection.

    SQLite serializes writers; without WAL mode, concurrent async requests
    (and the separate connection USSO Lite opens against the same file) can
    raise "database is locked" errors under load. See
    docs/02-architecture.md "Correctness & consistency".
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    cursor.close()


def configure_sqlite_engine(engine: AsyncEngine) -> None:
    event.listen(engine.sync_engine, "connect", _enable_sqlite_pragmas)


def create_engine(database_url: str) -> AsyncEngine:
    """Create the application async database engine."""
    engine = create_async_engine(database_url, pool_pre_ping=True)
    if database_url.startswith("sqlite"):
        configure_sqlite_engine(engine)
    return engine


def create_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Create the async session factory shared with fastapi-mongo-base."""
    return async_sessionmaker(engine, expire_on_commit=False)
