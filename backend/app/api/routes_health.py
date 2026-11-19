"""``GET /api/health``: liveness plus DB and Redis readiness."""

from __future__ import annotations

import asyncio
import inspect
from typing import Annotated, Literal, Protocol

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app import __version__
from app.logging import get_logger

CHECK_TIMEOUT_SECONDS = 2.0  # money-lint: allow probe timeout seconds, not money

CheckState = Literal["ok", "error"]

log = get_logger(__name__)
router = APIRouter(tags=["health"])


class HealthChecks(BaseModel):
    db: CheckState
    redis: CheckState


class HealthResponse(BaseModel):
    status: CheckState
    version: str
    checks: HealthChecks


class HealthProbe(Protocol):
    async def check_db(self) -> bool: ...

    async def check_redis(self) -> bool: ...


class ServiceProbe:
    """Probes the real engine and Redis client with short timeouts."""

    def __init__(self, engine: AsyncEngine, redis: Redis) -> None:
        self._engine = engine
        self._redis = redis

    async def check_db(self) -> bool:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS), self._engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True

    async def check_redis(self) -> bool:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            result = self._redis.ping()
            pong = await result if inspect.isawaitable(result) else result
        return bool(pong)


def get_probe(request: Request) -> HealthProbe:
    return ServiceProbe(request.app.state.engine, request.app.state.redis)


async def _run(name: str, check: HealthProbe) -> CheckState:
    probe = check.check_db if name == "db" else check.check_redis
    try:
        return "ok" if await probe() else "error"
    except Exception as exc:
        # Log the exception type only: driver messages can echo hosts, users or DSNs.
        log.warning("health.check_failed", check=name, error_type=type(exc).__name__)
        return "error"


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse, "description": "A dependency is down"}},
)
async def health(
    response: Response, probe: Annotated[HealthProbe, Depends(get_probe)]
) -> HealthResponse:
    db_state, redis_state = await asyncio.gather(_run("db", probe), _run("redis", probe))
    healthy = db_state == "ok" and redis_state == "ok"
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if healthy else "error",
        version=__version__,
        checks=HealthChecks(db=db_state, redis=redis_state),
    )
