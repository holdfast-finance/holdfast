"""Routers auto-register from ``app/api/routes_*.py`` (ADR 0011); the e2e hooks gate holds."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from starlette.routing import NoMatchFound

from app.main import build_api_router, discover_route_modules
from tests.conftest import make_settings

PUBLIC = {"routes_auth", "routes_health", "routes_household", "routes_setup"}


def _app(**overrides: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(build_api_router(make_settings(**overrides)))
    return app


def _test_hook_mounted(**overrides: Any) -> bool:
    # routes_testing is include_in_schema=False, so resolve it by endpoint name instead.
    try:
        _app(**overrides).url_path_for("setup_token")
    except NoMatchFound:
        return False
    return True


def test_discovers_every_public_route_module(clean_env: pytest.MonkeyPatch) -> None:
    names = discover_route_modules(make_settings())
    assert set(names) >= PUBLIC
    assert names == sorted(names)


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"app_env": "dev", "e2e_hooks": True},
        {"app_env": "prod", "e2e_hooks": True},
        {"app_env": "test", "e2e_hooks": False},
    ],
)
def test_test_hooks_stay_unmounted_without_both_flags(
    clean_env: pytest.MonkeyPatch, overrides: dict[str, Any]
) -> None:
    assert "routes_testing" not in discover_route_modules(make_settings(**overrides))
    assert not _test_hook_mounted(**overrides)


def test_test_hooks_mount_with_app_env_test_and_e2e_hooks(clean_env: pytest.MonkeyPatch) -> None:
    assert "routes_testing" in discover_route_modules(make_settings(app_env="test", e2e_hooks=True))
    assert _test_hook_mounted(app_env="test", e2e_hooks=True)


def test_public_routes_are_mounted(clean_env: pytest.MonkeyPatch) -> None:
    paths = set(_app().openapi()["paths"])
    assert "/api/health" in paths
    assert any(p.startswith("/api/auth/") for p in paths)
    assert any(p.startswith("/api/setup") for p in paths)
