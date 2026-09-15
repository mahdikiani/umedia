"""Async Alembic environment.

Only tracks this app's own tables (`ProviderConnection`, `Resource`,
`StorageObject`, `MediaFile`, `MediaFileObject`, `InstanceSettings`). usso.lite manages its
own tables separately via `LiteDatabase.init_db()` (`create_all`, not
migrations) -- see `apps/auth/services.py` -- so they are deliberately
not part of this metadata.
"""

import asyncio
from logging.config import fileConfig

from fastapi_mongo_base.sql.models import BaseEntity
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from apps.media_files.models import (
    InstanceSettings,
    LibraryTransfer,
    MediaFile,
    MediaFileObject,
    MediaFileStar,
)
from apps.provider_connections.models import ProviderConnection
from apps.resources.models import Resource
from apps.storage_objects.models import StorageObject
from server.config import Settings

__all__ = [
    "InstanceSettings",
    "LibraryTransfer",
    "MediaFile",
    "MediaFileObject",
    "MediaFileStar",
    "ProviderConnection",
    "Resource",
    "StorageObject",
]

config = context.config
config.set_main_option("sqlalchemy.url", Settings().database_uri)
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = BaseEntity.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: object) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    engine = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
