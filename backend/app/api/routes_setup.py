"""``/api/setup*``: first-run claim of an empty instance."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.api.deps import Ctx, set_session_cookie
from app.schemas.auth import SessionOut, SetupIn, SetupStatusOut
from app.services import setup

router = APIRouter(prefix="/setup", tags=["setup"])


@router.get("/status")
async def setup_status(ctx: Ctx) -> SetupStatusOut:
    return SetupStatusOut(needs_setup=await setup.setup_state(ctx.factory) != "complete")


@router.post("", status_code=status.HTTP_201_CREATED)
async def run_setup(body: SetupIn, response: Response, ctx: Ctx) -> SessionOut:
    token, out = await setup.run_setup(ctx, body)
    set_session_cookie(response, token)
    return out
