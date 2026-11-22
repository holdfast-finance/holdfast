"""``GET /connections``: connections and their accounts, read through row-level security.

A connection whose accounts are all hidden from the reader shows no names or masks. Counts of
link decisions are household-wide numbers only.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db_context import scoped_session
from app.models import Account, BankConnection, ConnectorAccountLink, PlaidLinkSlot
from app.schemas.connections import (
    ConnectionAccountOut,
    ConnectionOut,
    ConnectionsOut,
    OpenReservationOut,
)
from app.services.context import RequestContext
from app.services.sessions import Principal


async def connection_out(s: AsyncSession, conn: BankConnection) -> ConnectionOut:
    states = (
        await s.execute(
            select(ConnectorAccountLink.state).where(
                ConnectorAccountLink.bank_connection_id == conn.id
            )
        )
    ).scalars()
    counts = {"pending": 0, "mapped": 0, "ignored": 0}
    for state in states:
        counts[state] = counts.get(state, 0) + 1
    # RLS decides which accounts this reader may see.
    accounts = (
        await s.execute(
            select(Account)
            .where(Account.bank_connection_id == conn.id, Account.is_hidden.is_(False))
            .order_by(Account.name, Account.id)
        )
    ).scalars()
    flag = await s.scalar(
        select(PlaidLinkSlot.flag).where(PlaidLinkSlot.bank_connection_id == conn.id)
    )
    return ConnectionOut.model_validate(
        {
            "id": conn.id,
            "provider": conn.provider,
            "env": conn.env,
            "status": conn.status,
            "created_at": conn.created_at,
            "last_synced_at": conn.last_synced_at,
            "pending_accounts": counts["pending"],
            "mapped_accounts": counts["mapped"],
            "ignored_accounts": counts["ignored"],
            "slot_flag": flag,
            "accounts": [
                ConnectionAccountOut(id=a.id, name=a.name, mask=a.mask, type=a.type)
                for a in accounts
            ],
        }
    )


async def connection_by_id(s: AsyncSession, connection_id: uuid.UUID) -> ConnectionOut | None:
    conn = await s.get(BankConnection, connection_id)
    return None if conn is None else await connection_out(s, conn)


class ConnectionsService:
    def __init__(self, ctx: RequestContext, principal: Principal) -> None:
        self.ctx = ctx
        self.principal = principal

    async def list(self) -> ConnectionsOut:
        async with scoped_session(
            self.ctx.factory, self.principal.household_id, self.principal.person_id
        ) as s:
            conns = (
                (await s.execute(select(BankConnection).order_by(BankConnection.created_at)))
                .scalars()
                .all()
            )
            out = [await connection_out(s, c) for c in conns]
            reservations: list[OpenReservationOut] = []
            if self.principal.is_owner:
                rows = (
                    (
                        await s.execute(
                            select(PlaidLinkSlot)
                            .where(PlaidLinkSlot.state.in_(("reserved", "unknown")))
                            .order_by(PlaidLinkSlot.created_at)
                        )
                    )
                    .scalars()
                    .all()
                )
                reservations = [
                    OpenReservationOut.model_validate(
                        {"id": r.id, "state": r.state, "env": r.env, "created_at": r.created_at}
                    )
                    for r in rows
                ]
        return ConnectionsOut(connections=out, open_reservations=reservations)
