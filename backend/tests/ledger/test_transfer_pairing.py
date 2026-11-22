"""Transfer pairing service against real Postgres as holdfast_app. Synthetic data only."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db_context import scoped_session
from app.domain.ledger_rules import LedgerRuleError
from app.models import Account, AuditLog, Category, Person, Transaction
from app.services.ledger_service import LedgerWriter, NewTransaction, SplitInput
from app.services.transfer_pairing import pair_transfers
from tests.models import conftest as mc

pytestmark = pytest.mark.services
D = Decimal
Factory = async_sessionmaker[AsyncSession]
FROM, TO = date(2026, 3, 1), date(2026, 3, 31)


async def _account(
    f: Factory,
    hid: uuid.UUID,
    kind: str,
    currency: str = "USD",
    owner: uuid.UUID | None = None,
    visibility: str = "shared",
) -> uuid.UUID:
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
                visibility=visibility,
                owner_person_id=owner,
                mask="0000",
            )
        )
    return aid


async def _txn(
    f: Factory, hid: uuid.UUID, account: uuid.UUID, amount: str, day: int, desc: str = "TRANSFER"
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


async def _people(f: Factory, hid: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    alex, sam = uuid.uuid4(), uuid.uuid4()
    async with scoped_session(f, hid, None, household_wide=True) as s:
        s.add(Person(id=alex, household_id=hid, display_name="Alex", filing_role="primary"))
        s.add(Person(id=sam, household_id=hid, display_name="Sam", filing_role="spouse"))
    return alex, sam


async def _flagged(f: Factory, hid: uuid.UUID) -> int:
    async with scoped_session(f, hid, None, household_wide=True) as s:
        return (
            await s.execute(select(func.count()).where(Transaction.is_transfer.is_(True)))
        ).scalar_one()


async def test_zero_hint_refund_is_suggested_and_not_written(
    factory: Factory, household: uuid.UUID
) -> None:
    chk = await _account(factory, household, "depository")
    card = await _account(factory, household, "credit")
    out = await _txn(factory, household, chk, "-50.00", 10, "EXAMPLE MART")
    inn = await _txn(factory, household, card, "50.00", 13, "EXAMPLE MART REFUND")
    report = await _run(factory, household)
    assert not report.paired
    assert [(x.outflow_id, x.inflow_id, x.reason) for x in report.suggested] == [
        (out, inn, "refund_candidate")
    ]
    assert await _flagged(factory, household) == 0


async def test_a_users_category_keeps_a_row_out_of_auto_pairing(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await _account(factory, household, "depository")
    b = await _account(factory, household, "depository")
    out = await _txn(factory, household, a, "-25.00", 4)
    inn = await _txn(factory, household, b, "25.00", 4)
    cid = uuid.uuid4()
    async with scoped_session(factory, household, None, household_wide=True) as s:
        s.add(
            Category(id=cid, household_id=household, slug="custom.x", name="Gifts", kind="expense")
        )
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await LedgerWriter(s, household, user_id=uuid.uuid4()).set_category(out, cid)
    report = await _run(factory, household)
    assert not report.paired
    assert [(x.outflow_id, x.inflow_id, x.reason) for x in report.suggested] == [
        (out, inn, "user_treated_as_spending")
    ]


async def test_accounts_of_two_owners_are_never_paired(
    factory: Factory, household: uuid.UUID
) -> None:
    alex, sam = await _people(factory, household)
    a = await _account(factory, household, "depository", owner=alex, visibility="private")
    b = await _account(factory, household, "depository", owner=sam, visibility="private")
    await _txn(factory, household, a, "-25.00", 4)
    await _txn(factory, household, b, "25.00", 4)
    report = await _run(factory, household)
    # ADR 0027: both legs are hinted, so the domain returns a cross-owner `link`, not a pair.
    # Until C4c (which writes the links table) the service ignores `links`: nothing is written.
    assert not report.paired and not report.suggested
    assert await _flagged(factory, household) == 0


async def test_person_scoped_run_sees_only_their_accounts(
    factory: Factory, household: uuid.UUID
) -> None:
    alex, sam = await _people(factory, household)
    mine = await _account(factory, household, "depository", owner=alex, visibility="private")
    joint = await _account(factory, household, "depository")
    out = await _txn(factory, household, mine, "-25.00", 4)
    inn = await _txn(factory, household, joint, "25.00", 4)
    async with scoped_session(factory, household, sam) as s:
        nothing = await pair_transfers(s, household, FROM, TO, user_id=uuid.uuid4())
    assert not nothing.paired and not nothing.suggested and not nothing.ambiguous
    async with scoped_session(factory, household, alex) as s:
        report = await pair_transfers(s, household, FROM, TO, user_id=uuid.uuid4())
    # Alex's private account versus a joint one is a cross-owner edge (a `link` in the domain,
    # ADR 0027). Until C4c the service writes no links, so it pairs nothing.
    assert not report.paired and out != inn


async def test_a_session_with_no_user_and_no_system_context_is_refused(
    factory: Factory, household: uuid.UUID
) -> None:
    async with scoped_session(factory, household, None) as s:
        with pytest.raises(LedgerRuleError):
            await pair_transfers(s, household, FROM, TO)
    a = await _account(factory, household, "depository")
    b = await _account(factory, household, "depository")
    out = await _txn(factory, household, a, "-5.00", 4)
    inn = await _txn(factory, household, b, "5.00", 4)
    async with scoped_session(factory, household, None) as s:
        with pytest.raises(LedgerRuleError):
            await LedgerWriter(s, household).apply_transfer_pair(out, inn)


async def test_audit_names_the_matcher(factory: Factory, household: uuid.UUID) -> None:
    a = await _account(factory, household, "depository")
    b = await _account(factory, household, "depository")
    await _txn(factory, household, a, "-5.00", 4)
    await _txn(factory, household, b, "5.00", 4)
    await _run(factory, household)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        detail = (
            await s.execute(
                select(AuditLog.detail).where(AuditLog.action == "ledger.transfer_auto_paired")
            )
        ).scalar_one()
    assert detail["actor"] == "transfer_matcher" and detail["job_run_id"] is None


async def test_manual_mark_and_pending_are_rechecked_at_write_time(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await _account(factory, household, "depository")
    b = await _account(factory, household, "depository")
    out = await _txn(factory, household, a, "-5.00", 4)
    inn = await _txn(factory, household, b, "5.00", 4)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await LedgerWriter(s, household, user_id=uuid.uuid4()).mark_transfer_single(inn)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        with pytest.raises(LedgerRuleError):
            await LedgerWriter(s, household).apply_transfer_pair(out, inn)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        row = (await s.execute(select(Transaction).where(Transaction.id == out))).scalar_one()
        row.is_pending = True
    fresh = await _txn(factory, household, b, "5.00", 4)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        with pytest.raises(LedgerRuleError):
            await LedgerWriter(s, household).apply_transfer_pair(out, fresh)


async def test_result_does_not_depend_on_the_window(factory: Factory, household: uuid.UUID) -> None:
    a = await _account(factory, household, "depository")
    b = await _account(factory, household, "depository")
    c = await _account(factory, household, "depository")
    out = await _txn(factory, household, a, "-30.00", 15)
    await _txn(factory, household, b, "30.00", 20)
    await _txn(factory, household, c, "30.00", 20)  # same-day competitor, 5 days away
    for lo, hi in ((date(2026, 3, 15), date(2026, 3, 15)), (FROM, TO)):
        async with scoped_session(factory, household, None, household_wide=True) as s:
            report = await pair_transfers(s, household, lo, hi)
        assert not report.paired
        assert any(x.transaction_id == out for x in report.ambiguous)


async def test_other_households_are_untouched(
    factory: Factory, household: uuid.UUID, app_engine: Engine, owner_engine: Engine
) -> None:
    with app_engine.begin() as conn:
        other = conn.execute(
            text("SELECT create_household(:n)"), {"n": "Other Example"}
        ).scalar_one()
    try:
        a = await _account(factory, other, "depository")
        b = await _account(factory, other, "depository")
        await _txn(factory, other, a, "-5.00", 4)
        await _txn(factory, other, b, "5.00", 4)
        mine = await _run(factory, household)
        assert not mine.paired and not mine.suggested and not mine.ambiguous
        assert await _flagged(factory, other) == 0
        async with scoped_session(factory, household, None, household_wide=True) as s:
            with pytest.raises(ValueError):
                await pair_transfers(s, other, FROM, TO)
    finally:
        mc.delete_household(owner_engine, other)


async def test_a_split_keeps_a_row_out_of_auto_pairing(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await _account(factory, household, "depository")
    b = await _account(factory, household, "depository")
    out = await _txn(factory, household, a, "-25.00", 4)
    inn = await _txn(factory, household, b, "25.00", 4)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await LedgerWriter(s, household, user_id=uuid.uuid4()).write_splits(
            out, [SplitInput(amount=D("-10.00")), SplitInput(amount=D("-15.00"))]
        )
    report = await _run(factory, household)
    assert not report.paired
    assert [(x.outflow_id, x.inflow_id, x.reason) for x in report.suggested] == [
        (out, inn, "user_treated_as_spending")
    ]


async def test_a_spoofed_session_info_does_not_open_the_system_gate(
    factory: Factory, household: uuid.UUID
) -> None:
    async with scoped_session(factory, household, None) as s:
        s.info["household_wide"] = True
        with pytest.raises(LedgerRuleError):
            await pair_transfers(s, household, FROM, TO)
