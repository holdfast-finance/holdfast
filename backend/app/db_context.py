"""Row-level-security context for database sessions (ADR 0008).

Every household-scoped table has RLS keyed on two transaction-local settings:

- ``app.household_id``: the tenant. Unset means the session sees **no** household rows.
- ``app.person_id``: the viewing person, for per-person account visibility (PLAN 20.3).

``scoped_session`` sets both at the start of **every** transaction the session opens (an
``after_begin`` hook), so a ``commit()`` in the middle of a unit of work never drops the context.

Background jobs that must write to every account in a household, whoever owns it (bank sync,
imports), pass ``household_wide=True``. That widens person visibility only; the household boundary
still holds. Request handlers never set it (``tests/models/test_household_wide_guard.py`` fails
if ``household_wide=True`` appears under ``app/api/``).

Per-person rules (ADR 0008): a person *reads* accounts by visibility but *writes* only their own
or joint accounts and those accounts' details. Agent output with a ``person_id`` is visible only
to that person. The app role can't delete a household.

Identity tables (``app_user``, ``webauthn_credential``, ``auth_session``, ``totp_recovery_code``)
have no RLS because login happens before any household is known. Use ``unscoped_session`` for
them, and for ``create_household()``. An unscoped session sees nothing in household tables.

Usage::

    async with scoped_session(factory, household_id, person_id) as session:
        rows = (await session.execute(select(Account))).scalars().all()
    # committed on clean exit, rolled back on error

    # FastAPI: wrap in a dependency that resolves the current user first
    async def db(request: Request, user: CurrentUser = Depends(current_user)):
        async for session in household_session(request, user.household_id, user.person_id):
            yield session
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Request
from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, SessionTransaction

SET_CONTEXT_SQL = text(
    "SELECT set_config('app.household_id', :household_id, true),"
    " set_config('app.person_id', :person_id, true),"
    " set_config('app.household_wide', :household_wide, true)"
)


def _context_params(
    household_id: uuid.UUID, person_id: uuid.UUID | None, household_wide: bool
) -> dict[str, str]:
    # str(UUID) is the only thing that reaches the GUC: no caller-controlled text.
    return {
        "household_id": str(uuid.UUID(str(household_id))),
        "person_id": "" if person_id is None else str(uuid.UUID(str(person_id))),
        "household_wide": "on" if household_wide else "",
    }


def bind_context(
    session: AsyncSession,
    household_id: uuid.UUID,
    person_id: uuid.UUID | None,
    *,
    household_wide: bool = False,
) -> None:
    """Make every transaction ``session`` begins set the RLS context first.

    Call before the session runs its first statement.
    """
    if session.in_transaction():
        raise RuntimeError("bind_context must run before the session's first statement")
    params = _context_params(household_id, person_id, household_wide)

    def _set_context(_s: Session, _t: SessionTransaction, connection: Connection) -> None:
        connection.execute(SET_CONTEXT_SQL, params)

    event.listen(session.sync_session, "after_begin", _set_context)
    session.info["household_id"] = household_id
    session.info["person_id"] = person_id


async def apply_context(
    session: AsyncSession,
    household_id: uuid.UUID,
    person_id: uuid.UUID | None,
    *,
    household_wide: bool = False,
) -> None:
    """Set the RLS context for the session's **current** transaction only.

    For one-off cases such as first-run setup (``create_household`` then insert the person in the
    same transaction). It is gone after commit; prefer ``scoped_session`` everywhere else.
    """
    await session.execute(SET_CONTEXT_SQL, _context_params(household_id, person_id, household_wide))


async def list_household_ids(session: AsyncSession) -> list[uuid.UUID]:
    """Every household id, from an unscoped session (SECURITY DEFINER; ids only, no names)."""
    rows = await session.execute(text("SELECT list_household_ids()"))
    return [r[0] for r in rows]


async def _commit_or_rollback(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    try:
        yield session
    except BaseException:
        await session.rollback()
        raise
    else:
        await session.commit()


@asynccontextmanager
async def scoped_session(
    factory: async_sessionmaker[AsyncSession],
    household_id: uuid.UUID,
    person_id: uuid.UUID | None,
    *,
    household_wide: bool = False,
) -> AsyncIterator[AsyncSession]:
    """A session whose transactions run as ``household_id`` / ``person_id``.

    Commits on clean exit, rolls back on any exception. ``person_id=None`` sees shared, joint
    and summary-only accounts but no private ones (unless ``household_wide``).
    """
    async with factory() as session:
        bind_context(session, household_id, person_id, household_wide=household_wide)
        async for s in _commit_or_rollback(session):
            yield s


@asynccontextmanager
async def unscoped_session(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """A session with **no** household context: identity tables and ``create_household`` only.

    RLS makes every household-scoped table look empty here (fail closed).
    """
    async with factory() as session:
        async for s in _commit_or_rollback(session):
            yield s


async def household_session(
    request: Request,
    household_id: uuid.UUID,
    person_id: uuid.UUID | None,
) -> AsyncIterator[AsyncSession]:
    """FastAPI-friendly generator over ``app.state.session_factory``. See the module docstring."""
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with scoped_session(factory, household_id, person_id) as session:
        yield session


async def create_household(session: AsyncSession, name: str) -> uuid.UUID:
    """Call the ``create_household(name)`` SECURITY DEFINER function. Works without context.

    The new household is not visible to ``session`` afterwards unless it is scoped to it. For an
    atomic first-run setup, call ``apply_context(session, household_id, None)`` next in the same
    transaction, then insert the person and user.
    """
    result: Any = await session.execute(text("SELECT create_household(:name)"), {"name": name})
    household_id: uuid.UUID = result.scalar_one()
    return household_id
