"""Async SQLAlchemy engine, session factory and the declarative ``Base``."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime
from typing import Any

from fastapi import Request
from sqlalchemy import Date, DateTime, LargeBinary, MetaData, Text, Uuid
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import Settings

# Stable constraint names so Alembic autogenerate produces deterministic, reversible diffs.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

CONNECT_TIMEOUT_SECONDS = 2


class Base(DeclarativeBase):
    """Declarative base for every ORM model.

    The annotation map fixes the Postgres type for common Python types so models stay terse:
    ``datetime`` is always ``timestamptz``, ``bytes`` is ``bytea``, ``dict`` is ``jsonb`` and
    ``list[str]`` is ``text[]``. ``Decimal`` is deliberately *not* mapped: every money, quantity
    or price column must name its precision (see ``app.models._common``).
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {  # noqa: RUF012  (SQLAlchemy reads this class attribute)
        uuid.UUID: Uuid(),
        datetime: DateTime(timezone=True),
        date: Date(),
        str: Text(),
        bytes: LargeBinary(),
        dict[str, Any]: JSONB(),
        list[Any]: JSONB(),
        list[str]: ARRAY(Text()),
    }


def create_engine(settings: Settings) -> AsyncEngine:
    """Build the async engine. No connection is opened until first use."""
    return create_async_engine(
        settings.sqlalchemy_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def get_engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.engine
    return engine


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session
