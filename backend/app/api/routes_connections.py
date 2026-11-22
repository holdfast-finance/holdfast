"""``/api/connections``: Plaid Link (plan, token, exchange, resolve, abandon) and the list."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.deps import Ctx, CurrentPrincipal
from app.schemas.connections import (
    AbandonIn,
    ConnectionsOut,
    ExchangeIn,
    ExchangeOut,
    LinkPlanIn,
    LinkPlanOut,
    LinkTokenIn,
    LinkTokenOut,
    SlotOut,
)
from app.security.secret_token import SecretToken
from app.services.connections import ConnectionsService
from app.services.plaid_link import PlaidLinkService

router = APIRouter(prefix="/connections", tags=["connections"])


@router.get("")
async def list_connections(ctx: Ctx, principal: CurrentPrincipal) -> ConnectionsOut:
    return await ConnectionsService(ctx, principal).list()


@router.post("/plaid/link-plan")
async def link_plan(body: LinkPlanIn, ctx: Ctx, principal: CurrentPrincipal) -> LinkPlanOut:
    return await PlaidLinkService(ctx, principal).link_plan(body)


@router.post("/plaid/link-token")
async def link_token(body: LinkTokenIn, ctx: Ctx, principal: CurrentPrincipal) -> LinkTokenOut:
    return await PlaidLinkService(ctx, principal).link_token(body)


@router.post("/plaid/exchange")
async def exchange(body: ExchangeIn, ctx: Ctx, principal: CurrentPrincipal) -> ExchangeOut:
    token = SecretToken(body.public_token.get_secret_value())
    return await PlaidLinkService(ctx, principal).exchange(token, body.reservation_id)


@router.post("/plaid/reservations/{reservation_id}/resolve")
async def resolve_reservation(
    reservation_id: uuid.UUID, ctx: Ctx, principal: CurrentPrincipal
) -> SlotOut:
    return await PlaidLinkService(ctx, principal).resolve(reservation_id)


@router.post("/plaid/reservations/{reservation_id}/abandon")
async def abandon_reservation(
    reservation_id: uuid.UUID, body: AbandonIn, ctx: Ctx, principal: CurrentPrincipal
) -> SlotOut:
    return await PlaidLinkService(ctx, principal).abandon(reservation_id, body)
