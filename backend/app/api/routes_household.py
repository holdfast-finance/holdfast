"""``/api/household*``, ``/api/invites/accept`` and ``/api/audit``."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Response, status

from app.api.deps import Ctx, CurrentPrincipal, set_session_cookie
from app.db_context import scoped_session
from app.schemas.auth import HouseholdOut, InviteAcceptIn, InviteIn, InviteOut, SessionOut
from app.schemas.household import (
    AuditPage,
    HouseholdUpdate,
    PeopleList,
    PersonCreate,
    PersonOut,
    PersonUpdate,
)
from app.services import audit, invites
from app.services.household import HouseholdService, require_owner

router = APIRouter(tags=["household"])


@router.get("/household")
async def get_household(ctx: Ctx, principal: CurrentPrincipal) -> HouseholdOut:
    return await HouseholdService(ctx, principal).get()


@router.patch("/household")
async def rename_household(
    body: HouseholdUpdate, ctx: Ctx, principal: CurrentPrincipal
) -> HouseholdOut:
    return await HouseholdService(ctx, principal).rename(body.name)


@router.get("/household/people")
async def list_people(ctx: Ctx, principal: CurrentPrincipal) -> PeopleList:
    return await HouseholdService(ctx, principal).people()


@router.post("/household/people", status_code=status.HTTP_201_CREATED)
async def create_person(body: PersonCreate, ctx: Ctx, principal: CurrentPrincipal) -> PersonOut:
    return await HouseholdService(ctx, principal).create_person(body)


@router.patch("/household/people/{person_id}")
async def update_person(
    person_id: uuid.UUID, body: PersonUpdate, ctx: Ctx, principal: CurrentPrincipal
) -> PersonOut:
    return await HouseholdService(ctx, principal).update_person(person_id, body)


@router.delete("/household/people/{person_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_person(person_id: uuid.UUID, ctx: Ctx, principal: CurrentPrincipal) -> None:
    await HouseholdService(ctx, principal).delete_person(person_id)


@router.post("/household/people/{person_id}/invite")
async def invite_person(
    person_id: uuid.UUID, body: InviteIn, ctx: Ctx, principal: CurrentPrincipal
) -> InviteOut:
    url = await invites.create_invite(ctx, principal, person_id, body.email)
    return InviteOut(invite_url=url)


@router.post("/invites/accept")
async def accept_invite(body: InviteAcceptIn, response: Response, ctx: Ctx) -> SessionOut:
    token, out = await invites.accept_invite(ctx, body.token)
    set_session_cookie(response, token)
    return out


@router.get("/audit")
async def audit_page(
    ctx: Ctx,
    principal: CurrentPrincipal,
    limit: int = Query(default=audit.DEFAULT_LIMIT, ge=1, le=audit.MAX_LIMIT),
    before: str | None = Query(default=None, max_length=128),
) -> AuditPage:
    require_owner(principal)
    async with scoped_session(ctx.factory, principal.household_id, principal.person_id) as s:
        return await audit.page(s, limit=limit, before=before)
