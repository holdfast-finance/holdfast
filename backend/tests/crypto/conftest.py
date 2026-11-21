"""Fixtures for crypto service tests: real Postgres as holdfast_app (RLS applies)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.db import create_session_factory
from app.services.context import RequestContext
from app.services.sessions import Principal
from tests.models import conftest as models_conftest
from tests.models.conftest import _insert, set_ctx

app_url = models_conftest.app_url
owner_url = models_conftest.owner_url
migrated = models_conftest.migrated
app_engine = models_conftest.app_engine
owner_engine = models_conftest.owner_engine


@dataclass
class Fixture:
    household_id: uuid.UUID
    alex: uuid.UUID
    sam: uuid.UUID


@pytest.fixture
def world(app_engine: Engine, owner_engine: Engine) -> Iterator[Fixture]:
    with app_engine.begin() as c:
        hid = c.execute(text("SELECT create_household('Example Crypto Household')")).scalar_one()
        set_ctx(c, hid, wide=True)
        alex = _insert(c, "person", household_id=hid, display_name="Alex", filing_role="primary")
        sam = _insert(c, "person", household_id=hid, display_name="Sam", filing_role="spouse")
    yield Fixture(hid, alex, sam)
    models_conftest.delete_household(owner_engine, hid)


@pytest.fixture
async def factory(app_url: str, migrated: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(app_url)
    yield create_session_factory(engine)
    await engine.dispose()


def principal(world: Fixture, person: uuid.UUID | None) -> Principal:
    return Principal(
        session_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        household_id=world.household_id,
        person_id=person,
        role="owner",
        email="alex@example.com",
        reauth_at=None,
    )


def context(factory: async_sessionmaker[AsyncSession]) -> RequestContext:
    redis: Any = None  # the wallet service never touches Redis
    settings: Any = Settings.model_construct()
    return RequestContext(factory=factory, redis=redis, settings=settings, ip=None, user_agent=None)
