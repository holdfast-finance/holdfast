"""User lookups and the JSON views of users and households.

``app_user`` and ``webauthn_credential`` have no RLS; ``person`` and ``household`` do, so the
serializers need a session scoped to the user's household.
"""

from __future__ import annotations

import uuid

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppUser, Household, Person, WebauthnCredential
from app.schemas.auth import HouseholdOut, SessionOut, UserOut, UserPersonOut


async def get_user(s: AsyncSession, user_id: uuid.UUID, *, for_update: bool = False) -> AppUser:
    stmt = select(AppUser).where(AppUser.id == user_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await s.execute(stmt)).scalar_one()


async def household_ids(s: AsyncSession) -> list[uuid.UUID]:
    """Every household with at least one login, for worker sweeps (unscoped session).

    ``household`` has RLS, so ``app_user.household_id`` is the one place the app can list
    tenants. A household with no login can't enqueue from the API, so a sweep loses nothing.
    """
    stmt = select(AppUser.household_id).distinct().order_by(AppUser.household_id)
    return list((await s.execute(stmt)).scalars().all())


async def has_passkey(s: AsyncSession, user_id: uuid.UUID) -> bool:
    found = await s.execute(select(exists().where(WebauthnCredential.user_id == user_id)))
    return bool(found.scalar_one())


async def has_any_credential(s: AsyncSession, user: AppUser) -> bool:
    """A passkey or enabled TOTP. Zero means the session came from a setup token or invite."""
    return user.totp_enabled or await has_passkey(s, user.id)


async def user_out(scoped: AsyncSession, user: AppUser) -> UserOut:
    person = None
    if user.person_id is not None:
        p = (
            await scoped.execute(select(Person).where(Person.id == user.person_id))
        ).scalar_one_or_none()
        if p is not None:
            person = UserPersonOut(id=p.id, display_name=p.display_name, filing_role=p.filing_role)  # type: ignore[arg-type]
    return UserOut(
        id=user.id,
        email=user.email,
        role=user.role,  # type: ignore[arg-type]
        person=person,
        totp_enabled=user.totp_enabled,
        has_password=user.password_hash is not None,
    )


async def household_out(scoped: AsyncSession) -> HouseholdOut:
    h = (await scoped.execute(select(Household))).scalar_one()
    return HouseholdOut(id=h.id, name=h.name)


async def session_out(scoped: AsyncSession, user: AppUser) -> SessionOut:
    return SessionOut(user=await user_out(scoped, user), household=await household_out(scoped))
