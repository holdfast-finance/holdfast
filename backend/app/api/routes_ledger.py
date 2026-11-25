"""``/api/accounts``, ``/api/transactions`` and ``/api/transactions/summary`` (read only)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.api.deps import Ctx, CurrentPrincipal
from app.schemas.common import MoneyStr
from app.schemas.ledger import (
    AccountList,
    CategoryChoiceIn,
    CategorySource,
    Direction,
    TransactionOut,
    TransactionPage,
    TransactionSummary,
)
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
    category_id: list[uuid.UUID] | None = None,
    uncategorized: bool = False,
    category_source: CategorySource | None = None,
    transfer: Literal["only", "exclude"] | None = None,
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
        category_ids=category_id or [],
        uncategorized=uncategorized,
        category_source=category_source,
        transfer=transfer,
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
    category_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    uncategorized: bool = False,
    category_source: CategorySource | None = None,
    transfer: Literal["only", "exclude"] | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
) -> TransactionPage:
    """List live transactions. Category filters act on the parent row only: a split parent
    shows `has_splits=true` and is matched by its own category, never by its parts."""
    f = _filters(
        account_id,
        date_from,
        date_to,
        q,
        direction,
        min_amount,
        max_amount,
        exclude_transfers,
        category_id,
        uncategorized,
        category_source,
        transfer,
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
    category_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    uncategorized: bool = False,
    category_source: CategorySource | None = None,
    transfer: Literal["only", "exclude"] | None = None,
) -> TransactionSummary:
    """Per-currency totals. Category filters act on the parent row only; split parts are not
    summed separately. Superseded rows are excluded."""
    f = _filters(
        account_id,
        date_from,
        date_to,
        q,
        direction,
        min_amount,
        max_amount,
        exclude_transfers,
        category_id,
        uncategorized,
        category_source,
        transfer,
    )
    return await LedgerService(ctx, principal).summary(f)


@router.get("/transactions/{transaction_id}")
async def get_transaction(
    transaction_id: uuid.UUID, ctx: Ctx, principal: CurrentPrincipal
) -> TransactionOut:
    return await LedgerService(ctx, principal).get(transaction_id)


@router.put("/transactions/{transaction_id}/category")
async def set_transaction_category(
    transaction_id: uuid.UUID, body: CategoryChoiceIn, ctx: Ctx, principal: CurrentPrincipal
) -> TransactionOut:
    """Set or clear a category. 404 when the row is missing, invisible or not writable; 409 when
    the row is superseded or is a split parent (categorise its parts instead)."""
    return await LedgerService(ctx, principal).set_category(transaction_id, body.category_id)


@router.post("/transactions/{transaction_id}/category/release")
async def release_transaction_category(
    transaction_id: uuid.UUID, ctx: Ctx, principal: CurrentPrincipal
) -> TransactionOut:
    return await LedgerService(ctx, principal).release_category(transaction_id)
