"""LedgerWriter against real Postgres as holdfast_app: invariants, audit, RLS. Synthetic data."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db_context import scoped_session
from app.domain.ledger_rules import (
    InvalidAmountError,
    LedgerRuleError,
    SplitError,
    TransferPairError,
)
from app.models import Account, AuditLog, Category, Person, Rule, Transaction, TransactionSplit
from app.services.context import RequestContext
from app.services.ledger import LedgerService
from app.services.ledger_service import (
    AccountNotFoundError,
    AutoCategorySource,
    CategoryNotFoundError,
    LedgerRetryableError,
    LedgerWriter,
    NewTransaction,
    SplitInput,
    SupersededTransactionError,
    TransactionNotFoundError,
)
from app.services.sessions import Principal
from tests.models import conftest as mc

pytestmark = pytest.mark.services
D = Decimal
USER = uuid.uuid4()
Factory = async_sessionmaker[AsyncSession]


async def _account(
    f: Factory,
    hid: uuid.UUID,
    *,
    currency: str = "USD",
    owner: uuid.UUID | None = None,
    visibility: str | None = None,
) -> uuid.UUID:
    aid = uuid.uuid4()
    async with scoped_session(f, hid, None, household_wide=True) as s:
        s.add(
            Account(
                id=aid,
                household_id=hid,
                source="import",
                external_account_id=f"t:{aid}",
                statement_key=f"t:{aid}",
                name="Example Checking",
                type="depository",
                currency=currency,
                owner_person_id=owner,
                visibility=visibility or ("private" if owner else "shared"),
            )
        )
    return aid


async def _category(f: Factory, hid: uuid.UUID, name: str = "Groceries") -> uuid.UUID:
    cid = uuid.uuid4()
    async with scoped_session(f, hid, None, household_wide=True) as s:
        s.add(
            Category(id=cid, household_id=hid, slug=f"custom.{cid.hex}", name=name, kind="expense")
        )
    return cid


async def _txn(
    f: Factory, hid: uuid.UUID, account: uuid.UUID, amount: str, **kw: object
) -> uuid.UUID:
    async with scoped_session(f, hid, None, household_wide=True) as s:
        tid = await LedgerWriter(s, hid).insert_transaction(
            NewTransaction(
                account_id=account,
                posted_date=date(2026, 1, 15),
                amount=D(amount),
                description_raw="EXAMPLE MART",
                source="manual",
                **kw,  # type: ignore[arg-type]
            )
        )
    assert tid is not None
    return tid


async def _row(f: Factory, hid: uuid.UUID, tid: uuid.UUID) -> Transaction:
    async with scoped_session(f, hid, None, household_wide=True) as s:
        return (await s.execute(select(Transaction).where(Transaction.id == tid))).scalar_one()


async def _audit(f: Factory, hid: uuid.UUID) -> list[str]:
    async with scoped_session(f, hid, None) as s:
        rows = (await s.execute(select(AuditLog.action).order_by(AuditLog.at))).scalars().all()
    return [a for a in rows if a.startswith("ledger.")]


# ------------------------------------------------------------------------- insert


async def test_insert_round_trips_a_signed_decimal(factory: Factory, household: uuid.UUID) -> None:
    a = await _account(factory, household)
    t = await _txn(factory, household, a, "-42.10")
    row = await _row(factory, household, t)
    assert row.amount == D("-42.10") and row.household_id == household


@pytest.mark.parametrize("bad", [42.1, "42.10", D("0.001"), D("NaN")])
async def test_insert_rejects_non_money(
    factory: Factory, household: uuid.UUID, bad: object
) -> None:
    a = await _account(factory, household)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        with pytest.raises(InvalidAmountError):
            await LedgerWriter(s, household, user_id=USER).insert_transaction(
                NewTransaction(a, date(2026, 1, 1), bad, "X", "manual")  # type: ignore[arg-type]
            )


async def test_skip_duplicates_is_idempotent_on_the_external_id(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await _account(factory, household)
    new = NewTransaction(a, date(2026, 1, 1), D("-1.00"), "X", "plaid", external_id="ext-1")
    async with scoped_session(factory, household, None, household_wide=True) as s:
        svc = LedgerWriter(s, household, user_id=USER)
        assert await svc.insert_transaction(new, skip_duplicates=True) is not None
        assert await svc.insert_transaction(new, skip_duplicates=True) is None
        with pytest.raises(LedgerRuleError):
            await svc.insert_transaction(
                NewTransaction(a, date(2026, 1, 1), D("-1.00"), "X", "manual"),
                skip_duplicates=True,
            )


async def test_insert_into_another_households_account_fails(
    factory: Factory, household: uuid.UUID, app_engine: Engine, owner_engine: Engine
) -> None:
    with app_engine.begin() as c:
        other = c.execute(text("SELECT create_household('Example Other')")).scalar_one()
    try:
        foreign = await _account(factory, other)
        async with scoped_session(factory, household, None, household_wide=True) as s:
            # Denied by RLS in the database (not a pre-check: bulk ingest stays one statement/row).
            with pytest.raises(AccountNotFoundError):
                await LedgerWriter(s, household, user_id=USER).insert_transaction(
                    NewTransaction(foreign, date(2026, 1, 1), D("-1.00"), "X", "manual")
                )
    finally:
        mc.delete_household(owner_engine, other)


# ----------------------------------------------------------------------- category


async def test_user_category_edit_is_set_and_audited(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await _account(factory, household)
    t = await _txn(factory, household, a, "-5.00")
    c = await _category(factory, household)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await LedgerWriter(s, household, user_id=USER).set_category(t, c)
    row = await _row(factory, household, t)
    assert (row.category_id, row.category_source) == (c, "user")
    assert await _audit(factory, household) == ["ledger.category_set"]


async def test_rule_category_is_not_audited_and_none_clears(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await _account(factory, household)
    t = await _txn(factory, household, a, "-5.00")
    c = await _category(factory, household)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        svc = LedgerWriter(s, household, user_id=USER)
        await svc.apply_category(t, c, AutoCategorySource.RULE)
    assert (await _row(factory, household, t)).category_source == "rule"
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await LedgerWriter(s, household).apply_category(t, None, AutoCategorySource.RULE)
    row = await _row(factory, household, t)
    assert (row.category_id, row.category_source) == (None, None)
    assert await _audit(factory, household) == []


async def test_automated_source_never_overwrites_a_user_category(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await _account(factory, household)
    t = await _txn(factory, household, a, "-5.00")
    mine = await _category(factory, household, "Mine")
    other = await _category(factory, household, "Other")
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await LedgerWriter(s, household, user_id=USER).set_category(t, mine)
    for source in AutoCategorySource:
        async with scoped_session(factory, household, None, household_wide=True) as s:
            assert await LedgerWriter(s, household).apply_category(t, other, source) is False
        async with scoped_session(factory, household, None, household_wide=True) as s:
            assert await LedgerWriter(s, household).apply_category(t, None, source) is False
    row = await _row(factory, household, t)
    assert (row.category_id, row.category_source) == (mine, "user")
    # The user can still change their own choice, and an automated source applies elsewhere.
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await LedgerWriter(s, household, user_id=USER).set_category(t, other)
    assert (await _row(factory, household, t)).category_id == other
    t2 = await _txn(factory, household, a, "-6.00")
    async with scoped_session(factory, household, None, household_wide=True) as s:
        assert await LedgerWriter(s, household).apply_category(t2, other, AutoCategorySource.PLAID)
    assert (await _row(factory, household, t2)).category_source == "plaid"


async def test_source_precedence_is_user_rule_plaid_ml(
    factory: Factory, household: uuid.UUID
) -> None:
    rank = {AutoCategorySource.ML: 1, AutoCategorySource.PLAID: 2, AutoCategorySource.RULE: 3}
    a = await _account(factory, household)
    first = await _category(factory, household, "First")
    second = await _category(factory, household, "Second")
    for existing in AutoCategorySource:
        for new in AutoCategorySource:
            t = await _txn(factory, household, a, "-5.00")
            async with scoped_session(factory, household, None, household_wide=True) as s:
                assert await LedgerWriter(s, household).apply_category(t, first, existing)
            async with scoped_session(factory, household, None, household_wide=True) as s:
                applied = await LedgerWriter(s, household).apply_category(t, second, new)
            row = await _row(factory, household, t)
            assert applied == (rank[new] >= rank[existing]), (existing, new)
            want = (second, new.value) if applied else (first, existing.value)
            assert (row.category_id, row.category_source) == want, (existing, new)


async def test_user_clearing_a_category_sticks_until_released(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await _account(factory, household)
    t = await _txn(factory, household, a, "-5.00")
    c = await _category(factory, household)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await LedgerWriter(s, household, user_id=USER).set_category(t, c)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await LedgerWriter(s, household, user_id=USER).set_category(t, None)
    row = await _row(factory, household, t)
    assert (row.category_id, row.category_source) == (None, "user")
    for source in AutoCategorySource:
        async with scoped_session(factory, household, None, household_wide=True) as s:
            assert await LedgerWriter(s, household).apply_category(t, c, source) is False
    assert (await _row(factory, household, t)).category_id is None
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await LedgerWriter(s, household, user_id=USER).release_category(t)
    assert (await _row(factory, household, t)).category_source is None
    async with scoped_session(factory, household, None, household_wide=True) as s:
        assert await LedgerWriter(s, household).apply_category(t, c, AutoCategorySource.ML)
    assert (await _row(factory, household, t)).category_source == "ml"
    assert await _audit(factory, household) == [
        "ledger.category_set",
        "ledger.category_set",
        "ledger.category_released",
    ]


async def test_release_category_needs_a_user(factory: Factory, household: uuid.UUID) -> None:
    a = await _account(factory, household)
    t = await _txn(factory, household, a, "-5.00")
    async with scoped_session(factory, household, None, household_wide=True) as s:
        with pytest.raises(LedgerRuleError):
            await LedgerWriter(s, household).release_category(t)


async def test_unknown_category_and_source_are_rejected(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await _account(factory, household)
    t = await _txn(factory, household, a, "-5.00")
    async with scoped_session(factory, household, None, household_wide=True) as s:
        svc = LedgerWriter(s, household, user_id=USER)
        with pytest.raises(CategoryNotFoundError):
            await svc.set_category(t, uuid.uuid4())
        with pytest.raises(LedgerRuleError):
            await svc.apply_category(t, None, "user")  # type: ignore[arg-type]


# --------------------------------------------------------------------- transfers


async def test_pairing_links_two_legs_and_unmark_dissolves_both(
    factory: Factory, household: uuid.UUID
) -> None:
    a1, a2 = await _account(factory, household), await _account(factory, household)
    out, inn = (
        await _txn(factory, household, a1, "-100.00"),
        await _txn(factory, household, a2, "100.00"),
    )
    async with scoped_session(factory, household, None, household_wide=True) as s:
        group = await LedgerWriter(s, household, user_id=USER).mark_transfer_pair(out, inn)
    r1, r2 = await _row(factory, household, out), await _row(factory, household, inn)
    assert (r1.is_transfer, r2.is_transfer) == (True, True)
    assert r1.transfer_group_id == r2.transfer_group_id == group
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await LedgerWriter(s, household, user_id=USER).unmark_transfer(inn)
    r1, r2 = await _row(factory, household, out), await _row(factory, household, inn)
    assert (r1.is_transfer, r1.transfer_group_id, r2.is_transfer, r2.transfer_group_id) == (
        False,
        None,
        False,
        None,
    )
    assert await _audit(factory, household) == [
        "ledger.transfer_paired",
        "ledger.transfer_unmarked",
    ]


async def test_bad_pairs_write_nothing(factory: Factory, household: uuid.UUID) -> None:
    a1, a2 = await _account(factory, household), await _account(factory, household)
    eur = await _account(factory, household, currency="EUR")
    out = await _txn(factory, household, a1, "-10.00")
    same_acct_in = await _txn(factory, household, a1, "10.00")
    out2 = await _txn(factory, household, a2, "-10.00")
    eur_in = await _txn(factory, household, eur, "10.00")
    for other in (same_acct_in, out2, eur_in, out):
        async with scoped_session(factory, household, None, household_wide=True) as s:
            with pytest.raises(TransferPairError):
                await LedgerWriter(s, household, user_id=USER).mark_transfer_pair(out, other)
    assert not (await _row(factory, household, out)).is_transfer


async def test_a_leg_cannot_join_two_groups_or_flip_sign(
    factory: Factory, household: uuid.UUID
) -> None:
    a1, a2 = await _account(factory, household), await _account(factory, household)
    out, inn, third = (
        await _txn(factory, household, a1, "-10.00"),
        await _txn(factory, household, a2, "10.00"),
        await _txn(factory, household, a2, "10.00"),
    )
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await LedgerWriter(s, household, user_id=USER).mark_transfer_pair(out, inn)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        svc = LedgerWriter(s, household, user_id=USER)
        with pytest.raises(TransferPairError):
            await svc.mark_transfer_pair(out, third)
        with pytest.raises(TransferPairError):
            await svc.set_amount(out, D("10.00"))  # would make both legs inflow
        with pytest.raises(TransferPairError):
            await svc.set_amount(out, D("-9.50"))  # magnitude must stay equal to the partner
        await svc.set_amount(out, D("-10.00"))  # unchanged is fine


async def test_single_leg_transfer(factory: Factory, household: uuid.UUID) -> None:
    a = await _account(factory, household)
    t = await _txn(factory, household, a, "-10.00")
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await LedgerWriter(s, household, user_id=USER).mark_transfer_single(t)
    row = await _row(factory, household, t)
    assert (row.is_transfer, row.transfer_group_id) == (True, None)


# ------------------------------------------------------------------------ splits


async def test_splits_sum_exactly_and_are_replaced_not_appended(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await _account(factory, household)
    t = await _txn(factory, household, a, "-30.00")
    c = await _category(factory, household)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        svc = LedgerWriter(s, household, user_id=USER)
        await svc.write_splits(t, [SplitInput(D("-10.00"), c), SplitInput(D("-20.00"))])
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await LedgerWriter(s, household, user_id=USER).write_splits(t, [SplitInput(D("-30.00"), c)])
    async with scoped_session(factory, household, None, household_wide=True) as s:
        amounts = (await s.execute(select(TransactionSplit.amount))).scalars().all()
    assert amounts == [D("-30.00")]


async def test_bad_splits_leave_existing_splits_untouched(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await _account(factory, household)
    t = await _txn(factory, household, a, "-30.00")
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await LedgerWriter(s, household, user_id=USER).write_splits(t, [SplitInput(D("-30.00"))])
    async with scoped_session(factory, household, None, household_wide=True) as s:
        with pytest.raises(SplitError):
            await LedgerWriter(s, household, user_id=USER).write_splits(
                t, [SplitInput(D("-10.00")), SplitInput(D("-10.00"))]
            )
    async with scoped_session(factory, household, None, household_wide=True) as s:
        amounts = (await s.execute(select(TransactionSplit.amount))).scalars().all()
    assert amounts == [D("-30.00")]


async def test_amount_change_of_a_split_transaction_needs_new_splits(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await _account(factory, household)
    t = await _txn(factory, household, a, "-30.00")
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await LedgerWriter(s, household, user_id=USER).write_splits(t, [SplitInput(D("-30.00"))])
    async with scoped_session(factory, household, None, household_wide=True) as s:
        with pytest.raises(LedgerRuleError):
            await LedgerWriter(s, household, user_id=USER).set_amount(t, D("-40.00"))
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await LedgerWriter(s, household, user_id=USER).set_amount(
            t, D("-40.00"), splits=[SplitInput(D("-15.00")), SplitInput(D("-25.00"))]
        )
    assert (await _row(factory, household, t)).amount == D("-40.00")
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await LedgerWriter(s, household, user_id=USER).clear_splits(t)
        assert (await s.execute(select(TransactionSplit.id))).first() is None


# --------------------------------------------------------------------------- RLS


@pytest.fixture
def other_household(app_engine: Engine, owner_engine: Engine) -> Iterator[uuid.UUID]:
    with app_engine.begin() as c:
        hid = c.execute(text("SELECT create_household('Example Other')")).scalar_one()
    yield hid
    mc.delete_household(owner_engine, hid)


async def test_another_household_cannot_write_my_transaction(
    factory: Factory, household: uuid.UUID, other_household: uuid.UUID
) -> None:
    a = await _account(factory, household)
    t = await _txn(factory, household, a, "-5.00")
    c = await _category(factory, other_household)
    async with scoped_session(factory, other_household, None, household_wide=True) as s:
        svc = LedgerWriter(s, other_household, user_id=USER)
        with pytest.raises(TransactionNotFoundError):
            await svc.set_category(t, c)
        with pytest.raises(TransactionNotFoundError):
            await svc.set_amount(t, D("-6.00"))
        with pytest.raises(TransactionNotFoundError):
            await svc.write_splits(t, [SplitInput(D("-5.00"))])
        with pytest.raises(TransactionNotFoundError):
            await svc.mark_transfer_single(t)
        with pytest.raises(TransactionNotFoundError):
            await svc.unmark_transfer(t)
    row = await _row(factory, household, t)
    assert (row.amount, row.category_id, row.is_transfer) == (D("-5.00"), None, False)


async def test_cross_household_pairing_is_impossible(
    factory: Factory, household: uuid.UUID, other_household: uuid.UUID
) -> None:
    mine = await _txn(factory, household, await _account(factory, household), "-5.00")
    theirs = await _txn(factory, other_household, await _account(factory, other_household), "5.00")
    async with scoped_session(factory, household, None, household_wide=True) as s:
        with pytest.raises(TransactionNotFoundError):
            await LedgerWriter(s, household, user_id=USER).mark_transfer_pair(mine, theirs)


async def test_a_person_cannot_write_another_persons_private_account(
    factory: Factory, household: uuid.UUID
) -> None:
    alex, sam = uuid.uuid4(), uuid.uuid4()
    async with scoped_session(factory, household, None, household_wide=True) as s:
        s.add(Person(id=alex, household_id=household, display_name="Alex", filing_role="primary"))
        s.add(Person(id=sam, household_id=household, display_name="Sam", filing_role="spouse"))
    acct = await _account(factory, household, owner=alex)
    t = await _txn(factory, household, acct, "-5.00")
    c = await _category(factory, household)
    async with scoped_session(factory, household, sam) as s:
        with pytest.raises(TransactionNotFoundError):
            await LedgerWriter(s, household, user_id=USER).set_category(t, c)
    async with scoped_session(factory, household, alex) as s:
        await LedgerWriter(s, household, user_id=USER).set_category(t, c)
    assert (await _row(factory, household, t)).category_id == c


# ---------------------------------------------------------------- review fixes


async def test_pair_with_unequal_magnitudes_is_rejected(
    factory: Factory, household: uuid.UUID
) -> None:
    a1, a2 = await _account(factory, household), await _account(factory, household)
    out, inn = (
        await _txn(factory, household, a1, "-500.00"),
        await _txn(factory, household, a2, "2000.00"),
    )
    async with scoped_session(factory, household, None, household_wide=True) as s:
        with pytest.raises(TransferPairError):
            await LedgerWriter(s, household, user_id=USER).mark_transfer_pair(out, inn)


async def test_failed_set_amount_validation_leaves_the_stored_amount_unchanged(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await _account(factory, household)
    t = await _txn(factory, household, a, "-30.00")
    # The caller catches the error and the session still commits: nothing may have been mutated.
    async with scoped_session(factory, household, None, household_wide=True) as s:
        svc = LedgerWriter(s, household, user_id=USER)
        with pytest.raises(SplitError):
            await svc.set_amount(t, D("-40.00"), splits=[SplitInput(D("-10.00"))])
        with pytest.raises(CategoryNotFoundError):
            await svc.set_amount(t, D("-40.00"), splits=[SplitInput(D("-40.00"), uuid.uuid4())])
        with pytest.raises(InvalidAmountError):
            await svc.set_amount(t, D("-40.001"))
    assert (await _row(factory, household, t)).amount == D("-30.00")
    assert await _audit(factory, household) == []


async def test_database_errors_carry_no_row_values(factory: Factory, household: uuid.UUID) -> None:
    sentinel_desc, sentinel_amount = "SENTINEL-DESC-7731", D("-987654.32")

    def chain_text(exc: BaseException) -> str:
        out: list[str] = []
        seen: set[int] = set()
        stack: list[BaseException | None] = [exc]
        while stack:
            e = stack.pop()
            if e is None or id(e) in seen:
                continue
            seen.add(id(e))
            out.append(repr(e) + str(e))
            stack += [e.__cause__, e.__context__]
        return "\n".join(out)

    a = await _account(factory, household)
    cases: list[tuple[uuid.UUID, str, str | None, type[Exception]]] = [
        (a, "import", None, LedgerRuleError),  # CHECK violation: import rows need a fingerprint
        (uuid.uuid4(), "manual", None, AccountNotFoundError),  # account the session cannot see
    ]
    for account, source, fingerprint, expected in cases:
        async with scoped_session(factory, household, None, household_wide=True) as s:
            with pytest.raises(expected) as info:
                await LedgerWriter(s, household).insert_transaction(
                    NewTransaction(
                        account,
                        date(2026, 1, 1),
                        sentinel_amount,
                        sentinel_desc,
                        source,
                        import_fingerprint=fingerprint,
                    )
                )
            text_ = chain_text(info.value)
            assert sentinel_desc not in text_ and "987654" not in text_
            assert info.value.__cause__ is None and info.value.__context__ is None
            await s.rollback()


def test_the_production_engine_hides_statement_parameters() -> None:
    from app.db import create_engine
    from tests.conftest import make_settings

    engine = create_engine(make_settings())
    assert engine.sync_engine.hide_parameters is True


async def test_concurrent_edits_of_both_legs_never_deadlock(
    factory: Factory, household: uuid.UUID
) -> None:
    import asyncio

    a1, a2 = await _account(factory, household), await _account(factory, household)
    out, inn = (
        await _txn(factory, household, a1, "-10.00"),
        await _txn(factory, household, a2, "10.00"),
    )
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await LedgerWriter(s, household, user_id=USER).mark_transfer_pair(out, inn)

    async def hammer(leg: uuid.UUID, amount: str) -> list[BaseException]:
        errors: list[BaseException] = []
        for _ in range(15):
            try:
                async with scoped_session(factory, household, None, household_wide=True) as s:
                    await LedgerWriter(s, household, user_id=USER).set_amount(leg, D(amount))
            except LedgerRetryableError:
                pass  # typed and retryable is acceptable
            except BaseException as exc:
                errors.append(exc)
        return errors

    results = await asyncio.gather(hammer(out, "-10.00"), hammer(inn, "10.00"))
    assert results == [[], []]
    assert (await _row(factory, household, out)).transfer_group_id is not None


async def test_unmark_needs_both_legs_writable(factory: Factory, household: uuid.UUID) -> None:
    alex, sam = await _two_people(factory, household)
    a_alex = await _account(factory, household, owner=alex)
    a_sam = await _account(factory, household, owner=sam)
    out, inn = (
        await _txn(factory, household, a_alex, "-10.00"),
        await _txn(factory, household, a_sam, "10.00"),
    )
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await LedgerWriter(s, household, user_id=USER).mark_transfer_pair(out, inn)
    async with scoped_session(factory, household, sam) as s:
        svc = LedgerWriter(s, household, user_id=USER)
        with pytest.raises(TransferPairError):
            await svc.unmark_transfer(inn)  # Sam can write only his own leg
        with pytest.raises(TransferPairError):
            await svc.set_amount(inn, D("10.00"))
        with pytest.raises(TransactionNotFoundError):
            await svc.unmark_transfer(out)  # Alex's private leg is invisible
    for t in (out, inn):
        assert (await _row(factory, household, t)).is_transfer is True
    async with scoped_session(factory, household, alex) as s:
        pass  # alex alone cannot dissolve it either
        with pytest.raises(TransferPairError):
            await LedgerWriter(s, household, user_id=USER).unmark_transfer(out)


async def test_person_scoped_split_and_transfer_writes_follow_account_visibility(
    factory: Factory, household: uuid.UUID
) -> None:
    alex, sam = await _two_people(factory, household)
    private_alex = await _account(factory, household, owner=alex)
    joint = await _account(factory, household)  # no owner: writable by both
    shared_alex = await _account(factory, household, owner=alex, visibility="shared")
    t_private = await _txn(factory, household, private_alex, "-20.00")
    t_joint = await _txn(factory, household, joint, "-20.00")
    t_shared = await _txn(factory, household, shared_alex, "-20.00")
    parts = [SplitInput(D("-5.00")), SplitInput(D("-15.00"))]
    async with scoped_session(factory, household, sam) as s:
        svc = LedgerWriter(s, household, user_id=USER)
        await svc.write_splits(t_joint, parts)  # joint: ok
        await svc.mark_transfer_single(t_joint)
        for hidden_or_readonly in (t_private, t_shared):
            with pytest.raises(TransactionNotFoundError):
                await svc.write_splits(hidden_or_readonly, parts)
            with pytest.raises(TransactionNotFoundError):
                await svc.mark_transfer_single(hidden_or_readonly)
    async with scoped_session(factory, household, alex) as s:
        await LedgerWriter(s, household, user_id=USER).write_splits(t_shared, parts)


async def test_superseded_rows_cannot_be_edited_or_paired(
    factory: Factory, household: uuid.UUID
) -> None:
    from sqlalchemy import update

    a, b = await _account(factory, household), await _account(factory, household)
    old, winner = (
        await _txn(factory, household, a, "-10.00"),
        await _txn(factory, household, a, "-10.00"),
    )
    other = await _txn(factory, household, b, "10.00")
    c = await _category(factory, household)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        # Test setup only (tests are outside the app/ lint): mark `old` as replaced by `winner`.
        await s.execute(
            update(Transaction).where(Transaction.id == old).values(superseded_by_id=winner)
        )
    async with scoped_session(factory, household, None, household_wide=True) as s:
        svc = LedgerWriter(s, household, user_id=USER)
        for call in (
            svc.set_category(old, c),
            svc.apply_category(old, c, AutoCategorySource.RULE),
            svc.set_amount(old, D("-11.00")),
            svc.write_splits(old, [SplitInput(D("-10.00"))]),
            svc.clear_splits(old),
            svc.mark_transfer_single(old),
            svc.mark_transfer_pair(old, other),
        ):
            with pytest.raises(SupersededTransactionError):
                await call
    row = await _row(factory, household, old)
    assert (row.amount, row.category_id, row.is_transfer) == (D("-10.00"), None, False)


async def test_skip_duplicates_skips_on_either_idempotency_key(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await _account(factory, household)
    f1, f2 = "a" * 64, "b" * 64

    def new(ext: str, fp: str) -> NewTransaction:
        return NewTransaction(
            a, date(2026, 1, 1), D("-1.00"), "X", "plaid", external_id=ext, import_fingerprint=fp
        )

    async with scoped_session(factory, household, None, household_wide=True) as s:
        svc = LedgerWriter(s, household)
        assert await svc.insert_transaction(new("e1", f1), skip_duplicates=True) is not None
        assert await svc.insert_transaction(new("e1", f2), skip_duplicates=True) is None
        assert await svc.insert_transaction(new("e2", f1), skip_duplicates=True) is None
        assert await svc.insert_transaction(new("e2", f2), skip_duplicates=True) is not None


async def test_user_edits_need_a_user_and_the_session_must_match_the_household(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await _account(factory, household)
    t = await _txn(factory, household, a, "-5.00")
    c = await _category(factory, household)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        anonymous = LedgerWriter(s, household)
        for call in (
            anonymous.set_category(t, c),
            anonymous.set_amount(t, D("-6.00")),
            anonymous.mark_transfer_single(t),
            anonymous.write_splits(t, [SplitInput(D("-5.00"))]),
        ):
            with pytest.raises(LedgerRuleError):
                await call
        with pytest.raises(ValueError, match="scoped"):
            LedgerWriter(s, uuid.uuid4(), user_id=USER)
    async with factory() as unscoped:
        with pytest.raises(ValueError, match="scoped"):
            LedgerWriter(unscoped, household, user_id=USER)


async def test_category_source_cannot_be_user_on_the_automated_paths(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await _account(factory, household)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        with pytest.raises(LedgerRuleError):
            await LedgerWriter(s, household).insert_transaction(
                NewTransaction(
                    a,
                    date(2026, 1, 1),
                    D("-1.00"),
                    "X",
                    "manual",
                    category_source="user",  # type: ignore[arg-type]
                )
            )


async def _two_people(f: Factory, hid: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    alex, sam = uuid.uuid4(), uuid.uuid4()
    async with scoped_session(f, hid, None, household_wide=True) as s:
        s.add(Person(id=alex, household_id=hid, display_name="Alex", filing_role="primary"))
        s.add(Person(id=sam, household_id=hid, display_name="Sam", filing_role="spouse"))
    return alex, sam


async def _rule(f: Factory, hid: uuid.UUID, person: uuid.UUID, category_id: uuid.UUID) -> uuid.UUID:
    rid = uuid.uuid4()
    async with scoped_session(f, hid, person) as s:
        s.add(
            Rule(
                id=rid,
                household_id=hid,
                author_person_id=person,
                name="Example rule",
                category_id=category_id,
                conditions={"v": 1, "description": {"mode": "contains", "value": "EXAMPLE"}},
            )
        )
    return rid


async def test_bulk_rule_precedence_exact_counts_and_audit(
    factory: Factory, household: uuid.UUID
) -> None:
    hid = household
    alex, sam = await _two_people(factory, hid)
    category_id = await _category(factory, hid)
    rid = await _rule(factory, hid, alex, category_id)
    account = await _account(factory, hid, owner=alex)
    other = await _account(factory, hid, owner=sam, visibility="shared")
    plain = await _txn(factory, hid, account, "-1.00")
    automated = await _txn(
        factory,
        hid,
        account,
        "-1.00",
        category_id=category_id,
        category_source=AutoCategorySource.PLAID,
    )
    user = await _txn(factory, hid, account, "-1.00")
    split = await _txn(factory, hid, account, "-1.00")
    shared = await _txn(factory, hid, other, "-1.00")
    async with scoped_session(factory, hid, alex) as s:
        writer = LedgerWriter(s, hid, user_id=USER)
        await writer.set_category(user, None)
        await writer.write_splits(split, [SplitInput(D("-0.40")), SplitInput(D("-0.60"))])
        for stale in (user, split, shared, uuid.uuid4(), plain):
            with pytest.raises(LedgerRuleError, match=r"^preview_stale$"):
                await writer.apply_rule_bulk(rid, category_id, [plain, stale])
        assert not await writer.apply_category(split, category_id, AutoCategorySource.RULE, rid)
        untouched = await s.get(Transaction, plain)
        assert untouched is not None and untouched.category_rule_id is None
        assert await writer.apply_rule_bulk(rid, category_id, [plain, automated]) == 2

    for tid in (plain, automated):
        applied = await _row(factory, hid, tid)
        assert (applied.category_id, applied.category_source, applied.category_rule_id) == (
            category_id,
            "rule",
            rid,
        )
    assert (await _row(factory, hid, user)).category_source == "user"
    for tid in (split, shared):
        assert (await _row(factory, hid, tid)).category_rule_id is None

    async with scoped_session(factory, hid, alex) as s:
        writer = LedgerWriter(s, hid, user_id=USER)
        with pytest.raises(LedgerRuleError, match=r"^preview_stale$"):
            await writer.clear_rule_categories(rid, [plain, user])
        assert await writer.clear_rule_categories(rid, [plain, automated]) == 2
    for tid in (plain, automated):
        cleared = await _row(factory, hid, tid)
        assert (cleared.category_id, cleared.category_source, cleared.category_rule_id) == (
            None,
            None,
            None,
        )

    async with scoped_session(factory, hid, alex) as s:
        audits = list(
            await s.scalars(
                select(AuditLog).where(AuditLog.action.in_(["rule.applied", "rule.cleared"]))
            )
        )
    assert sorted(a.action for a in audits) == ["rule.applied", "rule.cleared"]
    assert all(a.user_id == USER and a.entity == "rule" for a in audits)
    assert all(a.detail == {"outcome": "success", "rule_id": str(rid)} for a in audits)


@pytest.mark.parametrize("rule_on_old", [True, False])
async def test_supersede_and_unsupersede_keep_live_rule_provenance(
    factory: Factory, household: uuid.UUID, rule_on_old: bool
) -> None:
    from tests.ledger.test_supersede_verbs import setup

    hid = household
    alex, _ = await _two_people(factory, hid)
    category_id = await _category(factory, hid)
    rid = await _rule(factory, hid, alex, category_id)
    _, old, winner = await setup(factory, hid)
    async with scoped_session(factory, hid, None, household_wide=True) as s:
        writer = LedgerWriter(s, hid, user_id=USER)
        if rule_on_old:
            assert await writer.apply_category(old, category_id, AutoCategorySource.RULE, rid)
        await writer.supersede_transaction(old, winner)
        if not rule_on_old:
            assert await writer.apply_category(winner, category_id, AutoCategorySource.RULE, rid)
        with pytest.raises(LedgerRuleError, match=r"^preview_stale$"):
            await writer.apply_rule_bulk(rid, category_id, [old])
        with pytest.raises(LedgerRuleError, match=r"^preview_stale$"):
            await writer.clear_rule_categories(rid, [old])

    applied = await _row(factory, hid, winner)
    assert applied.superseded_by_id is None
    assert (applied.category_id, applied.category_source, applied.category_rule_id) == (
        category_id,
        "rule",
        rid,
    )
    async with scoped_session(factory, hid, None, household_wide=True) as s:
        await LedgerWriter(s, hid).unsupersede_transaction(old)
    restored = await _row(factory, hid, old)
    assert restored.superseded_by_id is None
    assert (restored.category_id, restored.category_source, restored.category_rule_id) == (
        category_id,
        "rule",
        rid,
    )


async def test_post_pending_carries_rule_provenance_and_person_edit_clears_it(
    factory: Factory, household: uuid.UUID
) -> None:
    from tests.ledger.test_ledger_sync_verbs import conn_account, connector, put

    hid = household
    alex, _ = await _two_people(factory, hid)
    category_id = await _category(factory, hid)
    rid = await _rule(factory, hid, alex, category_id)
    account = await conn_account(factory, hid)
    await put(
        factory,
        hid,
        connector(
            account,
            "example-pending",
            "-25.00",
            is_pending=True,
            category_id=category_id,
            category_source=AutoCategorySource.RULE,
            category_rule_id=rid,
        ),
    )
    async with scoped_session(factory, hid, None, household_wide=True) as s:
        posted = await LedgerWriter(s, hid).post_pending(
            "example-pending", connector(account, "example-posted", "-25.00")
        )
    applied = await _row(factory, hid, posted)
    assert (applied.category_id, applied.category_source, applied.category_rule_id) == (
        category_id,
        "rule",
        rid,
    )
    async with scoped_session(factory, hid, None, household_wide=True) as s:
        writer = LedgerWriter(s, hid, user_id=USER)
        await writer.release_category(posted)
    assert (await _row(factory, hid, posted)).category_rule_id is None
    async with scoped_session(factory, hid, None, household_wide=True) as s:
        writer = LedgerWriter(s, hid, user_id=USER)
        await writer.apply_category(posted, category_id, AutoCategorySource.RULE, rid)
        await writer.set_category(posted, category_id)
    assert (await _row(factory, hid, posted)).category_rule_id is None


async def test_can_edit_matches_account_writable_for_person_sessions(
    factory: Factory, household: uuid.UUID
) -> None:
    hid = household
    alex, sam = await _two_people(factory, hid)
    context = cast(RequestContext, SimpleNamespace(factory=factory))
    for owner, visibility in (
        (alex, "private"),
        (alex, "shared"),
        (alex, "summary_only"),
        (None, "private"),
        (sam, "shared"),
    ):
        account = await _account(factory, hid, owner=owner, visibility=visibility)
        tid = await _txn(factory, hid, account, "-1.00")
        principal = Principal(uuid.uuid4(), USER, hid, alex, "owner", "alex@example.test", None)
        async with scoped_session(factory, hid, alex) as s:
            writable = await s.scalar(
                text("SELECT public.app_account_writable(:account)"), {"account": account}
            )
        row = await LedgerService(context, principal).get(tid)
        assert row.can_edit == writable == (owner in (None, alex))
        if owner is None:
            no_person = Principal(
                principal.session_id, USER, hid, None, "owner", principal.email, None
            )
            assert (await LedgerService(context, no_person).get(tid)).can_edit is False
