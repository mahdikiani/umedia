"""Async Alembic environment."""

import asyncio
from logging.config import fileConfig

from fastapi_mongo_base.sql.models import BaseEntity
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from apps.auth.models import AdminSettings
from apps.provider_connections.models import ProviderConnection
from server.config import Settings

__all__ = ["AdminSettings", "ProviderConnection"]

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
