"""Run the transfer matcher over a household date window and write confirmed pairs (PLAN 8.3, C4).

``pair_transfers`` works on a session the caller opened with ``scoped_session`` and commits
nothing. Reads follow that session's RLS context, so a person-scoped session only sees (and pairs)
accounts that person may see and write; the household-wide context belongs to system jobs only
(never a request handler). Pairs are written through ``LedgerWriter.apply_transfer_pair``, the one
writer, each inside a savepoint so a leg the session cannot write, or that another worker paired
first, is skipped without failing the rest.

Idempotent: paired rows are excluded from the next run, so a rerun pairs nothing new. Ambiguous
legs are **returned, not stored**: there is no review table yet (story C6 must persist them).
Candidates are loaded from ``MAX_DAYS`` before ``date_from`` to ``MAX_DAYS`` after ``date_to`` so a
leg at the edge of the window still finds its partner; a pair or ambiguity is reported only if at
least one of its legs posted inside the window.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.ledger_rules import LedgerRuleError
from app.domain.transfers import (
    MAX_DAYS,
    AmbiguousLeg,
    ProposedPair,
    TransferCandidate,
    match_transfers,
)
from app.models import Account, Transaction
from app.security.client_ip import IPAddress
from app.services.ledger_service import LedgerRetryableError, LedgerWriter


@dataclass(frozen=True)
class PairedTransfer:
    outflow_id: uuid.UUID
    inflow_id: uuid.UUID
    group_id: uuid.UUID


@dataclass(frozen=True)
class PairingReport:
    paired: tuple[PairedTransfer, ...]
    ambiguous: tuple[AmbiguousLeg, ...]
    skipped: tuple[ProposedPair, ...]


async def pair_transfers(
    session: AsyncSession,
    household_id: uuid.UUID,
    date_from: date,
    date_to: date,
    *,
    user_id: uuid.UUID | None = None,
    ip: IPAddress | None = None,
) -> PairingReport:
    if date_from > date_to:
        raise LedgerRuleError("the start date is after the end date")
    pad = timedelta(days=MAX_DAYS)
    rows = (
        await session.execute(
            select(Transaction, Account.type, Account.currency, Account.mask)
            .join(Account, Account.id == Transaction.account_id)
            .where(
                Transaction.household_id == household_id,
                Transaction.posted_date >= date_from - pad,
                Transaction.posted_date <= date_to + pad,
                Transaction.superseded_by_id.is_(None),
                Transaction.is_transfer.is_(False),
                Transaction.is_pending.is_(False),
            )
        )
    ).all()
    posted: dict[uuid.UUID, date] = {}
    cands: list[TransferCandidate] = []
    for t, acct_type, currency, mask in rows:
        posted[t.id] = t.posted_date
        cands.append(
            TransferCandidate(
                transaction_id=t.id,
                account_id=t.account_id,
                posted_date=t.posted_date,
                amount=t.amount,
                currency=currency,
                description=t.description_raw,
                account_type=acct_type,
                account_mask=mask,
            )
        )

    def inside(*ids: uuid.UUID) -> bool:
        return any(date_from <= posted[i] <= date_to for i in ids)

    result = match_transfers(cands)
    writer = LedgerWriter(session, household_id, user_id=user_id, ip=ip)
    paired: list[PairedTransfer] = []
    skipped: list[ProposedPair] = []
    for pair in result.pairs:
        if not inside(pair.outflow_id, pair.inflow_id):
            continue
        failure: LedgerRuleError | LedgerRetryableError | None = None
        group: uuid.UUID | None = None
        try:
            async with session.begin_nested():
                group = await writer.apply_transfer_pair(pair.outflow_id, pair.inflow_id)
        except (LedgerRuleError, LedgerRetryableError) as exc:
            failure = exc
        if failure is not None or group is None:
            skipped.append(pair)
        else:
            paired.append(PairedTransfer(pair.outflow_id, pair.inflow_id, group))
    ambiguous = tuple(a for a in result.ambiguous if inside(a.transaction_id, *a.candidate_ids))
    return PairingReport(tuple(paired), ambiguous, tuple(skipped))
