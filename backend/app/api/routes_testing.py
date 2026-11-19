"""Test-only hooks for the automated e2e stack. Mounted only when ``APP_ENV=test``.

``app.main`` includes this router only for ``app_env == "test"``, and the handler re-checks the
setting, so a dev or prod instance can never serve it. In test mode the setup token minted at
startup is also kept in memory (``app.state.test_setup_token``) so e2e can read it here instead
of scraping logs.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.services.errors import ApiError

router = APIRouter(prefix="/test", tags=["test"], include_in_schema=False)


@router.get("/setup-token")
async def setup_token(request: Request) -> dict[str, str]:
    if request.app.state.settings.app_env != "test":
        raise ApiError("not_found", "Not found.")
    token: str | None = getattr(request.app.state, "test_setup_token", None)
    if token is None:
        raise ApiError("not_found", "No setup token is pending.")
    return {"setup_token": token}
