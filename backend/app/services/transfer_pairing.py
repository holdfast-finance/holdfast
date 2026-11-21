"""Run the transfer matcher over a household date window and write confirmed pairs (PLAN 8.3, C4).

``pair_transfers`` works on a session the caller opened with ``scoped_session`` and commits
nothing. Reads follow that session's RLS context, so a person-scoped session only sees (and pairs)
accounts that person may see and write. Pairs are written through
``LedgerWriter.apply_transfer_pair``, the one writer, each inside a savepoint so a leg the session
cannot write, or that changed since it was loaded, is skipped without failing the rest. The call
needs an acting ``user_id`` or a household-wide system-job session; a request handler cannot run
it "as the system". ``job_run_id`` goes into the audit detail of each auto-pair; it may be
None today, but C6 must require it whenever ``user_id`` is None (a scheduled caller).
The household-wide check reads the database setting, not ``session.info``.

A pair between a private account and a joint one cannot be undone by the other joint member
(``unmark_transfer`` needs both legs writable). C6 must let either owner dissolve a pair that
touches their leg, or send such pairs to suggestions.

**NOT SAFE TO SCHEDULE YET.** A person who undoes an automatic pair (``unmark_transfer``) would
have it re-paired on the next run, because nothing records the rejection. Story C6 must add a
rejection record (pair-level "not a transfer", needs a migration) and make this function honor it
before any scheduled caller exists. Until then nothing registers it as a job and
``tests/ledger/test_transfer_pairing_guard.py`` fails if it, or ``apply_transfer_pair``, is
referenced from the job registry or ``app/api/``. As a partial guard, rows a person categorized or
split are never auto-paired (they come back as suggestions).

What is written: only confirmed pairs with at least one wording or mask hint, between accounts
one person could undo. Everything else is returned, never written: ``suggested`` (a likely pair
without a hint, a refund candidate, a row the person treated as spending, accounts of two
different owners) and ``ambiguous`` (ties).

Idempotent: paired rows leave the candidate pool, so a rerun pairs nothing new. The result does
not depend on the window: candidates load ``2 * MAX_DAYS`` beyond it, and anything whose legs
lie outside the window within ``MAX_DAYS`` of the load edge is dropped.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db_context import is_household_wide
from app.domain.ledger_rules import LedgerRuleError
from app.domain.transfers import (
    MAX_DAYS,
    AmbiguousLeg,
    ProposedPair,
    SuggestedPair,
    TransferCandidate,
    match_transfers,
)
from app.models import Account, Transaction, TransactionSplit
from app.security.client_ip import IPAddress
from app.services.ledger_service import LedgerRetryableError, LedgerWriter


@dataclass(frozen=True)
class PairedTransfer:
    outflow_id: uuid.UUID
    inflow_id: uuid.UUID
    group_id: uuid.UUID


@dataclass(frozen=True)
class PairingReport:
    """What a run did and found.

    A household-wide run reports ids from every person's private accounts, so a report must never
    be returned over an API. C6 must store ``suggested`` and ``ambiguous`` per person, with an RLS
    check, and show each person only legs on accounts they may see.
    """

    paired: tuple[PairedTransfer, ...]
    suggested: tuple[SuggestedPair, ...]
    ambiguous: tuple[AmbiguousLeg, ...]
    skipped: tuple[ProposedPair, ...]


async def pair_transfers(
    session: AsyncSession,
    household_id: uuid.UUID,
    date_from: date,
    date_to: date,
    *,
    user_id: uuid.UUID | None = None,
    job_run_id: uuid.UUID | None = None,
    ip: IPAddress | None = None,
) -> PairingReport:
    if date_from > date_to:
        raise LedgerRuleError("the start date is after the end date")
    writer = LedgerWriter(session, household_id, user_id=user_id, ip=ip)  # fails fast on mismatch
    if user_id is None and not await is_household_wide(session):
        raise LedgerRuleError("pairing needs an acting user or a system job session")
    pad = timedelta(days=2 * MAX_DAYS)
    near = timedelta(days=MAX_DAYS)
    load_from, load_to = date_from - pad, date_to + pad
    has_splits = exists().where(TransactionSplit.transaction_id == Transaction.id)
    rows = (
        await session.execute(
            select(
                Transaction,
                Account.type,
                Account.currency,
                Account.mask,
                Account.owner_person_id,
                has_splits,
            )
            .join(Account, Account.id == Transaction.account_id)
            .where(
                Transaction.household_id == household_id,
                Transaction.posted_date >= load_from,
                Transaction.posted_date <= load_to,
                Transaction.superseded_by_id.is_(None),
                Transaction.is_transfer.is_(False),
            )
        )
    ).all()
    posted: dict[uuid.UUID, date] = {}
    cands: list[TransferCandidate] = []
    for t, acct_type, currency, mask, owner, split in rows:
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
                is_pending=t.is_pending,
                protected=t.category_source == "user" or bool(split),
                owner_person_id=owner,
            )
        )

    def reportable(*ids: uuid.UUID) -> bool:
        if not any(date_from <= posted[i] <= date_to for i in ids):
            return False
        # A leg outside the window and close to the load edge may have lost a competitor there.
        return all(
            date_from <= posted[i] <= date_to or (load_from + near <= posted[i] <= load_to - near)
            for i in ids
        )

    result = match_transfers(cands)
    paired: list[PairedTransfer] = []
    skipped: list[ProposedPair] = []
    for pair in result.pairs:
        if not reportable(pair.outflow_id, pair.inflow_id):
            continue
        failure: LedgerRuleError | LedgerRetryableError | None = None
        group: uuid.UUID | None = None
        try:
            async with session.begin_nested():
                group = await writer.apply_transfer_pair(
                    pair.outflow_id, pair.inflow_id, job_run_id=job_run_id
                )
        except (LedgerRuleError, LedgerRetryableError) as exc:
            failure = exc
        if failure is not None or group is None:
            skipped.append(pair)
        else:
            paired.append(PairedTransfer(pair.outflow_id, pair.inflow_id, group))
    suggested = tuple(x for x in result.suggested if reportable(x.outflow_id, x.inflow_id))
    ambiguous = tuple(a for a in result.ambiguous if reportable(a.transaction_id, *a.candidate_ids))
    return PairingReport(tuple(paired), suggested, ambiguous, tuple(skipped))
