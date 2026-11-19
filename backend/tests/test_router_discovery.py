"""Routers auto-register from ``app/api/routes_*.py`` (ADR 0011); the e2e hooks gate holds."""

from __future__ import annotations

import importlib
import importlib.machinery
import py_compile
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from starlette.routing import NoMatchFound

import app.api as api_pkg
from app.main import build_api_router, discover_route_modules
from tests.conftest import make_settings

PUBLIC = {"routes_auth", "routes_health", "routes_household", "routes_setup"}
HOOKS: dict[str, Any] = {"app_env": "test", "e2e_hooks": True}


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
    assert "routes_testing" in discover_route_modules(make_settings(**HOOKS))
    assert _test_hook_mounted(**HOOKS)


def test_routes_testing_declares_test_only() -> None:
    from app.api import routes_testing

    assert routes_testing.TEST_ONLY is True


def test_public_routes_are_mounted(clean_env: pytest.MonkeyPatch) -> None:
    paths = set(_app().openapi()["paths"])
    assert "/api/health" in paths
    assert any(p.startswith("/api/auth/") for p in paths)
    assert any(p.startswith("/api/setup") for p in paths)


# --------------------------------------------------------------------------- temp route modules

ROUTER_SRC = "from fastapi import APIRouter\nrouter = APIRouter(prefix='/{name}')\n"


@pytest.fixture
def extra_api_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Extend ``app.api.__path__`` with a temp dir so discovery sees synthetic route modules."""
    monkeypatch.setattr(api_pkg, "__path__", [*api_pkg.__path__, str(tmp_path)])
    before = set(sys.modules)
    yield tmp_path
    for name in set(sys.modules) - before:
        if name.startswith("app.api."):
            del sys.modules[name]
    importlib.invalidate_caches()


def _write(directory: Path, name: str, extra: str = "") -> None:
    (directory / f"{name}.py").write_text(ROUTER_SRC.format(name=name) + extra)
    importlib.invalidate_caches()


def test_test_only_module_refuses_to_mount_without_hooks(
    clean_env: pytest.MonkeyPatch, extra_api_dir: Path
) -> None:
    _write(extra_api_dir, "routes_zz_debug", "TEST_ONLY = True\n")
    with pytest.raises(RuntimeError, match="TEST_ONLY"):
        build_api_router(make_settings())
    with pytest.raises(RuntimeError, match="TEST_ONLY"):
        build_api_router(make_settings(app_env="test", e2e_hooks=False))


def test_test_only_module_mounts_with_hooks(
    clean_env: pytest.MonkeyPatch, extra_api_dir: Path
) -> None:
    _write(extra_api_dir, "routes_zz_debug", "TEST_ONLY = True\n")
    assert "routes_zz_debug" in discover_route_modules(make_settings(**HOOKS))
    build_api_router(make_settings(**HOOKS))


def test_routes_test_prefix_is_never_imported_without_hooks(
    clean_env: pytest.MonkeyPatch, extra_api_dir: Path
) -> None:
    _write(extra_api_dir, "routes_test_extra")
    assert "routes_test_extra" not in discover_route_modules(make_settings())
    build_api_router(make_settings())
    assert "app.api.routes_test_extra" not in sys.modules


def test_routes_test_prefix_must_declare_test_only(
    clean_env: pytest.MonkeyPatch, extra_api_dir: Path
) -> None:
    _write(extra_api_dir, "routes_test_extra")
    with pytest.raises(RuntimeError, match="declare TEST_ONLY"):
        build_api_router(make_settings(**HOOKS))


def test_sourceless_modules_are_ignored(clean_env: pytest.MonkeyPatch, extra_api_dir: Path) -> None:
    src = extra_api_dir / "build" / "routes_zz_compiled.py"
    src.parent.mkdir()
    src.write_text(ROUTER_SRC.format(name="compiled"))
    py_compile.compile(str(src), cfile=str(extra_api_dir / "routes_zz_compiled.pyc"), doraise=True)
    native = extra_api_dir / f"routes_zz_native{importlib.machinery.EXTENSION_SUFFIXES[0]}"
    native.write_bytes(b"")
    _write(extra_api_dir, "routes_zz_plain")

    names = discover_route_modules(make_settings(**HOOKS))
    assert "routes_zz_plain" in names
    assert "routes_zz_compiled" not in names
    assert "routes_zz_native" not in names
    build_api_router(make_settings(**HOOKS))
    assert "app.api.routes_zz_compiled" not in sys.modules
    assert "app.api.routes_zz_native" not in sys.modules
