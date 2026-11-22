"""``/api/plaid/setup``: the Plaid setup checklist and its live check."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import Ctx, CurrentPrincipal
from app.schemas.plaid import PlaidSetupOut
from app.services.plaid_setup import PlaidSetupService

router = APIRouter(prefix="/plaid", tags=["plaid"])


@router.get("/setup")
async def plaid_setup(ctx: Ctx, principal: CurrentPrincipal) -> PlaidSetupOut:
    return await PlaidSetupService(ctx, principal).status()


@router.post("/setup/check")
async def check_plaid_setup(ctx: Ctx, principal: CurrentPrincipal) -> PlaidSetupOut:
    return await PlaidSetupService(ctx, principal).check()
