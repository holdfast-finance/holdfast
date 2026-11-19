"""app.db_context: scoped / unscoped sessions over the real RLS policies (async, holdfast_app)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import create_session_factory
from app.db_context import (
    apply_context,
    create_household,
    household_session,
    scoped_session,
    unscoped_session,
)
from app.models import Account, AppUser, AuditLog, Household, Person
from tests.models.conftest import Seed, delete_household

pytestmark = pytest.mark.services


@pytest.fixture
async def factory(app_url: str, migrated: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(app_url)
    yield create_session_factory(engine)
    await engine.dispose()


async def _count(session: AsyncSession, model: Any) -> int:
    return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


async def test_scoped_session_sees_only_its_household(
    factory: async_sessionmaker[AsyncSession], two_households: tuple[Seed, Seed]
) -> None:
    a, _ = two_households
    async with scoped_session(factory, a.household_id, a.person_ids[0]) as s:
        names = (await s.execute(select(Household.name))).scalars().all()
        assert names == ["Example Household A"]
        assert await _count(s, Person) == 2


async def test_context_survives_mid_session_commit(
    factory: async_sessionmaker[AsyncSession], two_households: tuple[Seed, Seed]
) -> None:
    a, _ = two_households
    async with scoped_session(factory, a.household_id, a.person_ids[0]) as s:
        assert await _count(s, Person) == 2
        await s.commit()
        assert await _count(s, Person) == 2  # new transaction, context re-applied


async def test_unscoped_session_sees_no_household_data(
    factory: async_sessionmaker[AsyncSession], two_households: tuple[Seed, Seed]
) -> None:
    async with unscoped_session(factory) as s:
        assert await _count(s, Household) == 0
        assert await _count(s, Account) == 0


async def test_rollback_on_error(
    factory: async_sessionmaker[AsyncSession], two_households: tuple[Seed, Seed]
) -> None:
    a, _ = two_households
    with pytest.raises(RuntimeError, match="boom"):
        async with scoped_session(factory, a.household_id, None) as s:
            s.add(Person(household_id=a.household_id, display_name="Temp", filing_role="dependent"))
            await s.flush()
            raise RuntimeError("boom")
    async with scoped_session(factory, a.household_id, None) as s:
        assert await _count(s, Person) == 2


async def test_household_session_dependency(
    factory: async_sessionmaker[AsyncSession], two_households: tuple[Seed, Seed]
) -> None:
    a, _ = two_households
    request: Any = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(session_factory=factory))
    )
    gen = household_session(request, a.household_id, a.person_ids[0])
    session = await anext(gen)
    assert await _count(session, Person) == 2
    with pytest.raises(StopAsyncIteration):
        await anext(gen)


async def test_atomic_first_run_setup(
    factory: async_sessionmaker[AsyncSession], owner_engine: Any
) -> None:
    """create_household -> apply_context -> person + owner user, in one transaction."""
    email = f"owner-{uuid.uuid4().hex[:8]}@example.com"
    async with unscoped_session(factory) as s:
        hid = await create_household(s, "Example First Run")
        await apply_context(s, hid, None)
        person = Person(household_id=hid, display_name="Alex Example", filing_role="primary")
        s.add(person)
        await s.flush()
        s.add(AppUser(person_id=person.id, email=email, role="owner"))
        s.add(AuditLog(household_id=hid, action="setup.complete", detail={"step": "household"}))
    async with unscoped_session(factory) as s:
        user = (await s.execute(select(AppUser).where(AppUser.email == email))).scalar_one()
        assert user.role == "owner"
        assert user.totp_enabled is False
    async with scoped_session(factory, hid, user.person_id) as s:
        assert (await s.execute(select(Person.display_name))).scalar_one() == "Alex Example"
        assert await _count(s, AuditLog) == 1
    delete_household(owner_engine, hid)
    async with unscoped_session(factory) as s:
        gone = (await s.execute(select(AppUser).where(AppUser.email == email))).first()
        assert gone is None  # person deleted -> user cascades


async def test_orm_pre_auth_audit_insert(factory: async_sessionmaker[AsyncSession]) -> None:
    """The ORM must not ask for RETURNING on audit_log (pre-auth rows fail the SELECT policy)."""
    async with unscoped_session(factory) as s:
        s.add(AuditLog(household_id=None, action="login.failed", detail={"method": "passkey"}))


async def test_money_round_trips_as_decimal(
    factory: async_sessionmaker[AsyncSession], two_households: tuple[Seed, Seed]
) -> None:
    a, _ = two_households
    async with scoped_session(factory, a.household_id, a.person_ids[0]) as s:
        amount = (
            await s.execute(
                text('SELECT amount FROM "transaction" WHERE id = :id'),
                {"id": a.rows["transaction"]["id"]},
            )
        ).scalar_one()
    assert amount == Decimal("-42.10")
    assert isinstance(amount, Decimal)


async def test_rejects_non_uuid_context(factory: async_sessionmaker[AsyncSession]) -> None:
    bad: Any = "00000000-0000-0000-0000-000000000000'; DROP TABLE account; --"
    with pytest.raises(ValueError):
        async with scoped_session(factory, bad, None):
            pass
