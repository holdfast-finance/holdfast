"""supersede_transaction and unsupersede_transaction against real Postgres (PHASE-2 3.3).

A test per field the verbs move, plus its reverse. Conflict and mismatch leave both rows live.
Synthetic data only; every amount is a ``Decimal`` built from a string.
"""

from __future__ import annotations

import random
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db_context import scoped_session
from app.domain.ledger_rules import LedgerRuleError, SupersedeConflict
from app.models import Transaction
from app.services.ledger_service import (
    AutoCategorySource,
    LedgerWriter,
    TransactionNotFoundError,
)
from tests.ledger.test_ledger_sync_verbs import (
    USER,
    Factory,
    category,
    conn_account,
    connector,
    document,
    import_row,
    put,
    row,
    split,
    splits_of,
    touch,
)

pytestmark = pytest.mark.services
D = Decimal


async def supersede(f: Factory, hid: uuid.UUID, old: uuid.UUID, new: uuid.UUID) -> dict[str, int]:
    async with scoped_session(f, hid, None, household_wide=True) as s:
        w = LedgerWriter(s, hid)
        await w.supersede_transaction(old, new)
        return dict(w.counts)


async def unsupersede(f: Factory, hid: uuid.UUID, old: uuid.UUID) -> None:
    async with scoped_session(f, hid, None, household_wide=True) as s:
        await LedgerWriter(s, hid).unsupersede_transaction(old)


async def pair(f: Factory, hid: uuid.UUID, a: uuid.UUID, b: uuid.UUID) -> uuid.UUID:
    async with scoped_session(f, hid, None, household_wide=True) as s:
        return await LedgerWriter(s, hid, user_id=USER).mark_transfer_pair(a, b)


async def single(f: Factory, hid: uuid.UUID, a: uuid.UUID) -> None:
    async with scoped_session(f, hid, None, household_wide=True) as s:
        await LedgerWriter(s, hid, user_id=USER).mark_transfer_single(a)


async def both_live(f: Factory, hid: uuid.UUID, *ids: uuid.UUID) -> bool:
    return all([(await row(f, hid, i)).superseded_by_id is None for i in ids])


