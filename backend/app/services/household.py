"""Household and people (owner-only writes). Every query runs under the caller's RLS context."""

from __future__ import annotations

import uuid

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError

from app.db_context import scoped_session
from app.models import AppUser, Household, Person
from app.schemas.auth import HouseholdOut
from app.schemas.household import PeopleList, PersonCreate, PersonOut, PersonUpdate
from app.services import audit
from app.services.context import RequestContext
from app.services.errors import ApiError
from app.services.sessions import Principal


def require_owner(principal: Principal) -> None:
    if not principal.is_owner:
        raise ApiError("forbidden", "Only the household owner can do that.")


def _person_out(p: Person, has_account: bool) -> PersonOut:
    return PersonOut.model_validate(
        {
            "id": p.id,
            "display_name": p.display_name,
            "filing_role": p.filing_role,
            "has_account": has_account,
        }
    )


class HouseholdService:
    def __init__(self, ctx: RequestContext, principal: Principal) -> None:
        self.ctx = ctx
        self.principal = principal

    async def get(self) -> HouseholdOut:
        async with scoped_session(
            self.ctx.factory, self.principal.household_id, self.principal.person_id
        ) as s:
            h = (await s.execute(select(Household))).scalar_one()
            return HouseholdOut(id=h.id, name=h.name)

    async def rename(self, name: str) -> HouseholdOut:
        require_owner(self.principal)
        async with scoped_session(
            self.ctx.factory, self.principal.household_id, self.principal.person_id
        ) as s:
            h = (await s.execute(select(Household))).scalar_one()
            h.name = name
            audit.add(
                s,
                "household.renamed",
                entity="household",
                entity_id=h.id,
                household_id=self.principal.household_id,
                user_id=self.principal.user_id,
                ip=self.ctx.ip,
            )
            return HouseholdOut(id=h.id, name=name)

    async def people(self) -> PeopleList:
        async with scoped_session(
            self.ctx.factory, self.principal.household_id, self.principal.person_id
        ) as s:
            has_account = exists().where(AppUser.person_id == Person.id)
            rows = (
                await s.execute(
                    select(Person, has_account.label("has_account")).order_by(
                        Person.created_at, Person.id
                    )
                )
            ).all()
            return PeopleList(items=[_person_out(p, bool(acct)) for p, acct in rows])

    async def create_person(self, body: PersonCreate) -> PersonOut:
        require_owner(self.principal)
        async with scoped_session(
            self.ctx.factory, self.principal.household_id, self.principal.person_id
        ) as s:
            person = Person(
                household_id=self.principal.household_id,
                display_name=body.display_name,
                filing_role=body.filing_role,
            )
            s.add(person)
            await s.flush()
            audit.add(
                s,
                "household.person.created",
                entity="person",
                entity_id=person.id,
                household_id=self.principal.household_id,
                user_id=self.principal.user_id,
                ip=self.ctx.ip,
            )
            return _person_out(person, False)

    async def update_person(self, person_id: uuid.UUID, body: PersonUpdate) -> PersonOut:
        require_owner(self.principal)
        async with scoped_session(
            self.ctx.factory, self.principal.household_id, self.principal.person_id
        ) as s:
            person = (
                await s.execute(select(Person).where(Person.id == person_id))
            ).scalar_one_or_none()
            if person is None:
                raise ApiError("not_found", "No such person.")
            if body.display_name is not None:
                person.display_name = body.display_name
            if body.filing_role is not None:
                person.filing_role = body.filing_role
            has_account = (
                await s.execute(select(exists().where(AppUser.person_id == person.id)))
            ).scalar_one()
            audit.add(
                s,
                "household.person.updated",
                entity="person",
                entity_id=person.id,
                household_id=self.principal.household_id,
                user_id=self.principal.user_id,
                ip=self.ctx.ip,
            )
            return _person_out(person, bool(has_account))

    async def delete_person(self, person_id: uuid.UUID) -> None:
        require_owner(self.principal)
        try:
            async with scoped_session(
                self.ctx.factory, self.principal.household_id, self.principal.person_id
            ) as s:
                person = (
                    await s.execute(select(Person).where(Person.id == person_id))
                ).scalar_one_or_none()
                if person is None:
                    raise ApiError("not_found", "No such person.")
                if (
                    await s.execute(select(exists().where(AppUser.person_id == person.id)))
                ).scalar_one():
                    raise ApiError(
                        "conflict", "This person has an account. Remove their access first."
                    )
                await s.delete(person)
                await s.flush()
                audit.add(
                    s,
                    "household.person.deleted",
                    entity="person",
                    entity_id=person_id,
                    household_id=self.principal.household_id,
                    user_id=self.principal.user_id,
                    ip=self.ctx.ip,
                )
        except IntegrityError:
            # account.owner_person_id is NO ACTION (ADR 0008): their accounts must move first.
            raise ApiError(
                "conflict", "This person still owns accounts. Reassign them first."
            ) from None
