"""GET /api/health: 200 when DB and Redis answer, 503 with the failing check otherwise."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from app import __version__
from app.api.routes_health import get_probe
from app.main import create_app
from tests.conftest import make_settings


async def _client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class FakeProbe:
    def __init__(self, *, db: bool | Exception, redis: bool | Exception) -> None:
        self._db, self._redis = db, redis

    async def _answer(self, value: bool | Exception) -> bool:
        if isinstance(value, Exception):
            raise value
        return value

    async def check_db(self) -> bool:
        return await self._answer(self._db)

    async def check_redis(self) -> bool:
        return await self._answer(self._redis)


def _app_with(probe: FakeProbe) -> FastAPI:
    app = create_app(make_settings())
    app.dependency_overrides[get_probe] = lambda: probe
    return app


@pytest.mark.parametrize(
    ("db", "redis", "expected"),
    [
        (RuntimeError("password=hunter2 host=db"), True, {"db": "error", "redis": "ok"}),
        (
            True,
            ConnectionError("redis://:s3cret@redis:6379 refused"),
            {"db": "ok", "redis": "error"},
        ),
        (TimeoutError(), False, {"db": "error", "redis": "error"}),
    ],
)
async def test_health_returns_503_with_failing_check(
    db: bool | Exception, redis: bool | Exception, expected: dict[str, str]
) -> None:
    app = _app_with(FakeProbe(db=db, redis=redis))
    async for client in _client(app):
        resp = await client.get("/api/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body == {"status": "error", "version": __version__, "checks": expected}
    for leaked in ("hunter2", "s3cret", "refused", "password", "Error"):
        assert leaked not in resp.text


async def test_health_ok_with_fakes() -> None:
    app = _app_with(FakeProbe(db=True, redis=True))
    async for client in _client(app):
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "version": "0.0.0-alpha",
        "checks": {"db": "ok", "redis": "ok"},
    }


async def test_health_503_when_real_services_unreachable() -> None:
    # Port 1 on loopback refuses immediately; exercises the real probe's error handling.
    app = create_app(
        make_settings(
            database_url="postgresql+psycopg://nobody:not-a-real-pw@127.0.0.1:1/none",
            redis_url="redis://127.0.0.1:1/0",
        )
    )
    async for client in _client(app):
        resp = await client.get("/api/health")
    assert resp.status_code == 503
    assert resp.json()["checks"] == {"db": "error", "redis": "error"}
    assert "not-a-real-pw" not in resp.text
    await app.state.redis.aclose()
    await app.state.engine.dispose()


@pytest.mark.services
async def test_health_ok_against_real_services(database_url: str, redis_url: str) -> None:
    app = create_app(make_settings(database_url=database_url, redis_url=redis_url))
    async for client in _client(app):
        resp = await client.get("/api/health")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "status": "ok",
        "version": __version__,
        "checks": {"db": "ok", "redis": "ok"},
    }
    await app.state.redis.aclose()
    await app.state.engine.dispose()


@pytest.mark.parametrize(("app_env", "status"), [("dev", 200), ("prod", 404)])
async def test_openapi_and_docs_only_outside_prod(app_env: str, status: int) -> None:
    app = create_app(make_settings(app_env=app_env))
    async for client in _client(app):
        spec = await client.get("/api/openapi.json")
        docs = await client.get("/api/docs")
    assert spec.status_code == status
    assert docs.status_code == status
    if status == 200:
        assert "/api/health" in spec.json()["paths"]


async def test_routes_live_under_api_prefix() -> None:
    app = create_app(make_settings())
    async for client in _client(app):
        spec = (await client.get("/api/openapi.json")).json()
        bare = await client.get("/health")
        redirect = await client.get("/api/docs/oauth2-redirect")
    assert spec["paths"]
    assert all(path.startswith("/api/") for path in spec["paths"])
    assert bare.status_code == 404
    assert redirect.status_code == 200
