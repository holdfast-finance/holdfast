"""upsert_external, remove_external and post_pending against real Postgres (PHASE-2 3.3).

Synthetic data only. Amounts are ``Decimal`` built from strings: outflow negative.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db_context import scoped_session
from app.domain.ledger_rules import LedgerRuleError
from app.models import (
    Account,
    BankConnection,
    Category,
    Document,
    ImportRun,
    Transaction,
    TransactionSplit,
)
from app.services.ledger_service import (
    AutoCategorySource,
    LedgerWriter,
    NewTransaction,
    SplitInput,
    TransactionNotFoundError,
    UpsertAction,
)

pytestmark = pytest.mark.services
D = Decimal
USER = uuid.uuid4()
Factory = async_sessionmaker[AsyncSession]
DAY = date(2026, 3, 10)


async def conn_account(
    f: Factory, hid: uuid.UUID, *, env: str = "production", connected: bool = True
) -> uuid.UUID:
    """A synthetic account. Connected + production is an adopted import account (ADR 0033);
    connected + sandbox is a plain connector account (the database refuses to adopt into
    sandbox); ``connected=False`` is an import account."""
    aid = uuid.uuid4()
    cid = uuid.uuid4()
    adopt = connected and env == "production"
    plain_connector = connected and not adopt
    async with scoped_session(f, hid, None, household_wide=True) as s:
        if connected:
            s.add(
                BankConnection(
                    id=cid,
                    household_id=hid,
                    provider="plaid",
                    external_id=f"item-{cid.hex}",
                    access_token_enc=b"hf2.synthetic",
                    env=env,
                )
            )
            await s.flush()
        s.add(
            Account(
                id=aid,
                household_id=hid,
                source="plaid" if plain_connector else "import",
                external_account_id=f"t:{aid}",
                statement_key=None if plain_connector else f"t:{aid}",
                bank_connection_id=cid if plain_connector else None,
                name="Example Checking",
                type="depository",
                currency="USD",
                visibility="shared",
            )
        )
    if adopt:
        async with scoped_session(f, hid, None, household_wide=True) as s:
            await s.execute(
                update(Account)
                .where(Account.id == aid)
                .values(source="plaid", bank_connection_id=cid, adopted_at=datetime.now(UTC))
            )
    return aid


async def category(f: Factory, hid: uuid.UUID, name: str = "Groceries") -> uuid.UUID:
    cid = uuid.uuid4()
    async with scoped_session(f, hid, None, household_wide=True) as s:
        s.add(
            Category(id=cid, household_id=hid, slug=f"custom.{cid.hex}", name=name, kind="expense")
        )
    return cid


async def document(f: Factory, hid: uuid.UUID) -> uuid.UUID:
    did = uuid.uuid4()
    async with scoped_session(f, hid, None, household_wide=True) as s:
        s.add(
            Document(
                id=did,
                household_id=hid,
                kind="receipt",
                filename="example.pdf",
                sha256=did.hex * 2,
                storage_path=f"receipts/{did.hex}.pdf",
                mime="application/pdf",
                size_bytes=1,
            )
        )
    return did


def connector(account: uuid.UUID, external_id: str, amount: str, **kw: object) -> NewTransaction:
    fields: dict[str, object] = {
        "posted_date": DAY,
        "description_raw": "EXAMPLE MART",
        "source": "plaid",
    }
    fields.update(kw)
    return NewTransaction(
        account_id=account,
        amount=D(amount),
        external_id=external_id,
        **fields,  # type: ignore[arg-type]
    )


async def put(f: Factory, hid: uuid.UUID, new: NewTransaction) -> uuid.UUID:
    async with scoped_session(f, hid, None, household_wide=True) as s:
        outcome = await LedgerWriter(s, hid).upsert_external(new)
    return outcome.transaction_id


async def import_row(
    f: Factory, hid: uuid.UUID, account: uuid.UUID, amount: str, **kw: object
) -> uuid.UUID:
    """An imported (statement) row, inserted directly: test setup, not application code."""
    tid = uuid.uuid4()
    async with scoped_session(f, hid, None, household_wide=True) as s:
        doc = uuid.uuid4()
        s.add(
            Document(
                id=doc,
                household_id=hid,
                kind="statement",
                filename="example.pdf",
                sha256=doc.hex * 2,
                storage_path=f"statements/{doc.hex}.pdf",
                mime="application/pdf",
                size_bytes=1,
            )
        )
        await s.flush()
        run = ImportRun(
            household_id=hid,
            document_id=doc,
            account_id=account,
            status="running",
            started_at=datetime(2026, 3, 1, tzinfo=UTC),
        )
        s.add(run)
        await s.flush()
        s.add(
            Transaction(
                id=tid,
                household_id=hid,
                account_id=account,
                posted_date=DAY,
                amount=D(amount),
                description_raw="EXAMPLE MART",
                source="import",
                import_fingerprint=tid.hex * 2,
                import_run_id=run.id,
                **kw,
            )
        )
    return tid


async def row(f: Factory, hid: uuid.UUID, tid: uuid.UUID) -> Transaction:
    async with scoped_session(f, hid, None, household_wide=True) as s:
        return (await s.execute(select(Transaction).where(Transaction.id == tid))).scalar_one()


async def maybe_row(f: Factory, hid: uuid.UUID, tid: uuid.UUID) -> Transaction | None:
    async with scoped_session(f, hid, None, household_wide=True) as s:
        return (
            await s.execute(select(Transaction).where(Transaction.id == tid))
        ).scalar_one_or_none()


async def touch(f: Factory, hid: uuid.UUID, tid: uuid.UUID, **fields: object) -> None:
    """Set non-ledger-money fields (notes, tax, receipt) directly: test setup."""
    async with scoped_session(f, hid, None, household_wide=True) as s:
        await s.execute(update(Transaction).where(Transaction.id == tid).values(**fields))


async def splits_of(f: Factory, hid: uuid.UUID, tid: uuid.UUID) -> list[Decimal]:
    async with scoped_session(f, hid, None, household_wide=True) as s:
        rows = await s.execute(
            select(TransactionSplit.amount)
            .where(TransactionSplit.transaction_id == tid)
            .order_by(TransactionSplit.amount)
        )
        return list(rows.scalars())


async def split(
    f: Factory, hid: uuid.UUID, tid: uuid.UUID, parts: Sequence[str], *, note: str = "p"
) -> None:
    async with scoped_session(f, hid, None, household_wide=True) as s:
        await LedgerWriter(s, hid, user_id=USER).write_splits(
            tid, [SplitInput(D(p), note=note) for p in parts]
        )


# ----------------------------------------------------------------- upsert_external


async def test_upsert_inserts_with_outflow_negative_then_replays_unchanged(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household)
    new = connector(a, "ext-1", "-12.34")
    async with scoped_session(factory, household, None, household_wide=True) as s:
        first = await LedgerWriter(s, household).upsert_external(new)
    assert first.action is UpsertAction.INSERTED
    r = await row(factory, household, first.transaction_id)
    assert r.amount == D("-12.34") and r.amount < 0 and r.external_id == "ext-1"
    async with scoped_session(factory, household, None, household_wide=True) as s:
        again = await LedgerWriter(s, household).upsert_external(new)
    assert again.action is UpsertAction.UNCHANGED and again.transaction_id == first.transaction_id


async def test_upsert_updates_fields_in_place_on_account_and_external_id(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household)
    other = await conn_account(factory, household)
    tid = await put(factory, household, connector(a, "ext-1", "-5.00", is_pending=True))
    moved = connector(a, "ext-1", "-5.00", description_raw="EXAMPLE MART 2", merchant_name="Mart")
    async with scoped_session(factory, household, None, household_wide=True) as s:
        out = await LedgerWriter(s, household).upsert_external(moved)
    assert out.action is UpsertAction.UPDATED and out.transaction_id == tid
    r = await row(factory, household, tid)
    assert (r.description_raw, r.merchant_name, r.is_pending) == ("EXAMPLE MART 2", "Mart", False)
    # The same external id on another account is another row.
    assert await put(factory, household, connector(other, "ext-1", "-5.00")) != tid


async def test_upsert_rejects_float_amounts_and_non_connector_rows(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        svc = LedgerWriter(s, household)
        with pytest.raises(LedgerRuleError):
            await svc.upsert_external(
                NewTransaction(a, DAY, 4.2, "X", "plaid", external_id="e")  # type: ignore[arg-type]
            )
        with pytest.raises(LedgerRuleError):
            await svc.upsert_external(NewTransaction(a, DAY, D("-1.00"), "X", "plaid"))
        with pytest.raises(LedgerRuleError):
            await svc.upsert_external(connector(a, "e", "-1.00", source="manual"))


@pytest.mark.parametrize(
    ("held", "kept"),
    [("user", True), ("rule", True), ("plaid", False)],
)
async def test_upsert_never_replaces_a_user_or_rule_category(
    factory: Factory, household: uuid.UUID, held: str, kept: bool
) -> None:
    a = await conn_account(factory, household)
    mine = await category(factory, household, "Mine")
    theirs = await category(factory, household, "Theirs")
    tid = await put(factory, household, connector(a, "ext-1", "-5.00"))
    await touch(factory, household, tid, category_id=mine, category_source=held)
    incoming = connector(
        a, "ext-1", "-5.00", category_id=theirs, category_source=AutoCategorySource.PLAID
    )
    async with scoped_session(factory, household, None, household_wide=True) as s:
        out = await LedgerWriter(s, household).upsert_external(incoming)
    r = await row(factory, household, tid)
    assert out.category_kept is kept
    assert (r.category_id, r.category_source) == ((mine, held) if kept else (theirs, "plaid"))


async def test_upsert_ml_does_not_replace_plaid_and_none_does_not_clear(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household)
    c = await category(factory, household)
    d = await category(factory, household, "Other")
    tid = await put(
        factory,
        household,
        connector(a, "ext-1", "-5.00", category_id=c, category_source=AutoCategorySource.PLAID),
    )
    await put(
        factory,
        household,
        connector(a, "ext-1", "-5.00", category_id=d, category_source=AutoCategorySource.ML),
    )
    await put(factory, household, connector(a, "ext-1", "-5.00"))
    r = await row(factory, household, tid)
    assert (r.category_id, r.category_source) == (c, "plaid")


async def test_upsert_changed_amount_clears_splits_and_reports_it(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household)
    tid = await put(factory, household, connector(a, "ext-1", "-10.00"))
    await split(factory, household, tid, ["-6.00", "-4.00"])
    async with scoped_session(factory, household, None, household_wide=True) as s:
        svc = LedgerWriter(s, household)
        out = await svc.upsert_external(connector(a, "ext-1", "-10.50"))
        assert svc.counts["splits_cleared"] == 1
    assert out.splits_cleared and out.action is UpsertAction.UPDATED
    assert await splits_of(factory, household, tid) == []
    assert (await row(factory, household, tid)).amount == D("-10.50")


async def test_upsert_same_amount_keeps_splits(factory: Factory, household: uuid.UUID) -> None:
    a = await conn_account(factory, household)
    tid = await put(factory, household, connector(a, "ext-1", "-10.00"))
    await split(factory, household, tid, ["-6.00", "-4.00"])
    await put(factory, household, connector(a, "ext-1", "-10.00", description_raw="CHANGED"))
    assert await splits_of(factory, household, tid) == [D("-6.00"), D("-4.00")]


async def test_upsert_amount_change_on_a_paired_leg_dissolves_the_pair(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household)
    b = await conn_account(factory, household)
    out_leg = await put(factory, household, connector(a, "ext-out", "-20.00"))
    in_leg = await put(factory, household, connector(b, "ext-in", "20.00"))
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await LedgerWriter(s, household, user_id=USER).mark_transfer_pair(out_leg, in_leg)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        out = await LedgerWriter(s, household).upsert_external(connector(a, "ext-out", "-20.25"))
    assert out.transfer_dissolved
    for tid in (out_leg, in_leg):
        r = await row(factory, household, tid)
        assert (r.is_transfer, r.transfer_group_id) == (False, None)


# ---------------------------------------------------------------- remove_external


async def test_remove_external_deletes_and_reports_missing(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household)
    tid = await put(factory, household, connector(a, "ext-1", "-5.00"))
    async with scoped_session(factory, household, None, household_wide=True) as s:
        svc = LedgerWriter(s, household)
        assert await svc.remove_external(a, "ext-1") is True
    assert await maybe_row(factory, household, tid) is None
    async with scoped_session(factory, household, None, household_wide=True) as s:
        assert await LedgerWriter(s, household).remove_external(a, "ext-1") is False


async def test_remove_external_of_a_winner_gives_the_old_row_back_its_state(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household)
    old = await import_row(factory, household, a, "-5.00", notes="kept note")
    new = await put(factory, household, connector(a, "ext-1", "-5.00"))
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await LedgerWriter(s, household).supersede_transaction(old, new)
    assert (await row(factory, household, old)).superseded_by_id == new
    async with scoped_session(factory, household, None, household_wide=True) as s:
        assert await LedgerWriter(s, household).remove_external(a, "ext-1") is True
    r = await row(factory, household, old)
    assert r.superseded_by_id is None and r.notes == "kept note"
    assert await maybe_row(factory, household, new) is None


async def test_remove_external_of_a_winner_clears_its_transfer_state_first(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household)
    b = await conn_account(factory, household)
    old = await import_row(factory, household, a, "-30.00")
    partner = await put(factory, household, connector(b, "ext-p", "30.00"))
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await LedgerWriter(s, household, user_id=USER).mark_transfer_pair(old, partner)
    new = await put(factory, household, connector(a, "ext-1", "-30.00"))
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await LedgerWriter(s, household).supersede_transaction(old, new)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await LedgerWriter(s, household).remove_external(a, "ext-1")
    r_old, r_partner = await row(factory, household, old), await row(factory, household, partner)
    assert r_old.transfer_group_id is not None
    assert r_old.transfer_group_id == r_partner.transfer_group_id and r_old.is_transfer


# ------------------------------------------------------------------ post_pending


async def test_post_pending_replaces_the_pending_row_and_carries_everything(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household)
    mine = await category(factory, household, "Mine")
    theirs = await category(factory, household, "Theirs")
    doc = await document(factory, household)
    pend = await put(factory, household, connector(a, "pend-1", "-10.00", is_pending=True))
    await touch(
        factory,
        household,
        pend,
        category_id=mine,
        category_source="user",
        notes="my note",
        is_tax_relevant=True,
        tax_tag="work",
        receipt_document_id=doc,
    )
    await split(factory, household, pend, ["-7.00", "-3.00"])
    posted = connector(
        a, "post-1", "-10.00", category_id=theirs, category_source=AutoCategorySource.PLAID
    )
    async with scoped_session(factory, household, None, household_wide=True) as s:
        new_id = await LedgerWriter(s, household).post_pending("pend-1", posted)
    assert await maybe_row(factory, household, pend) is None
    r = await row(factory, household, new_id)
    assert (r.category_id, r.category_source) == (mine, "user")
    assert (r.notes, r.is_tax_relevant, r.tax_tag, r.receipt_document_id) == (
        "my note",
        True,
        "work",
        doc,
    )
    assert (r.external_id, r.pending_external_id, r.is_pending) == ("post-1", "pend-1", False)
    assert r.amount == D("-10.00")
    assert await splits_of(factory, household, new_id) == [D("-7.00"), D("-3.00")]


async def test_post_pending_lower_ranked_pending_category_does_not_win(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household)
    old_cat = await category(factory, household, "Old")
    new_cat = await category(factory, household, "New")
    pend = await put(
        factory,
        household,
        connector(
            a,
            "pend-1",
            "-4.00",
            is_pending=True,
            category_id=old_cat,
            category_source=AutoCategorySource.ML,
        ),
    )
    assert pend
    posted = connector(
        a, "post-1", "-4.00", category_id=new_cat, category_source=AutoCategorySource.PLAID
    )
    async with scoped_session(factory, household, None, household_wide=True) as s:
        new_id = await LedgerWriter(s, household).post_pending("pend-1", posted)
    r = await row(factory, household, new_id)
    assert (r.category_id, r.category_source) == (new_cat, "plaid")


async def test_post_pending_changed_amount_drops_splits_and_reports_it(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household)
    pend = await put(factory, household, connector(a, "pend-1", "-10.00", is_pending=True))
    await split(factory, household, pend, ["-7.00", "-3.00"])
    async with scoped_session(factory, household, None, household_wide=True) as s:
        svc = LedgerWriter(s, household)
        new_id = await svc.post_pending("pend-1", connector(a, "post-1", "-11.25"))
        assert svc.counts["splits_cleared"] == 1
    assert await splits_of(factory, household, new_id) == []
    assert (await row(factory, household, new_id)).amount == D("-11.25")
    assert await maybe_row(factory, household, pend) is None


async def test_post_pending_carries_a_paired_transfer_leg(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household)
    b = await conn_account(factory, household)
    pend = await put(factory, household, connector(a, "pend-1", "-15.00", is_pending=True))
    partner = await put(factory, household, connector(b, "ext-p", "15.00"))
    async with scoped_session(factory, household, None, household_wide=True) as s:
        group = await LedgerWriter(s, household, user_id=USER).mark_transfer_pair(pend, partner)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        new_id = await LedgerWriter(s, household).post_pending(
            "pend-1", connector(a, "post-1", "-15.00")
        )
    assert (await row(factory, household, new_id)).transfer_group_id == group
    assert (await row(factory, household, partner)).transfer_group_id == group


async def test_post_pending_refuses_a_missing_pending_row_and_writes_nothing(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        with pytest.raises(TransactionNotFoundError):
            await LedgerWriter(s, household).post_pending("nope", connector(a, "post-1", "-1.00"))
    async with scoped_session(factory, household, None, household_wide=True) as s:
        found = await s.execute(select(Transaction.id).where(Transaction.account_id == a))
        assert found.first() is None


async def test_post_pending_refuses_a_posted_row_that_is_pending_or_a_live_row(
    factory: Factory, household: uuid.UUID
) -> None:
    a = await conn_account(factory, household)
    await put(factory, household, connector(a, "pend-1", "-1.00", is_pending=True))
    await put(factory, household, connector(a, "plain", "-1.00"))
    async with scoped_session(factory, household, None, household_wide=True) as s:
        svc = LedgerWriter(s, household)
        with pytest.raises(LedgerRuleError):
            await svc.post_pending("pend-1", connector(a, "post-1", "-1.00", is_pending=True))
        with pytest.raises(LedgerRuleError):  # "plain" is not pending
            await svc.post_pending("plain", connector(a, "post-2", "-1.00"))
        with pytest.raises(LedgerRuleError):  # the posted external id is taken
            await svc.post_pending("pend-1", connector(a, "plain", "-1.00"))
