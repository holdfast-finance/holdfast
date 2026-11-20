"""Fixtures for job framework tests: real Postgres (as holdfast_app) and Redis DB 15.

Reuses the schema fixtures from ``tests/models/conftest.py`` (migrated to head as the owner;
every job write runs as holdfast_app under RLS). All data is synthetic.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from urllib.parse import urlsplit, urlunsplit

import pytest
from redis.asyncio import Redis
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import create_session_factory
from app.jobs.runner import RunnerDeps
from tests.conftest import _reachable, _unavailable, env_redis_url
from tests.models import conftest as models_conftest

# Schema fixtures shared with tests/models (pytest finds them by these module-level names).
app_url = models_conftest.app_url
owner_url = models_conftest.owner_url
migrated = models_conftest.migrated
app_engine = models_conftest.app_engine
owner_engine = models_conftest.owner_engine

REDIS_TEST_DB = 15


@pytest.fixture
def households(
    app_engine: Engine,
    owner_engine: Engine,
) -> Iterator[tuple[uuid.UUID, uuid.UUID]]:
    """Two fresh, empty households (created through create_household, as the app does)."""
    ids: list[uuid.UUID] = []
    with app_engine.begin() as c:
        for label in ("A", "B"):
            ids.append(
                c.execute(
                    text("SELECT create_household(:n)"), {"n": f"Example Jobs Household {label}"}
                ).scalar_one()
            )
    yield ids[0], ids[1]
    for hid in ids:
        models_conftest.delete_household(owner_engine, hid)


@pytest.fixture
async def deps(app_url: str, migrated: str) -> AsyncIterator[RunnerDeps]:
    engine = create_async_engine(app_url)
    yield RunnerDeps(engine=engine, factory=create_session_factory(engine))
    await engine.dispose()


@pytest.fixture
def jobs_redis_url() -> str:
    url = env_redis_url()
    if not url:
        _unavailable("TEST_REDIS_URL / REDIS_URL is not set; start Redis to run job tests")
    assert url is not None
    if not _reachable(url, 6379):
        _unavailable("Redis at TEST_REDIS_URL / REDIS_URL is unreachable")
    return urlunsplit(urlsplit(url)._replace(path=f"/{REDIS_TEST_DB}"))


@pytest.fixture
async def redis(jobs_redis_url: str) -> AsyncIterator[Redis]:
    client: Redis = Redis.from_url(jobs_redis_url)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()
