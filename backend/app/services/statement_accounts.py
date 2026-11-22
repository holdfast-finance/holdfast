"""The one place a statement finds (or creates) its account: by ``account.statement_key``.

ADR 0033 §4. A statement block's key (profile, last four digits, block hash) is stored on the
account it created as ``statement_key``, unique per household and final once set. The lookup
ignores ``source``, so a statement re-imported after a connector adopted the account (``source``
is then ``plaid`` / ``simplefin``) lands on the same row instead of making a twin. Statement
import and the CSV importer (I2b) both call :func:`resolve_statement_account`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account


class AccountNotVisibleError(RuntimeError):
    """The imported account exists but this session cannot see it (per-person visibility).

    Never create a twin: the unique key blocks it. Import as a household-wide job, or as the
    account's owner. The message carries no account data.
    """


@dataclass(frozen=True)
class NewStatementAccount:
    """What a statement knows about an account it creates. ``mask``: at most four digits."""

    name: str
    type: str
    mask: str | None


async def resolve_statement_account(
    s: AsyncSession,
    household_id: uuid.UUID,
    statement_key: str,
    new: NewStatementAccount,
    person_id: uuid.UUID | None,
    *,
    joint: bool = False,
) -> Account:
    """The household's account with ``statement_key``, created as an ``import`` account if none
    exists. Race-safe on the ``(household_id, statement_key)`` unique key.

    A person importing owns the new account and it starts private (ADR 0008), unless ``joint``
    (an upload the person marked as the household's); a household-wide job (no person) creates a
    joint, shared account. The key should say whose account it is when people can share a
    source (statement import adds the owner for CSV/OFX), so two people never resolve to one.
    Raises :class:`AccountNotVisibleError` when the account exists but this session can't see it.
    """
    where = (Account.household_id == household_id, Account.statement_key == statement_key)
    acct = (await s.execute(select(Account).where(*where))).scalar_one_or_none()
    if acct is not None:
        return acct
    await s.execute(
        pg_insert(Account)
        .values(
            id=uuid.uuid4(),
            household_id=household_id,
            source="import",
            statement_key=statement_key,
            external_account_id=statement_key,
            name=new.name,
            mask=new.mask,
            type=new.type,
            owner_person_id=None if joint else person_id,
            visibility="private" if person_id and not joint else "shared",
        )
        .on_conflict_do_nothing(index_elements=["household_id", "statement_key"])
    )
    # Lost a race (or the row is hidden from this session): read the winner. Still nothing means
    # per-person visibility hides it, and we must not make a twin.
    acct = (await s.execute(select(Account).where(*where))).scalar_one_or_none()
    if acct is None:
        raise AccountNotVisibleError("imported account is not visible to this session")
    return acct
