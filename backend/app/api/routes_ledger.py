"""``/api/accounts``, ``/api/transactions`` and ``/api/transactions/summary`` (read only)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import Ctx, CurrentPrincipal
from app.schemas.common import MoneyStr
from app.schemas.ledger import AccountList, Direction, TransactionPage, TransactionSummary
from app.services.ledger import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MAX_QUERY_LENGTH,
    LedgerService,
    TransactionFilters,
)

router = APIRouter(tags=["ledger"])


def _filters(
    account_id: list[uuid.UUID] | None,
    date_from: date | None,
    date_to: date | None,
    q: str | None,
    direction: Direction | None,
    min_amount: MoneyStr | None,
    max_amount: MoneyStr | None,
    exclude_transfers: bool,
) -> TransactionFilters:
    return TransactionFilters(
        account_ids=account_id or [],
        date_from=date_from,
        date_to=date_to,
        q=q or None,
        direction=direction,
        min_amount=min_amount,
        max_amount=max_amount,
        exclude_transfers=exclude_transfers,
    )


@router.get("/accounts")
async def list_accounts(ctx: Ctx, principal: CurrentPrincipal) -> AccountList:
    return await LedgerService(ctx, principal).accounts()


@router.get("/transactions")
async def list_transactions(
    ctx: Ctx,
    principal: CurrentPrincipal,
    account_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: Annotated[str | None, Query(max_length=MAX_QUERY_LENGTH)] = None,
    direction: Direction | None = None,
    min_amount: MoneyStr | None = None,
    max_amount: MoneyStr | None = None,
    exclude_transfers: bool = False,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
) -> TransactionPage:
    f = _filters(
        account_id, date_from, date_to, q, direction, min_amount, max_amount, exclude_transfers
    )
    return await LedgerService(ctx, principal).transactions(f, limit=limit, cursor=cursor)


@router.get("/transactions/summary")
async def transactions_summary(
    ctx: Ctx,
    principal: CurrentPrincipal,
    account_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: Annotated[str | None, Query(max_length=MAX_QUERY_LENGTH)] = None,
    direction: Direction | None = None,
    min_amount: MoneyStr | None = None,
    max_amount: MoneyStr | None = None,
    exclude_transfers: bool = False,
) -> TransactionSummary:
    f = _filters(
        account_id, date_from, date_to, q, direction, min_amount, max_amount, exclude_transfers
    )
    return await LedgerService(ctx, principal).summary(f)
