"""FastAPI application factory.

Run with: ``uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000``.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from redis.asyncio import Redis

from app import __version__
from app.api import errors, routes_auth, routes_health, routes_household, routes_setup
from app.config import Settings, get_settings
from app.db import CONNECT_TIMEOUT_SECONDS, create_engine, create_session_factory
from app.logging import configure_logging, get_logger
from app.security.body_limit import BodySizeLimitMiddleware
from app.security.csrf import CSRFMiddleware
from app.services.setup import SETUP_TTL_SECONDS, issue_setup_token, setup_state

API_PREFIX = "/api"
SETUP_RETRY_SECONDS = 5.0

log = get_logger(__name__)


def e2e_hooks_enabled(settings: Settings) -> bool:
    """``/api/test/*`` needs both APP_ENV=test and E2E_HOOKS=true (never in dev or prod)."""
    return settings.app_env == "test" and settings.e2e_hooks


def create_redis(settings: Settings) -> Redis:
    """Build the Redis client. No connection is opened until first use."""
    client: Redis = Redis.from_url(
        settings.redis_url.get_secret_value(),
        socket_connect_timeout=CONNECT_TIMEOUT_SECONDS,
        socket_timeout=CONNECT_TIMEOUT_SECONDS,
    )
    return client


async def announce_setup_token(app: FastAPI) -> str | None:
    """If setup is pending, mint a setup token and log it once. Returns it (for tests only).

    This WARNING line is the one place a secret is logged on purpose: the operator reads it from
    ``docker compose logs api`` to claim the instance. It goes in the event text (not a field
    named like a secret), so the redaction filter leaves it readable.
    """
    if await setup_state(app.state.session_factory) == "complete":
        return None
    token = await issue_setup_token(app.state.redis)
    if e2e_hooks_enabled(app.state.settings):
        app.state.test_setup_token = token
    minutes = SETUP_TTL_SECONDS // 60
    log.warning(
        f"=== HOLDFAST FIRST-RUN SETUP TOKEN: {token} === "
        f"Enter it at /setup within {minutes} minutes. Restart the API for a new one."
    )
    return token


async def _setup_token_loop(app: FastAPI) -> None:
    """Retry until DB and Redis answer (the API may start before `make migrate`)."""
    while True:
        try:
            await announce_setup_token(app)
            return
        except Exception as exc:
            log.warning("setup.token_deferred", error_type=type(exc).__name__)
            await asyncio.sleep(SETUP_RETRY_SECONDS)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()
    if settings.app_env == "test":
        log.warning(
            "api.test_mode",
            detail="APP_ENV=test is for automated e2e stacks only; never run it for real data.",
            e2e_hooks=settings.e2e_hooks,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info("api.startup", version=__version__, app_env=settings.app_env)
        task = asyncio.create_task(_setup_token_loop(app))
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
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

    errors.install(app)
    app.add_middleware(CSRFMiddleware, prefix=API_PREFIX)
    # Added last = outermost: oversized bodies are refused before anything else reads them.
    app.add_middleware(BodySizeLimitMiddleware, prefix=API_PREFIX)

    api = APIRouter(prefix=API_PREFIX)
    api.include_router(routes_health.router)
    api.include_router(routes_setup.router)
    api.include_router(routes_auth.router)
    api.include_router(routes_household.router)
    if e2e_hooks_enabled(settings):
        from app.api import routes_testing

        api.include_router(routes_testing.router)
    app.include_router(api)
    return app
