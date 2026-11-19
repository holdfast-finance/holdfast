"""Test-only hooks for the automated e2e stack. Mounted only with ``APP_ENV=test`` *and*
``E2E_HOOKS=true``.

``app.main`` includes this router only when both are set, and the handler re-checks them, so a
dev or prod instance (or a test instance without the second switch) can never serve it. With
both set, the setup token minted at startup is also kept in memory (``app.state.test_setup_token``)
so e2e can read it here instead of scraping logs.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.services.errors import ApiError

# Discovery refuses to mount a TEST_ONLY module unless APP_ENV=test and E2E_HOOKS=true (ADR 0011).
TEST_ONLY = True

router = APIRouter(prefix="/test", tags=["test"], include_in_schema=False)


@router.get("/setup-token")
async def setup_token(request: Request) -> dict[str, str]:
    settings = request.app.state.settings
    if settings.app_env != "test" or not settings.e2e_hooks:
        raise ApiError("not_found", "Not found.")
    token: str | None = getattr(request.app.state, "test_setup_token", None)
    if token is None:
        raise ApiError("not_found", "No setup token is pending.")
    return {"setup_token": token}
