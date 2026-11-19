"""Alembic environment (async, psycopg 3).

Migrations run as the schema owner: the URL comes from ``Settings.migrations_sqlalchemy_url``
(MIGRATIONS_DATABASE_URL + POSTGRES_OWNER_PASSWORD, falling back to DATABASE_URL). Tests may pass a
URL through ``config.attributes["database_url"]`` instead.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import URL, Connection, make_url
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401  (registers models on Base.metadata)
from app.config import get_settings
from app.db import Base

config = context.config

if config.config_file_name is not None and not config.attributes.get("skip_logging_config"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> URL:
    override = config.attributes.get("database_url")
    if override is not None:
        return make_url(override)
    return get_settings().migrations_sqlalchemy_url


def run_migrations_offline() -> None:
    context.configure(
        url=_url().render_as_string(hide_password=True),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url(), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
