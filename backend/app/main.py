"""FastAPI application factory.

Run with: ``uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000``.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import importlib.machinery
import pkgutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from redis.asyncio import Redis

import app.api as api_pkg
from app import __version__
from app.api import errors
from app.config import Settings, get_settings
from app.db import CONNECT_TIMEOUT_SECONDS, create_engine, create_session_factory
from app.logging import configure_logging, get_logger
from app.security import crypto
from app.security.body_limit import BodySizeLimitMiddleware
from app.security.csrf import CSRFMiddleware
from app.services.setup import SETUP_TTL_SECONDS, issue_setup_token, setup_state

API_PREFIX = "/api"
ROUTE_MODULE_PREFIX = "routes_"
# Test-only route modules (ADR 0011). Two independent signals, both fail closed:
# - a name starting ``routes_test`` is skipped *before import* unless e2e hooks are enabled, so
#   its code never loads in dev or prod, and it must declare ``TEST_ONLY = True``;
# - any route module declaring ``TEST_ONLY = True`` refuses to mount without e2e hooks, and the
#   app fails at startup rather than serving it.
TEST_ROUTE_PREFIX = "routes_test"
SETUP_RETRY_SECONDS = 5.0

log = get_logger(__name__)


def e2e_hooks_enabled(settings: Settings) -> bool:
    """``/api/test/*`` needs both APP_ENV=test and E2E_HOOKS=true (never in dev or prod)."""
    return settings.app_env == "test" and settings.e2e_hooks


def _has_source(finder: object, name: str) -> bool:
    """True only for a module backed by a real ``.py`` file (no sourceless .pyc, .so, .pyd)."""
    find_spec = getattr(finder, "find_spec", None)
    spec = find_spec(name) if find_spec is not None else None
    return (
        spec is not None
        and isinstance(spec.loader, importlib.machinery.SourceFileLoader)
        and isinstance(spec.origin, str)
        and spec.origin.endswith(".py")
    )


def discover_route_modules(settings: Settings) -> list[str]:
    """Names of ``app/api/routes_*.py`` modules to mount, sorted for a stable OpenAPI order.

    A new router is a new module exposing ``router``; main.py is never edited (ADR 0011).
    """
    hooks = e2e_hooks_enabled(settings)
    return sorted(
        info.name
        for info in pkgutil.iter_modules(api_pkg.__path__)
        if info.name.startswith(ROUTE_MODULE_PREFIX)
        and not info.ispkg
        and (hooks or not info.name.startswith(TEST_ROUTE_PREFIX))
        and _has_source(info.module_finder, info.name)
    )


def build_api_router(settings: Settings) -> APIRouter:
    hooks = e2e_hooks_enabled(settings)
    api = APIRouter(prefix=API_PREFIX)
    for name in discover_route_modules(settings):
        module = importlib.import_module(f"{api_pkg.__name__}.{name}")
        test_only = getattr(module, "TEST_ONLY", False) is True
        if name.startswith(TEST_ROUTE_PREFIX) and not test_only:
            raise RuntimeError(f"app.api.{name} is a test route module; declare TEST_ONLY = True")
        if test_only and not hooks:
            raise RuntimeError(
                f"app.api.{name} is TEST_ONLY but APP_ENV=test and E2E_HOOKS=true are not both "
                "set; refusing to start"
            )
        router = getattr(module, "router", None)
        if not isinstance(router, APIRouter):
            raise RuntimeError(f"app.api.{name} must expose `router: APIRouter`")
        api.include_router(router)
    return api


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
        if settings.fernet_key is not None:
            crypto.load_ring(settings.fernet_key)  # malformed or duplicate keys fail startup
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

    app.include_router(build_api_router(settings))
    return app