async def setup(
    f: Factory, hid: uuid.UUID, amount: str = "-25.00"
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """An adopted account, an imported row on it and the connector row that replaces it."""
    a = await conn_account(f, hid)
    old = await import_row(f, hid, a, amount)
    new = await put(f, hid, connector(a, "ext-1", amount))
    return a, old, new


# ------------------------------------------------------------------------ the mark


async def test_supersede_marks_old_and_keeps_its_fingerprint_and_run(
    factory: Factory, household: uuid.UUID
) -> None:
    _, old, new = await setup(factory, household)
    before = await row(factory, household, old)
    counts = await supersede(factory, household, old, new)
    after = await row(factory, household, old)
    assert after.superseded_by_id == new and counts["rows_superseded"] == 1
    assert (after.import_fingerprint, after.import_run_id) == (
        before.import_fingerprint,
        before.import_run_id,
    )
    assert after.amount == D("-25.00") == (await row(factory, household, new)).amount


async def test_unsupersede_makes_the_old_row_live_again(
    factory: Factory, household: uuid.UUID
) -> None:
    _, old, new = await setup(factory, household)
    await supersede(factory, household, old, new)
    await unsupersede(factory, household, old)
    assert await both_live(factory, household, old, new)


# --------------------------------------------------------------------- category


@pytest.mark.parametrize(
    ("old_source", "moves"),
    [("user", True), ("rule", True), ("plaid", False), ("ml", False)],
)
async def test_category_moves_to_the_winner_only_when_old_outranks_it(
    factory: Factory, household: uuid.UUID, old_source: str, moves: bool
) -> None:
    a = await conn_account(factory, household)
    mine = await category(factory, household, "Mine")
    theirs = await category(factory, household, "Theirs")
    old = await import_row(
        factory, household, a, "-9.00", category_id=mine, category_source=old_source
    )
    new = await put(
        factory,
        household,
        connector(
            a, "ext-1", "-9.00", category_id=theirs, category_source=AutoCategorySource.PLAID
        ),
    )
    await supersede(factory, household, old, new)
    r = await row(factory, household, new)
    assert (r.category_id, r.category_source) == (
        (mine, old_source) if moves else (theirs, "plaid")
    )
    # The old row is history and keeps what it had.
    assert (await row(factory, household, old)).category_id == mine


async def test_a_user_choice_of_no_category_moves_too(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household)
    theirs = await category(factory, household, "Theirs")
    old = await import_row(factory, household, a, "-9.00", category_source="user")
    new = await put(
        factory,
        household,
        connector(
            a, "ext-1", "-9.00", category_id=theirs, category_source=AutoCategorySource.PLAID
        ),
    )
    await supersede(factory, household, old, new)
    r = await row(factory, household, new)
    assert (r.category_id, r.category_source) == (None, "user")


async def test_unsupersede_gives_old_a_higher_ranked_category_from_the_winner(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household)
    first = await category(factory, household, "First")
    chosen = await category(factory, household, "Chosen")
    old = await import_row(
        factory, household, a, "-9.00", category_id=first, category_source="rule"
    )
    new = await put(factory, household, connector(a, "ext-1", "-9.00"))
    await supersede(factory, household, old, new)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await LedgerWriter(s, household, user_id=USER).set_category(new, chosen)
    await unsupersede(factory, household, old)
    r = await row(factory, household, old)
    assert (r.category_id, r.category_source) == (chosen, "user")


async def test_unsupersede_keeps_old_category_when_the_winner_does_not_outrank_it(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household)
    mine = await category(factory, household, "Mine")
    old = await import_row(factory, household, a, "-9.00", category_id=mine, category_source="user")
    new = await put(factory, household, connector(a, "ext-1", "-9.00"))
    await supersede(factory, household, old, new)
    await unsupersede(factory, household, old)
    r = await row(factory, household, old)
    assert (r.category_id, r.category_source) == (mine, "user")


# ------------------------------------------------------------------------ splits


async def test_splits_move_to_the_winner_and_back(factory: Factory, household: uuid.UUID) -> None:
    _, old, new = await setup(factory, household, "-10.00")
    await split(factory, household, old, ["-6.00", "-4.00"])
    await supersede(factory, household, old, new)
    assert await splits_of(factory, household, new) == [D("-6.00"), D("-4.00")]
    assert await splits_of(factory, household, old) == []
    await unsupersede(factory, household, old)
    assert await splits_of(factory, household, old) == [D("-6.00"), D("-4.00")]
    assert await splits_of(factory, household, new) == []


async def test_when_both_have_splits_each_keeps_its_own_and_it_is_counted(
    factory: Factory, household: uuid.UUID
) -> None:
    _, old, new = await setup(factory, household, "-10.00")
    await split(factory, household, old, ["-6.00", "-4.00"])
    await split(factory, household, new, ["-9.00", "-1.00"])
    counts = await supersede(factory, household, old, new)
    assert counts["splits_kept_on_old"] == 1
    assert await splits_of(factory, household, old) == [D("-6.00"), D("-4.00")]
    assert await splits_of(factory, household, new) == [D("-9.00"), D("-1.00")]
    await unsupersede(factory, household, old)  # old has splits, so nothing moves back
    assert await splits_of(factory, household, old) == [D("-6.00"), D("-4.00")]
    assert await splits_of(factory, household, new) == [D("-9.00"), D("-1.00")]


async def test_a_split_on_the_winner_only_stays_on_the_winner(
    factory: Factory, household: uuid.UUID
) -> None:
    _, old, new = await setup(factory, household, "-10.00")
    await split(factory, household, new, ["-9.00", "-1.00"])
    await supersede(factory, household, old, new)
    assert await splits_of(factory, household, new) == [D("-9.00"), D("-1.00")]


# --------------------------------------------------------------------- transfers


async def test_a_paired_leg_hands_its_group_to_the_winner_and_back(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household)
    b = await conn_account(factory, household)
    old = await import_row(factory, household, a, "-30.00")
    partner = await put(factory, household, connector(b, "ext-p", "30.00"))
    group = await pair(factory, household, old, partner)
    new = await put(factory, household, connector(a, "ext-1", "-30.00"))
    await supersede(factory, household, old, new)
    r_old, r_new = await row(factory, household, old), await row(factory, household, new)
    assert (r_old.is_transfer, r_old.transfer_group_id) == (False, None)
    assert (r_new.is_transfer, r_new.transfer_group_id) == (True, group)
    assert (await row(factory, household, partner)).transfer_group_id == group
    await unsupersede(factory, household, old)
    r_old, r_new = await row(factory, household, old), await row(factory, household, new)
    assert (r_old.is_transfer, r_old.transfer_group_id) == (True, group)
    assert (r_new.is_transfer, r_new.transfer_group_id) == (False, None)
    assert (await row(factory, household, partner)).transfer_group_id == group


async def test_a_single_marked_leg_hands_its_mark_to_the_winner_and_back(
    factory: Factory, household: uuid.UUID
) -> None:
    _, old, new = await setup(factory, household)
    await single(factory, household, old)
    await supersede(factory, household, old, new)
    assert (await row(factory, household, old)).is_transfer is False
    assert (await row(factory, household, new)).is_transfer is True
    await unsupersede(factory, household, old)
    assert (await row(factory, household, old)).is_transfer is True
    assert (await row(factory, household, new)).is_transfer is False


async def test_a_winner_that_is_already_a_transfer_conflicts_and_both_stay_live(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household)
    b = await conn_account(factory, household)
    old = await import_row(factory, household, a, "-30.00")
    new = await put(factory, household, connector(a, "ext-1", "-30.00"))
    partner = await put(factory, household, connector(b, "ext-p", "30.00"))
    group = await pair(factory, household, new, partner)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        w = LedgerWriter(s, household)
        with pytest.raises(SupersedeConflict):
            await w.supersede_transaction(old, new)
        assert w.counts["supersede_conflict"] == 1
    assert await both_live(factory, household, old, new)
    assert (await row(factory, household, new)).transfer_group_id == group
    assert (await row(factory, household, old)).is_transfer is False


async def test_a_single_marked_winner_over_an_unmarked_old_row_conflicts(
    factory: Factory, household: uuid.UUID
) -> None:
    _, old, new = await setup(factory, household)
    await single(factory, household, new)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        with pytest.raises(SupersedeConflict):
            await LedgerWriter(s, household).supersede_transaction(old, new)
    assert await both_live(factory, household, old, new)


async def test_old_paired_and_winner_single_marked_conflicts(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household)
    b = await conn_account(factory, household)
    old = await import_row(factory, household, a, "-30.00")
    partner = await put(factory, household, connector(b, "ext-p", "30.00"))
    group = await pair(factory, household, old, partner)
    new = await put(factory, household, connector(a, "ext-1", "-30.00"))
    await single(factory, household, new)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        with pytest.raises(SupersedeConflict):
            await LedgerWriter(s, household).supersede_transaction(old, new)
    assert await both_live(factory, household, old, new)
    assert (await row(factory, household, old)).transfer_group_id == group


# --------------------------------------------------------- tax, receipt and notes


async def test_tax_flag_is_or_and_the_tag_prefers_the_winner(
    factory: Factory, household: uuid.UUID
) -> None:
    _, old, new = await setup(factory, household)
    await touch(factory, household, old, is_tax_relevant=True, tax_tag="old-tag")
    await supersede(factory, household, old, new)
    r = await row(factory, household, new)
    assert (r.is_tax_relevant, r.tax_tag) == (True, "old-tag")
    # Reverse: the winner gained a tag and its own tax flag; old has its values already.
    await touch(factory, household, new, tax_tag="new-tag")
    await unsupersede(factory, household, old)
    r_old = await row(factory, household, old)
    assert (r_old.is_tax_relevant, r_old.tax_tag) == (True, "old-tag")


async def test_the_winners_own_tag_beats_olds_and_old_takes_a_tag_it_lacks_on_reverse(
    factory: Factory, household: uuid.UUID
) -> None:
    _, old, new = await setup(factory, household)
    await touch(factory, household, old, tax_tag="old-tag")
    await touch(factory, household, new, tax_tag="new-tag")
    await supersede(factory, household, old, new)
    assert (await row(factory, household, new)).tax_tag == "new-tag"
    # A second pair: old has nothing, the winner gets a tag after the fact.
    a = await conn_account(factory, household)
    old2 = await import_row(factory, household, a, "-3.00")
    new2 = await put(factory, household, connector(a, "ext-2", "-3.00"))
    await supersede(factory, household, old2, new2)
    await touch(factory, household, new2, tax_tag="late", is_tax_relevant=True)
    await unsupersede(factory, household, old2)
    r = await row(factory, household, old2)
    assert (r.tax_tag, r.is_tax_relevant) == ("late", True)


async def test_receipt_prefers_the_winner_and_returns_to_old_when_old_has_none(
    factory: Factory, household: uuid.UUID
) -> None:
    _, old, new = await setup(factory, household)
    d_old = await document(factory, household)
    await touch(factory, household, old, receipt_document_id=d_old)
    await supersede(factory, household, old, new)
    assert (await row(factory, household, new)).receipt_document_id == d_old
    a = await conn_account(factory, household)
    old2 = await import_row(factory, household, a, "-3.00")
    new2 = await put(factory, household, connector(a, "ext-2", "-3.00"))
    d_new = await document(factory, household)
    await touch(factory, household, new2, receipt_document_id=d_new)
    await supersede(factory, household, old2, new2)
    assert (await row(factory, household, new2)).receipt_document_id == d_new
    await unsupersede(factory, household, old2)
    assert (await row(factory, household, old2)).receipt_document_id == d_new


async def test_notes_prefer_the_winner_unless_empty_and_return_to_old_when_empty(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household)
    old = await import_row(factory, household, a, "-4.00", notes="from old")
    new = await put(factory, household, connector(a, "ext-1", "-4.00"))
    await touch(factory, household, new, notes="")
    await supersede(factory, household, old, new)
    assert (await row(factory, household, new)).notes == "from old"
    old2 = await import_row(factory, household, a, "-5.00", notes="from old")
    new2 = await put(factory, household, connector(a, "ext-2", "-5.00"))
    await touch(factory, household, new2, notes="from new")
    await supersede(factory, household, old2, new2)
    assert (await row(factory, household, new2)).notes == "from new"
    old3 = await import_row(factory, household, a, "-6.00")
    new3 = await put(factory, household, connector(a, "ext-3", "-6.00"))
    await supersede(factory, household, old3, new3)
    await touch(factory, household, new3, notes="typed later")
    await unsupersede(factory, household, old3)
    assert (await row(factory, household, old3)).notes == "typed later"


# -------------------------------------------------------------- preconditions


async def test_unequal_amounts_raise_the_mismatch_code_and_write_nothing(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household)
    mine = await category(factory, household, "Mine")
    old = await import_row(
        factory, household, a, "-25.00", category_id=mine, category_source="user"
    )
    new = await put(factory, household, connector(a, "ext-1", "-25.01"))
    async with scoped_session(factory, household, None, household_wide=True) as s:
        w = LedgerWriter(s, household)
        with pytest.raises(LedgerRuleError, match="supersede_amount_mismatch"):
            await w.supersede_transaction(old, new)
        assert w.counts["supersede_amount_mismatch"] == 1
    assert await both_live(factory, household, old, new)
    assert (await row(factory, household, new)).category_id is None


async def test_a_sandbox_connection_never_supersedes(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household, env="sandbox")
    old = await import_row(factory, household, a, "-1.00")
    new = await put(factory, household, connector(a, "ext-1", "-1.00"))
    async with scoped_session(factory, household, None, household_wide=True) as s:
        with pytest.raises(LedgerRuleError, match="supersede_not_production"):
            await LedgerWriter(s, household).supersede_transaction(old, new)
    assert await both_live(factory, household, old, new)


async def test_an_account_without_a_connection_never_supersedes(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household, connected=False)
    old = await import_row(factory, household, a, "-1.00")
    other = await import_row(factory, household, a, "-1.00")
    async with scoped_session(factory, household, None, household_wide=True) as s:
        with pytest.raises(LedgerRuleError):  # a statement row is not a connector row
            await LedgerWriter(s, household).supersede_transaction(old, other)


async def test_rows_on_different_accounts_are_refused(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household)
    b = await conn_account(factory, household)
    old = await import_row(factory, household, a, "-1.00")
    new = await put(factory, household, connector(b, "ext-1", "-1.00"))
    async with scoped_session(factory, household, None, household_wide=True) as s:
        with pytest.raises(LedgerRuleError, match="supersede_account_mismatch"):
            await LedgerWriter(s, household).supersede_transaction(old, new)


async def test_a_pending_winner_and_a_reversed_pair_are_refused(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household)
    old = await import_row(factory, household, a, "-1.00")
    pending = await put(factory, household, connector(a, "ext-1", "-1.00", is_pending=True))
    async with scoped_session(factory, household, None, household_wide=True) as s:
        w = LedgerWriter(s, household)
        with pytest.raises(LedgerRuleError, match="supersede_not_eligible"):
            await w.supersede_transaction(old, pending)
        with pytest.raises(LedgerRuleError, match="supersede_source"):
            await w.supersede_transaction(pending, old)
        with pytest.raises(LedgerRuleError, match="supersede_same_row"):
            await w.supersede_transaction(old, old)


async def test_a_row_already_in_a_supersession_is_refused_on_both_sides(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household)
    old = await import_row(factory, household, a, "-2.00")
    other_old = await import_row(factory, household, a, "-2.00")
    new = await put(factory, household, connector(a, "ext-1", "-2.00"))
    other_new = await put(factory, household, connector(a, "ext-2", "-2.00"))
    await supersede(factory, household, old, new)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        w = LedgerWriter(s, household)
        with pytest.raises(LedgerRuleError, match="supersede_not_eligible"):
            await w.supersede_transaction(old, other_new)  # old is already replaced
        with pytest.raises(LedgerRuleError, match="supersede_not_eligible"):
            await w.supersede_transaction(other_old, new)  # new already replaces a row
    assert (await row(factory, household, other_old)).superseded_by_id is None


async def test_unsupersede_refuses_a_live_or_missing_row(
    factory: Factory, household: uuid.UUID
) -> None:
    _, old, _ = await setup(factory, household)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        w = LedgerWriter(s, household)
        with pytest.raises(LedgerRuleError, match="not superseded"):
            await w.unsupersede_transaction(old)
        with pytest.raises(TransactionNotFoundError):
            await w.unsupersede_transaction(uuid.uuid4())


async def test_a_superseded_row_stays_out_of_the_live_set_until_restored(
    factory: Factory, household: uuid.UUID
) -> None:
    a, old, new = await setup(factory, household, "-8.00")

    async def live_total() -> Decimal:
        async with scoped_session(factory, household, None, household_wide=True) as s:
            rows = await s.execute(
                select(Transaction.amount).where(
                    Transaction.account_id == a, Transaction.superseded_by_id.is_(None)
                )
            )
            return sum(rows.scalars(), D("0"))

    assert await live_total() == D("-16.00")  # both live: the matcher has not run yet
    await supersede(factory, household, old, new)
    assert await live_total() == D("-8.00")
    await unsupersede(factory, household, old)
    assert await live_total() == D("-16.00")


# ------------------------------------------------------------------- invariants


async def test_random_supersede_unsupersede_round_trips_keep_the_ledger_consistent(
    factory: Factory, household: uuid.UUID
) -> None:
    """Seeded scenarios: splits always sum to the amount, a group always has two legs, and
    supersede then unsupersede puts every old-row field back where it started."""
    rng = random.Random(20261122)  # noqa: S311 - seeded test data, not security
    a = await conn_account(factory, household)
    b = await conn_account(factory, household)
    for n in range(10):
        amount = D(f"-{rng.randint(1, 900)}.{rng.randint(0, 99):02d}")
        old = await import_row(factory, household, a, str(amount))
        new = await put(factory, household, connector(a, f"ext-{n}", str(amount)))
        if rng.random() < 0.5 and abs(amount) >= D("2"):
            first = (abs(amount) // 2).quantize(D("0.01"))
            await split(factory, household, old, [str(-first), str(amount + first)])
        group: uuid.UUID | None = None
        if rng.random() < 0.5:
            partner = await put(factory, household, connector(b, f"p-{n}", str(-amount)))
            group = await pair(factory, household, old, partner)
        before = await row(factory, household, old)
        old_splits = await splits_of(factory, household, old)
        await supersede(factory, household, old, new)
        winner = await row(factory, household, new)
        assert winner.amount == amount
        assert sum(await splits_of(factory, household, new), D("0")) in (D("0"), amount)
        assert winner.transfer_group_id == group
        await unsupersede(factory, household, old)
        after = await row(factory, household, old)
        assert (after.amount, after.is_transfer, after.transfer_group_id) == (
            before.amount,
            before.is_transfer,
            before.transfer_group_id,
        )
        assert after.superseded_by_id is None
        assert await splits_of(factory, household, old) == old_splits
        if group is not None:
            async with scoped_session(factory, household, None, household_wide=True) as s:
                legs = await s.execute(
                    select(Transaction.id).where(Transaction.transfer_group_id == group)
                )
                assert len(legs.all()) == 2
