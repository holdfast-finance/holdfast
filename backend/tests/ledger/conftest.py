"""Fixtures for LedgerWriter DB tests: real Postgres as holdfast_app, RLS enforced."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import create_session_factory
from tests.importers import conftest as importers_conftest

# Schema and household fixtures shared with tests/importers (pytest finds them by these names).
app_url = importers_conftest.app_url
owner_url = importers_conftest.owner_url
migrated = importers_conftest.migrated
app_engine = importers_conftest.app_engine
owner_engine = importers_conftest.owner_engine
household = importers_conftest.household


@pytest.fixture
async def factory(app_url: str, migrated: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    # Same engine option as app.db.create_engine, so error text is what production would log.
    engine = create_async_engine(app_url, hide_parameters=True)
    yield create_session_factory(engine)
    await engine.dispose()
