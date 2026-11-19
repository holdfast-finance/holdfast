"""FastAPI application factory.

Run with: ``uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from redis.asyncio import Redis

from app import __version__
from app.api import routes_health
from app.config import Settings, get_settings
from app.db import CONNECT_TIMEOUT_SECONDS, create_engine, create_session_factory
from app.logging import configure_logging, get_logger

API_PREFIX = "/api"

log = get_logger(__name__)


def create_redis(settings: Settings) -> Redis:
    """Build the Redis client. No connection is opened until first use."""
    client: Redis = Redis.from_url(
        settings.redis_url.get_secret_value(),
        socket_connect_timeout=CONNECT_TIMEOUT_SECONDS,
        socket_timeout=CONNECT_TIMEOUT_SECONDS,
    )
    return client


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info("api.startup", version=__version__, app_env=settings.app_env)
        try:
            yield
        finally:
            await app.state.redis.aclose()
            await app.state.engine.dispose()
            log.info("api.shutdown")

    app = FastAPI(
        title="Holdfast API",
        version=__version__,
        lifespan=lifespan,
        openapi_url=None if settings.is_prod else f"{API_PREFIX}/openapi.json",
        docs_url=None if settings.is_prod else f"{API_PREFIX}/docs",
        redoc_url=None,
        swagger_ui_oauth2_redirect_url=None
        if settings.is_prod
        else f"{API_PREFIX}/docs/oauth2-redirect",
    )
    # Clients are created eagerly but lazily connect, so the app boots with services down.
    app.state.settings = settings
    app.state.engine = create_engine(settings)
    app.state.session_factory = create_session_factory(app.state.engine)
    app.state.redis = create_redis(settings)

    api = APIRouter(prefix=API_PREFIX)
    api.include_router(routes_health.router)
    app.include_router(api)
    return app
