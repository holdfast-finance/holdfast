"""``PATCH /api/accounts/{id}`` (A1). Listing stays on the ledger routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.deps import Ctx, CurrentPrincipal
from app.schemas.accounts import AccountOwnershipIn
from app.schemas.ledger import AccountOut
from app.services.accounts import AccountService

router = APIRouter(tags=["accounts"])


@router.patch("/accounts/{account_id}")
async def change_account_ownership(
    account_id: uuid.UUID, body: AccountOwnershipIn, ctx: Ctx, principal: CurrentPrincipal
) -> AccountOut:
    return await AccountService(ctx, principal).change_ownership(account_id, body)
