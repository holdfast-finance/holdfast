"""IngestService against real Postgres (PHASE-2 3.4, ADR 0033, ADR 0035).

Synthetic data only. Amounts are ``Decimal`` built from strings: outflow negative.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db_context import create_household, scoped_session, unscoped_session
from app.domain.categories import pfc_category_slug
from app.domain.connector import ConnectorAccount, ConnectorBalance, ConnectorTxn, SyncBatch
from app.domain.ledger_rules import LedgerRuleError
from app.domain.statement_parse import ParsedStatement, ParsedTxn, Reconciliation, parse_statement
from app.importers.profile_loader import load_profiles
from app.models import (
    Account,
    AccountCoverage,
    BalanceSnapshot,
    Category,
    ConnectorAccountLink,
    Document,
    ImportRun,
    Transaction,
)
from app.services.categories import ensure_default_categories
from app.services.ingest import IngestCounts, IngestService
from app.services.ledger_service import (
    LedgerWriter,
    NewTransaction,
    TransactionNotFoundError,
    UpsertOutcome,
)
from app.services.statement_import import AccountTarget, DocumentMeta, import_statement
from tests.ledger.test_ledger_sync_verbs import USER, conn_account, people, touch

pytestmark = pytest.mark.services
D = Decimal
Factory = async_sessionmaker[AsyncSession]
DAY = date(2026, 3, 10)
EXT = "acct-ext-1"
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)


async def mapped_account(
    f: Factory, hid: uuid.UUID, *, ext: str = EXT, owner: uuid.UUID | None = None
) -> tuple[uuid.UUID, uuid.UUID]:
    """An adopted account with a mapped link. Returns ``(connection_id, account_id)``."""
    aid = await conn_account(f, hid, owner=owner)
    async with scoped_session(f, hid, None, household_wide=True) as s:
        cid = (
            await s.execute(select(Account.bank_connection_id).where(Account.id == aid))
        ).scalar_one()
        s.add(
            ConnectorAccountLink(
                household_id=hid,
                bank_connection_id=cid,
                external_account_id=ext,
                state="mapped",
                account_id=aid,
                decided_at=datetime.now(UTC),
            )
        )
    return cid, aid


async def imported(
    f: Factory,
    hid: uuid.UUID,
    aid: uuid.UUID,
    amount: str,
    *,
    posted: date = DAY,
    desc: str = "EXAMPLE MART",
    before_adoption: bool = False,
) -> uuid.UUID:
    """A statement row, written by the one writer. ``before_adoption`` backdates ``created_at``."""
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
            account_id=aid,
            status="running",
            started_at=datetime(2026, 3, 1, tzinfo=UTC),
        )
        s.add(run)
        await s.flush()
        tid = await LedgerWriter(s, hid).insert_transaction(
            NewTransaction(
                account_id=aid,
                posted_date=posted,
                amount=D(amount),
                description_raw=desc,
                source="import",
                import_fingerprint=uuid.uuid4().hex * 2,
                import_run_id=run.id,
            ),
            skip_duplicates=True,
        )
    assert tid is not None
    if before_adoption:
        await touch(f, hid, tid, created_at=LONG_AGO)
    return tid


def txn(
    ext_id: str,
    amount: str,
    *,
    posted: date = DAY,
    authorized: date | None = None,
    desc: str = "EXAMPLE MART",
    pending: bool = False,
    pending_id: str | None = None,
    ext: str = EXT,
    pfc: tuple[str, str] | None = None,
) -> ConnectorTxn:
    return ConnectorTxn(
        external_account_id=ext,
        external_id=ext_id,
        posted_date=posted,
        authorized_date=authorized,
        amount=D(amount),
        description_raw=desc,
        merchant_name=None,
        pfc_primary=pfc[0] if pfc else None,
        pfc_detailed=pfc[1] if pfc else None,
        is_pending=pending,
        pending_external_id=pending_id,
    )


def account(ext: str = EXT, currency: str | None = "USD") -> ConnectorAccount:
    return ConnectorAccount(
        external_account_id=ext,
        name="Example Checking",
        official_name=None,
        mask="0000",
        type="depository",
        subtype=None,
        currency=currency,
        institution_name="First Example Bank",
    )


def batch(
    *,
    added: tuple[ConnectorTxn, ...] = (),
    modified: tuple[ConnectorTxn, ...] = (),
    removed: tuple[tuple[str, str], ...] = (),
    balances: tuple[ConnectorBalance, ...] = (),
    accounts: tuple[ConnectorAccount, ...] | None = None,
    complete: bool = True,
) -> SyncBatch:
    return SyncBatch(
        accounts=(account(),) if accounts is None else accounts,
        balances=balances,
        added=added,
        modified=modified,
        removed=removed,
        next_cursor="c1",
        has_more=False,
        history_complete=complete,
    )


async def ingest(f: Factory, hid: uuid.UUID, cid: uuid.UUID, b: SyncBatch) -> IngestCounts:
    async with scoped_session(f, hid, None, household_wide=True) as s:
        return await IngestService(s, hid).apply(cid, b)


async def all_rows(f: Factory, hid: uuid.UUID, aid: uuid.UUID) -> list[Transaction]:
    async with scoped_session(f, hid, None, household_wide=True) as s:
        rows = await s.execute(
            select(Transaction)
            .where(Transaction.account_id == aid)
            .order_by(Transaction.posted_date, Transaction.id)
        )
        return list(rows.scalars())


async def live(f: Factory, hid: uuid.UUID, aid: uuid.UUID) -> list[Transaction]:
    return [r for r in await all_rows(f, hid, aid) if r.superseded_by_id is None]


async def ledger_state(
    f: Factory, hid: uuid.UUID, aid: uuid.UUID
) -> list[tuple[uuid.UUID, datetime, uuid.UUID | None]]:
    return [(r.id, r.updated_at, r.superseded_by_id) for r in await all_rows(f, hid, aid)]


async def covered(f: Factory, hid: uuid.UUID, aid: uuid.UUID, start: date, end: date) -> None:
    async with scoped_session(f, hid, None, household_wide=True) as s:
        s.add(AccountCoverage(household_id=hid, account_id=aid, start_date=start, end_date=end))


def card_fixture() -> tuple[ParsedStatement, list[ParsedTxn]]:
    fixture = Path(__file__).parents[1] / "fixtures/supersede/card-layout.sanitized.txt"
    profile = next(p for p in load_profiles() if p.id == "navy-federal-card")
    statement = parse_statement(fixture.read_text(encoding="utf-8"), profile)
    return statement, list(statement.blocks[0].txns)


def twins_of(rows: list[ParsedTxn]) -> tuple[ConnectorTxn, ...]:
    """Connector rows posted 0 to 3 days after each printed date, authorized on the printed date."""
    return tuple(
        txn(
            f"p{i}",
            str(t.amount),
            posted=t.posted_date + timedelta(days=i % 4),
            authorized=t.posted_date,
            desc=t.description,
        )
        for i, t in enumerate(rows)
    )


# --------------------------------------------------------------- acceptance


async def test_trans_ne_post_fixture_leaves_zero_live_duplicates_and_replays_clean(
    factory: Factory, household: uuid.UUID
) -> None:
    """Sanitized card layout: transaction date != posting date. Four equal daily charges and
    the interest row, connector rows posted 0 to 3 days after the printed date."""
    _, rows = card_fixture()
    cid, aid = await mapped_account(factory, household)
    for t in rows:
        await imported(
            factory,
            household,
            aid,
            str(t.amount),
            posted=t.posted_date,
            desc=t.description,
        )
    added = twins_of(rows)
    first = await ingest(factory, household, cid, batch(added=added))
    assert first.added == 5 and first.superseded == 5 and first.held == 0
    live_rows = await live(factory, household, aid)
    assert len(live_rows) == 5 and {r.source for r in live_rows} == {"plaid"}
    assert sorted(r.amount for r in live_rows) == sorted(t.amount for t in rows)
    assert all(r.amount < 0 for r in live_rows)  # outflow negative

    before = await ledger_state(factory, household, aid)
    for _ in range(2):  # replay twice
        again = await ingest(factory, household, cid, batch(added=added))
        assert again.writes == 0 and again.unchanged == 5
    assert await ledger_state(factory, household, aid) == before


async def test_replaying_a_full_batch_twice_writes_nothing(
    factory: Factory, household: uuid.UUID
) -> None:
    cid, aid = await mapped_account(factory, household)
    b = batch(
        added=(txn("a", "-5.00"), txn("b", "12.50", posted=DAY + timedelta(days=1))),
        removed=((EXT, "gone"),),
        balances=(ConnectorBalance(EXT, DAY, D("100.00"), D("90.00"), None),),
    )
    first = await ingest(factory, household, cid, b)
    assert first.added == 2 and first.balances_written == 1
    before = await ledger_state(factory, household, aid)
    for _ in range(2):
        assert (await ingest(factory, household, cid, b)).writes == 0
    assert await ledger_state(factory, household, aid) == before
    assert [r.amount for r in await live(factory, household, aid)] == [D("-5.00"), D("12.50")]


# ------------------------------------------------------------- pending, removal


async def test_pending_posts_with_removal_in_the_same_batch(
    factory: Factory, household: uuid.UUID
) -> None:
    """Plaid sends removed=[pending id] with added=[posted row naming it]. The removal is the
    pending row posting, so it must not delete the row before its posted twin lands."""
    cid, aid = await mapped_account(factory, household)
    await ingest(factory, household, cid, batch(added=(txn("pend-1", "-9.00", pending=True),)))
    pending_id = (await live(factory, household, aid))[0].id
    await touch(factory, household, pending_id, notes="keep me", is_tax_relevant=True)

    posted = txn("post-1", "-9.00", posted=DAY + timedelta(days=2), pending_id="pend-1")
    b = batch(added=(posted,), removed=((EXT, "pend-1"),))
    counts = await ingest(factory, household, cid, b)
    assert counts.pending_posted == 1 and counts.removed == 0
    rows = await live(factory, household, aid)
    assert [(r.external_id, r.is_pending, r.notes, r.is_tax_relevant) for r in rows] == [
        ("post-1", False, "keep me", True)
    ]
    # Replay: the pending row is gone and the posted row exists, so nothing is written.
    assert (await ingest(factory, household, cid, b)).writes == 0
    assert len(await all_rows(factory, household, aid)) == 1


async def test_a_posted_row_with_an_unseen_pending_twin_falls_back_to_upsert(
    factory: Factory, household: uuid.UUID
) -> None:
    cid, aid = await mapped_account(factory, household)
    counts = await ingest(
        factory, household, cid, batch(added=(txn("post-1", "-4.00", pending_id="never-seen"),))
    )
    assert counts.added == 1 and counts.pending_posted == 0
    assert [r.external_id for r in await live(factory, household, aid)] == ["post-1"]


async def test_post_pending_not_found_race_falls_back_to_upsert(
    factory: Factory, household: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, aid = await mapped_account(factory, household)
    await ingest(factory, household, cid, batch(added=(txn("pend-1", "-9.00", pending=True),)))

    async def vanished(self: LedgerWriter, pending: str, posted: NewTransaction) -> uuid.UUID:
        await self.upsert_external(replace(posted, description_raw="partial pending write"))
        raise TransactionNotFoundError("pending transaction not found")

    monkeypatch.setattr(LedgerWriter, "post_pending", vanished)
    counts = await ingest(
        factory, household, cid, batch(added=(txn("post-1", "-9.00", pending_id="pend-1"),))
    )
    assert counts.added == 1
    by_external = {r.external_id: r for r in await live(factory, household, aid)}
    assert by_external["post-1"].description_raw == "EXAMPLE MART"


async def test_modified_amount_updates_before_independent_additions(
    factory: Factory, household: uuid.UUID
) -> None:
    cid, aid = await mapped_account(factory, household)
    await ingest(factory, household, cid, batch(added=(txn("existing", "-5.00"),)))
    update_batch = batch(
        modified=(txn("existing", "-7.00"),),
        added=(txn("new", "-2.00"),),
    )
    counts = await ingest(factory, household, cid, update_batch)
    assert counts.modified == 1 and counts.added == 1
    assert {row.external_id: row.amount for row in await live(factory, household, aid)} == {
        "existing": D("-7.00"),
        "new": D("-2.00"),
    }
    assert (await ingest(factory, household, cid, update_batch)).writes == 0


async def test_a_genuine_removal_deletes_and_a_missing_one_is_success(
    factory: Factory, household: uuid.UUID
) -> None:
    cid, aid = await mapped_account(factory, household)
    await ingest(factory, household, cid, batch(added=(txn("a", "-1.00"),)))
    counts = await ingest(
        factory, household, cid, batch(removed=((EXT, "a"), (EXT, "never-existed")))
    )
    assert counts.removed == 1
    assert await all_rows(factory, household, aid) == []


# ----------------------------------------------------------------- coverage


async def test_covered_posted_rows_are_skipped_and_gaps_and_pending_are_not(
    factory: Factory, household: uuid.UUID
) -> None:
    cid, aid = await mapped_account(factory, household)
    await covered(factory, household, aid, date(2026, 2, 1), date(2026, 2, 28))
    counts = await ingest(
        factory,
        household,
        cid,
        batch(
            added=(
                txn("in", "-1.00", posted=date(2026, 2, 10)),
                txn("edge", "-1.00", posted=date(2026, 2, 28)),
                txn("gap", "-1.00", posted=date(2026, 1, 20)),
                txn("after", "-1.00", posted=date(2026, 3, 2)),
                txn("pend", "-1.00", posted=date(2026, 2, 12), pending=True),
            ),
            modified=(txn("in-2", "-2.00", posted=date(2026, 2, 11)),),
            removed=((EXT, "in"),),  # a covered row was never inserted: no-op
        ),
    )
    assert counts.skipped_covered == 3 and counts.added == 3
    assert {r.external_id for r in await live(factory, household, aid)} == {"gap", "after", "pend"}


async def test_covered_post_keeps_pending_row_with_user_metadata(
    factory: Factory, household: uuid.UUID
) -> None:
    cid, aid = await mapped_account(factory, household)
    await covered(factory, household, aid, DAY, DAY)
    await ingest(factory, household, cid, batch(added=(txn("pending", "-9.00", pending=True),)))
    pending = (await live(factory, household, aid))[0]
    await touch(factory, household, pending.id, notes="keep for review")
    counts = await ingest(
        factory,
        household,
        cid,
        batch(added=(txn("posted", "-9.00", pending_id="pending"),), removed=((EXT, "pending"),)),
    )
    assert counts.held_pending_user_metadata == 1
    assert counts.removed == 0
    assert [(row.external_id, row.notes) for row in await live(factory, household, aid)] == [
        ("pending", "keep for review"),
    ]


@pytest.mark.parametrize("lag", [1, 2, 3])
async def test_coverage_edge_twin_posted_after_end_date_is_paired(
    factory: Factory, household: uuid.UUID, lag: int
) -> None:
    """ADR 0035 6: the statement row sits on the last covered day, its connector twin posts
    1 to 3 days after the end date. Pinned: they pair, so there is no live duplicate."""
    cid, aid = await mapped_account(factory, household)
    end = date(2026, 2, 28)
    await imported(factory, household, aid, "-30.00", posted=end, before_adoption=True)
    await covered(factory, household, aid, date(2026, 2, 1), end)
    counts = await ingest(
        factory,
        household,
        cid,
        batch(added=(txn("c1", "-30.00", posted=end + timedelta(days=lag), authorized=end),)),
    )
    assert counts.added == 1 and counts.superseded == 1
    assert [r.source for r in await live(factory, household, aid)] == ["plaid"]


async def test_a_twin_more_than_three_days_after_coverage_stays_a_visible_duplicate(
    factory: Factory, household: uuid.UUID
) -> None:
    cid, aid = await mapped_account(factory, household)
    end = date(2026, 2, 28)
    await imported(factory, household, aid, "-30.00", posted=end, before_adoption=True)
    await covered(factory, household, aid, date(2026, 2, 1), end)
    counts = await ingest(
        factory,
        household,
        cid,
        batch(added=(txn("c1", "-30.00", posted=end + timedelta(days=4)),)),
    )
    assert counts.superseded == 0
    assert len(await live(factory, household, aid)) == 2  # ADR 0035 6, accepted residual


# ------------------------------------------------------------ matching rules


async def test_ambiguous_pair_with_no_shared_token_is_held_not_superseded(
    factory: Factory, household: uuid.UUID
) -> None:
    cid, aid = await mapped_account(factory, household)
    await imported(factory, household, aid, "-100.00", desc="ATM WITHDRAWAL")
    counts = await ingest(
        factory,
        household,
        cid,
        batch(
            added=(
                txn("t1", "-100.00", desc="TRANSFER OUT", posted=DAY + timedelta(days=1)),
                txn("t2", "-100.00", desc="TRANSFER OUT", posted=DAY + timedelta(days=2)),
            )
        ),
    )
    assert counts.held == 1 and counts.superseded == 0
    assert len(await live(factory, household, aid)) == 3


async def test_matching_waits_for_complete_history(factory: Factory, household: uuid.UUID) -> None:
    cid, aid = await mapped_account(factory, household)
    await imported(factory, household, aid, "-7.00")
    part = await ingest(factory, household, cid, batch(added=(txn("c1", "-7.00"),), complete=False))
    assert part.matching_deferred and part.superseded == 0
    done = await ingest(factory, household, cid, batch(complete=True))
    assert done.superseded == 1
    assert [r.source for r in await live(factory, household, aid)] == ["plaid"]


async def test_split_batches_leave_the_same_live_multiset(
    factory: Factory, household: uuid.UUID
) -> None:
    days = [DAY + timedelta(days=i * 10) for i in range(4)]
    results: list[list[Decimal]] = []
    for split_at in (0, 2, 4):
        cid, aid = await mapped_account(factory, household)
        for d in days:
            await imported(factory, household, aid, "-5.00", posted=d)
        rows = [txn(f"c{i}", "-5.00", posted=d + timedelta(days=1)) for i, d in enumerate(days)]
        await ingest(factory, household, cid, batch(added=tuple(rows[:split_at]), complete=False))
        await ingest(factory, household, cid, batch(added=tuple(rows[split_at:]), complete=True))
        live_rows = await live(factory, household, aid)
        results.append(sorted(r.amount for r in live_rows))
        assert len(live_rows) == 4 and {r.source for r in live_rows} == {"plaid"}
    assert results[0] == results[1] == results[2]


async def test_statement_import_into_an_adopted_account_matches_connector_rows(
    factory: Factory, household: uuid.UUID
) -> None:
    """The other direction (contract 3.4): the connector rows are there first."""
    statement, rows = card_fixture()
    cid, aid = await mapped_account(factory, household)
    await ingest(factory, household, cid, batch(added=twins_of(rows)))
    assert len(await live(factory, household, aid)) == 5
    meta = DocumentMeta(sha256="a" * 64, filename="s.pdf", storage_path="s/1.pdf", size_bytes=1)
    await import_statement(
        factory,
        household,
        statement,
        meta,
        household_wide=True,
        target=AccountTarget(aid),
    )
    live_rows = await live(factory, household, aid)
    assert len(live_rows) == 5 and {r.source for r in live_rows} == {"plaid"}


async def test_statement_reimport_after_connector_matching_leaves_ledger_unchanged(
    factory: Factory, household: uuid.UUID
) -> None:
    statement, rows = card_fixture()
    cid, aid = await mapped_account(factory, household)
    meta = DocumentMeta(sha256="b" * 64, filename="s.pdf", storage_path="s/2.pdf", size_bytes=1)
    for first in (True, False):
        await import_statement(
            factory,
            household,
            statement,
            meta,
            household_wide=True,
            target=AccountTarget(aid),
        )
        if first:
            await ingest(factory, household, cid, batch(added=twins_of(rows)))
            before = await ledger_state(factory, household, aid)
    assert await ledger_state(factory, household, aid) == before
    assert len(await live(factory, household, aid)) == 5


async def test_flagged_statement_stays_out_of_connector_matching(
    factory: Factory, household: uuid.UUID
) -> None:
    statement, rows = card_fixture()
    cid, aid = await mapped_account(factory, household)
    block = replace(
        statement.blocks[0],
        opening_balance=None,
        reconciliation=Reconciliation("needs_review", "missing_balance"),
    )
    await import_statement(
        factory,
        household,
        replace(statement, blocks=(block,)),
        DocumentMeta(sha256="c" * 64, filename="s.pdf", storage_path="s/3.pdf", size_bytes=1),
        household_wide=True,
        target=AccountTarget(aid),
    )
    counts = await ingest(factory, household, cid, batch(added=twins_of(rows)))
    assert counts.superseded == 0
    assert len(await live(factory, household, aid)) == 2 * len(rows)


# ----------------------------------------------------------------- accounts


async def test_unseen_ignored_and_null_currency_accounts_are_not_ingested(
    factory: Factory, household: uuid.UUID
) -> None:
    cid, _aid = await mapped_account(factory, household)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        s.add(
            ConnectorAccountLink(
                household_id=household,
                bank_connection_id=cid,
                external_account_id="ign",
                state="ignored",
                decided_at=datetime.now(UTC),
            )
        )
    b = batch(
        added=(
            txn("a", "-1.00"),
            txn("b", "-2.00", ext="ign"),
            txn("c", "-3.00", ext="new-one"),
            txn("d", "-4.00", ext="nocur"),
        ),
        accounts=(account(), account("ign"), account("new-one"), account("nocur", None)),
    )
    counts = await ingest(factory, household, cid, b)
    assert counts.added == 1 and counts.accounts_pending == 1 and counts.skipped_currency == 1
    async with scoped_session(factory, household, None, household_wide=True) as s:
        links = {
            link.external_account_id: link.state
            for link in (await s.execute(select(ConnectorAccountLink))).scalars()
        }
    assert links == {EXT: "mapped", "ign": "ignored", "new-one": "pending"}
    assert (await ingest(factory, household, cid, b)).accounts_pending == 0  # not a second link


async def test_a_currency_that_differs_from_the_account_is_skipped(
    factory: Factory, household: uuid.UUID
) -> None:
    cid, _aid = await mapped_account(factory, household)
    counts = await ingest(
        factory,
        household,
        cid,
        batch(added=(txn("a", "-1.00"),), accounts=(account(currency="EUR"),)),
    )
    assert counts.skipped_currency == 1 and counts.added == 0


async def test_link_cannot_ingest_into_another_connections_account(
    factory: Factory, household: uuid.UUID
) -> None:
    cid, first = await mapped_account(factory, household)
    _, other = await mapped_account(factory, household)
    async with scoped_session(factory, household, None, household_wide=True) as session:
        await session.execute(
            update(ConnectorAccountLink)
            .where(ConnectorAccountLink.bank_connection_id == cid)
            .values(account_id=other)
        )
    counts = await ingest(factory, household, cid, batch(added=(txn("misrouted", "-1.00"),)))
    assert counts.skipped_unmapped == 1
    assert not await live(factory, household, first)
    assert not await live(factory, household, other)


async def test_row_currency_is_checked_without_reported_accounts(
    factory: Factory, household: uuid.UUID
) -> None:
    cid, aid = await mapped_account(factory, household)
    row = replace(txn("foreign", "-1.00"), currency="EUR")
    counts = await ingest(factory, household, cid, batch(added=(row,), accounts=()))
    assert counts.skipped_currency == 1
    assert await live(factory, household, aid) == []


async def test_balances_upsert_per_day_and_never_overwrite_a_manual_snapshot(
    factory: Factory, household: uuid.UUID
) -> None:
    cid, aid = await mapped_account(factory, household)
    day_before = DAY - timedelta(days=1)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        s.add(
            BalanceSnapshot(
                household_id=household, account_id=aid, as_of_date=day_before, current=D("1.00")
            )
        )
    bals = (
        ConnectorBalance(EXT, day_before, D("999.00"), None, None),
        ConnectorBalance(EXT, DAY, D("-20.00"), None, D("500.00")),
    )
    first = await ingest(factory, household, cid, batch(balances=bals))
    assert first.balances_written == 1 and first.balances_skipped_manual == 1
    second = await ingest(
        factory,
        household,
        cid,
        batch(balances=(ConnectorBalance(EXT, DAY, D("-25.00"), None, D("500.00")),)),
    )
    assert second.balances_written == 1
    async with scoped_session(factory, household, None, household_wide=True) as s:
        snaps = {
            r.as_of_date: (r.current, r.bank_connection_id is not None)
            for r in (await s.execute(select(BalanceSnapshot))).scalars()
        }
        n = (await s.execute(select(func.count()).select_from(BalanceSnapshot))).scalar_one()
    assert n == 2
    assert snaps[day_before] == (D("1.00"), False)
    assert snaps[DAY] == (D("-25.00"), True)


async def test_plaid_categories_map_and_signs_are_kept(
    factory: Factory, household: uuid.UUID
) -> None:
    cid, aid = await mapped_account(factory, household)
    primary, detailed = "FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE"
    slug = pfc_category_slug(primary, detailed)
    assert slug is not None
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await ensure_default_categories(s, household)
    await ingest(
        factory,
        household,
        cid,
        batch(added=(txn("a", "-3.50", pfc=(primary, detailed)), txn("b", "1200.00"))),
    )
    async with scoped_session(factory, household, None, household_wide=True) as s:
        expected = (await s.execute(select(Category.id).where(Category.slug == slug))).scalar_one()
    rows = {r.external_id: r for r in await all_rows(factory, household, aid)}
    assert rows["a"].category_id == expected and rows["a"].category_source == "plaid"
    assert rows["a"].amount == D("-3.50") and rows["b"].amount == D("1200.00")
    assert rows["b"].category_id is None


# ----------------------------------------------------------------- callers


async def test_a_person_session_needs_a_named_user(factory: Factory, household: uuid.UUID) -> None:
    alex, _ = await people(factory, household)
    cid, _aid = await mapped_account(factory, household)
    b = batch(added=(txn("a", "-1.00"),))
    async with scoped_session(factory, household, alex) as s:
        with pytest.raises(LedgerRuleError):
            await IngestService(s, household).apply(cid, b)
    async with scoped_session(factory, household, alex) as s:
        counts = await IngestService(s, household, user_id=USER).apply(cid, b)
    assert counts.added == 1


async def test_an_unwritable_partner_on_a_shared_account_does_not_block_supersession(
    factory: Factory, household: uuid.UUID
) -> None:
    """Sam syncs a joint account whose imported row is paired with a leg on Alex's *shared*
    account: Sam can see that leg but not write it."""
    alex, sam = await people(factory, household)
    cid, joint = await mapped_account(factory, household)
    alexs = await conn_account(factory, household, owner=alex)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        await s.execute(update(Account).where(Account.id == alexs).values(visibility="shared"))
    old = await imported(factory, household, joint, "-30.00")
    async with scoped_session(factory, household, None, household_wide=True) as s:
        partner = await LedgerWriter(s, household).upsert_external(
            NewTransaction(
                account_id=alexs,
                posted_date=DAY,
                amount=D("30.00"),
                description_raw="EXAMPLE TRANSFER",
                source="plaid",
                external_id="p-1",
            )
        )
        group = await LedgerWriter(s, household, user_id=USER).mark_transfer_pair(
            old, partner.transaction_id
        )
    async with scoped_session(factory, household, sam) as s:
        counts = await IngestService(s, household, user_id=USER).apply(
            cid, batch(added=(txn("c1", "-30.00"),))
        )
    assert counts.added == 1 and counts.superseded == 1
    rows = {r.external_id: r for r in await live(factory, household, joint)}
    assert rows["c1"].transfer_group_id == group  # took over the pair state
    async with scoped_session(factory, household, None, household_wide=True) as s:
        leg = (
            await s.execute(select(Transaction).where(Transaction.id == partner.transaction_id))
        ).scalar_one()
    assert leg.transfer_group_id == group  # the partner leg was not touched


async def test_apply_serializes_the_same_connection(factory: Factory, household: uuid.UUID) -> None:
    cid, _ = await mapped_account(factory, household)
    async with scoped_session(factory, household, None, household_wide=True) as first:
        await IngestService(first, household).apply(cid, batch())
        async with scoped_session(factory, household, None, household_wide=True) as second:
            entered = asyncio.Event()

            async def apply_second() -> IngestCounts:
                entered.set()
                return await IngestService(second, household).apply(cid, batch())

            task = asyncio.create_task(apply_second())
            try:
                await entered.wait()
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), timeout=0.1)
                assert await first.scalar(text("SELECT 1")) == 1
                await first.commit()
                await asyncio.wait_for(task, timeout=5)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)


async def test_apply_rejects_a_foreign_household_connection(
    factory: Factory, household: uuid.UUID
) -> None:
    cid, aid = await mapped_account(factory, household)
    async with unscoped_session(factory) as session:
        other = await create_household(session, "Example Foreign Household")
    with pytest.raises(LedgerRuleError, match="connection not found"):
        await ingest(factory, other, cid, batch(added=(txn("foreign", "-5.00"),)))
    assert not await live(factory, household, aid)
    async with scoped_session(factory, other, None, household_wide=True) as session:
        for model in (Transaction, BalanceSnapshot, ConnectorAccountLink):
            assert await session.scalar(select(func.count()).select_from(model)) == 0


async def test_failure_rolls_back_transactions_balances_and_new_links(
    factory: Factory, household: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, aid = await mapped_account(factory, household)
    original = LedgerWriter.upsert_external

    async def fail_second(self: LedgerWriter, row: NewTransaction) -> UpsertOutcome:
        if row.external_id == "fail":
            raise RuntimeError("synthetic failure after first write")
        return await original(self, row)

    monkeypatch.setattr(LedgerWriter, "upsert_external", fail_second)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        await ingest(
            factory,
            household,
            cid,
            batch(
                added=(txn("first", "-5.00"), txn("fail", "-2.00")),
                accounts=(account(), account("unseen")),
                balances=(ConnectorBalance(EXT, DAY, D("100.00"), None, None),),
            ),
        )
    assert not await live(factory, household, aid)
    async with scoped_session(factory, household, None, household_wide=True) as session:
        assert await session.scalar(select(func.count()).select_from(BalanceSnapshot)) == 0
        links = await session.scalars(select(ConnectorAccountLink.external_account_id))
        assert links.all() == [EXT]


@pytest.mark.parametrize("seed", range(50))
async def test_interleaving_import_rows_and_connector_batches_is_order_independent(
    factory: Factory, household: uuid.UUID, seed: int
) -> None:
    cid, aid = await mapped_account(factory, household)
    days = [DAY + timedelta(days=i) for i in range(4)]
    randomizer = random.Random(seed)  # noqa: S311 - deterministic test permutations
    events = [(kind, i) for kind in ("import", "connector") for i in range(len(days))]
    randomizer.shuffle(events)
    for kind, i in events:
        if kind == "import":
            await imported(factory, household, aid, "-5.00", posted=days[i])
        else:
            row = txn(f"c{i}", "-5.00", posted=days[i] + timedelta(days=1), authorized=days[i])
            await ingest(factory, household, cid, batch(added=(row,), complete=False))
    await ingest(factory, household, cid, batch())
    rows = await live(factory, household, aid)
    assert [(row.external_id, row.amount) for row in rows] == [
        (f"c{i}", D("-5.00")) for i in range(len(days))
    ]
    assert (await ingest(factory, household, cid, batch())).writes == 0
