"""Fixtures for the statement-import service tests: real Postgres as holdfast_app (RLS applies)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import create_session_factory
from tests.models import conftest as models_conftest

# Schema fixtures shared with tests/models (pytest finds them by these module-level names).
app_url = models_conftest.app_url
owner_url = models_conftest.owner_url
migrated = models_conftest.migrated
app_engine = models_conftest.app_engine
owner_engine = models_conftest.owner_engine


@pytest.fixture
def household(app_engine: Engine, owner_engine: Engine) -> Iterator[uuid.UUID]:
    with app_engine.begin() as c:
        hid = c.execute(
            text("SELECT create_household(:n)"), {"n": "Example Import Household"}
        ).scalar_one()
    yield hid
    models_conftest.delete_household(owner_engine, hid)


@pytest.fixture
async def factory(app_url: str, migrated: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(app_url)
    yield create_session_factory(engine)
    await engine.dispose()
