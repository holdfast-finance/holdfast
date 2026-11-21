"""Transfer pairing service against real Postgres as holdfast_app. Synthetic data only."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db_context import scoped_session
from app.models import Account, AuditLog, Transaction
from app.services.ledger_service import LedgerWriter, NewTransaction
from app.services.transfer_pairing import pair_transfers

pytestmark = pytest.mark.services
D = Decimal
Factory = async_sessionmaker[AsyncSession]
FROM, TO = date(2026, 3, 1), date(2026, 3, 31)


async def _account(f: Factory, hid: uuid.UUID, kind: str, currency: str = "USD") -> uuid.UUID:
    aid = uuid.uuid4()
    async with scoped_session(f, hid, None, household_wide=True) as s:
        s.add(
            Account(
                id=aid,
                household_id=hid,
                source="import",
                external_account_id=f"t:{aid}",
                name="First Example",
                type=kind,
                currency=currency,
                visibility="shared",
                mask="0000",
            )
        )
    return aid


async def _txn(
    f: Factory, hid: uuid.UUID, account: uuid.UUID, amount: str, day: int, desc: str = "EXAMPLE"
) -> uuid.UUID:
    async with scoped_session(f, hid, None, household_wide=True) as s:
        tid = await LedgerWriter(s, hid).insert_transaction(
            NewTransaction(account, date(2026, 3, day), D(amount), desc, "manual")
        )
    assert tid is not None
    return tid


async def _run(f: Factory, hid: uuid.UUID):  # type: ignore[no-untyped-def]
    async with scoped_session(f, hid, None, household_wide=True) as s:
        return await pair_transfers(s, hid, FROM, TO)


async def _spend(f: Factory, hid: uuid.UUID) -> Decimal:
    async with scoped_session(f, hid, None, household_wide=True) as s:
        total = (
            await s.execute(
                select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                    Transaction.amount < 0, Transaction.is_transfer.is_(False)
                )
            )
        ).scalar_one()
    return D(total)


async def test_pairs_go_through_the_writer_and_a_rerun_is_a_noop(
    factory: Factory, household: uuid.UUID
) -> None:
    chk = await _account(factory, household, "depository")
    card = await _account(factory, household, "credit")
    pay_out = await _txn(factory, household, chk, "-300.00", 10, "ONLINE PMT CARD")
    pay_in = await _txn(factory, household, card, "300.00", 11, "PAYMENT THANK YOU")
    await _txn(factory, household, card, "-42.10", 5, "EXAMPLE MART")
    await _txn(factory, household, chk, "-12.90", 6, "EXAMPLE CAFE")

    first = await _run(factory, household)
    assert [(p.outflow_id, p.inflow_id) for p in first.paired] == [(pay_out, pay_in)]
    assert not first.ambiguous and not first.skipped

    async with scoped_session(factory, household, None, household_wide=True) as s:
        rows = (await s.execute(select(Transaction))).scalars().all()
        flagged = {r.id for r in rows if r.is_transfer}
        groups = {r.transfer_group_id for r in rows if r.is_transfer}
        actions = (await s.execute(select(AuditLog.action))).scalars().all()
    assert flagged == {pay_out, pay_in} and len(groups) == 1 and None not in groups
    assert "ledger.transfer_auto_paired" in actions

    second = await _run(factory, household)
    assert not second.paired and not second.ambiguous
    async with scoped_session(factory, household, None, household_wide=True) as s:
        again = (await s.execute(select(Transaction.transfer_group_id))).scalars().all()
    assert set(again) == groups | {None}

    # Card purchases and the cafe stay spending; the card payment does not.
    assert await _spend(factory, household) == D("-55.00")


async def test_ambiguous_legs_are_returned_and_never_written(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await _account(factory, household, "depository")
    b = await _account(factory, household, "depository")
    c = await _account(factory, household, "depository")
    out = await _txn(factory, household, a, "-20.00", 10)
    await _txn(factory, household, b, "20.00", 10)
    await _txn(factory, household, c, "20.00", 10)
    report = await _run(factory, household)
    assert not report.paired
    assert any(x.transaction_id == out and len(x.candidate_ids) == 2 for x in report.ambiguous)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        n = (
            await s.execute(select(func.count()).where(Transaction.is_transfer.is_(True)))
        ).scalar_one()
    assert n == 0


async def test_window_edge_partner_is_found_but_outside_pairs_are_left(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await _account(factory, household, "depository")
    b = await _account(factory, household, "depository")
    out = await _txn(factory, household, a, "-8.00", 31)
    inn = await _txn(factory, household, b, "8.00", 31)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        later = await LedgerWriter(s, household).insert_transaction(
            NewTransaction(a, date(2026, 5, 1), D("-9.00"), "X", "manual")
        )
        await LedgerWriter(s, household).insert_transaction(
            NewTransaction(b, date(2026, 5, 1), D("9.00"), "X", "manual")
        )
    assert later is not None
    report = await _run(factory, household)
    assert [(p.outflow_id, p.inflow_id) for p in report.paired] == [(out, inn)]


async def test_cross_currency_accounts_do_not_pair(factory: Factory, household: uuid.UUID) -> None:
    usd = await _account(factory, household, "depository", "USD")
    eur = await _account(factory, household, "depository", "EUR")
    await _txn(factory, household, usd, "-10.00", 3)
    await _txn(factory, household, eur, "10.00", 3)
    report = await _run(factory, household)
    assert not report.paired and not report.ambiguous
