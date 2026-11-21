"""Statement import through the F6 import-run tables: idempotent, reconciled or flagged."""

from __future__ import annotations

import dataclasses
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import Engine, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db_context import scoped_session
from app.domain.ledger_rules import InvalidAmountError
from app.domain.statement_parse import ParsedStatement, detect_profile, parse_statement
from app.importers.profile_loader import load_profiles
from app.models import Account, Document, ImportRun, Transaction
from app.services.ledger_service import LedgerWriter, NewTransaction
from app.services.statement_import import (
    AccountNotVisibleError,
    DocumentMeta,
    _unexplained_duplicates,
    import_statement,
    infer_review_reason,
)
from tests.importers.test_statement_profiles import DEBIT, FIXTURES, NFCU_CARD
from tests.models.conftest import set_ctx, table

pytestmark = pytest.mark.services

PROFILES = load_profiles()


def _parse(text: str) -> ParsedStatement:
    profile = detect_profile(text, PROFILES)
    assert profile is not None
    return parse_statement(text, profile)


def _meta(n: int) -> DocumentMeta:
    return DocumentMeta(
        sha256=f"{n:064x}",
        filename="statement.pdf",
        storage_path=f"statements/{n}.pdf",
        size_bytes=10,
    )


async def _count(factory: async_sessionmaker[AsyncSession], hid: uuid.UUID, model: type) -> int:
    async with scoped_session(factory, hid, None, household_wide=True) as s:
        return int((await s.execute(select(func.count()).select_from(model))).scalar_one())


async def test_import_creates_accounts_runs_and_signed_transactions(
    factory: async_sessionmaker[AsyncSession], household: uuid.UUID
) -> None:
    result = await import_statement(
        factory, household, _parse(NFCU_CARD), _meta(1), household_wide=True
    )
    (block,) = result.blocks
    assert (block.reconcile_status, block.rows_inserted, block.created) == ("reconciled", 3, True)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        acct = (await s.execute(select(Account))).scalar_one()
        assert (acct.source, acct.type, acct.mask) == ("import", "credit", "0000")
        amounts = sorted(
            (await s.execute(select(Transaction.amount).where(Transaction.account_id == acct.id)))
            .scalars()
            .all()
        )
        assert amounts == [Decimal("-100.00"), Decimal("-25.00"), Decimal("80.00")]
        run = (await s.execute(select(ImportRun))).scalar_one()
        assert run.balance_diff == Decimal("0.00")
        assert run.reconcile_status == "reconciled"
        assert (await s.execute(select(Document))).scalar_one().parse_status == "parsed"


async def test_reimporting_the_same_file_writes_nothing(
    factory: async_sessionmaker[AsyncSession], household: uuid.UUID
) -> None:
    st = _parse(DEBIT)
    first = await import_statement(factory, household, st, _meta(2), household_wide=True)
    counts = [await _count(factory, household, m) for m in (Transaction, ImportRun, Account)]
    again = await import_statement(factory, household, st, _meta(2), household_wide=True)
    assert [
        await _count(factory, household, m) for m in (Transaction, ImportRun, Account)
    ] == counts
    assert first.rows_inserted > 0
    assert again.rows_inserted == 0
    assert all(not b.created for b in again.blocks)


async def test_same_rows_in_a_different_file_are_deduplicated_by_fingerprint(
    factory: async_sessionmaker[AsyncSession], household: uuid.UUID
) -> None:
    st = _parse(DEBIT)
    await import_statement(factory, household, st, _meta(3), household_wide=True)
    before = await _count(factory, household, Transaction)
    again = await import_statement(
        factory, household, st, _meta(4), household_wide=True
    )  # new bytes, same content
    assert await _count(factory, household, Transaction) == before
    assert again.rows_inserted == 0
    assert sum(b.rows_duplicate for b in again.blocks) == before
    assert all(b.created and b.reconcile_status == "reconciled" for b in again.blocks)


@pytest.mark.parametrize("existing_row", ["manual", "outside_prior_window"])
async def test_unexplained_duplicate_needs_review(
    factory: async_sessionmaker[AsyncSession], household: uuid.UUID, existing_row: str
) -> None:
    st = _parse(NFCU_CARD)
    (parsed,) = st.blocks
    assert parsed.reconciliation.status == "reconciled"
    txn = next(t for t in parsed.txns if t.amount == Decimal("80.00"))
    if existing_row == "manual":
        payment = next(
            line for line in NFCU_CARD.splitlines(keepends=True) if "EXAMPLE PAYMENT" in line
        )
        seed = _parse(NFCU_CARD.replace(payment, "").replace("$200.00", "$120.00"))
        assert seed.blocks[0].reconciliation.status == "reconciled"
    else:
        seed = dataclasses.replace(st, period_start=txn.posted_date + timedelta(days=1))
    first = await import_statement(factory, household, seed, _meta(10), household_wide=True)
    (original,) = first.blocks
    if existing_row == "manual":
        async with scoped_session(factory, household, None, household_wide=True) as s:
            await LedgerWriter(s, household).insert_transaction(
                NewTransaction(
                    account_id=original.account_id,
                    posted_date=txn.posted_date,
                    amount=txn.amount,
                    description_raw=txn.description,
                    source="manual",
                    import_fingerprint=txn.fingerprint,
                )
            )

    again = await import_statement(factory, household, st, _meta(11), household_wide=True)
    (block,) = again.blocks
    assert block.created
    assert block.rows_inserted == 0
    assert block.rows_duplicate == len(parsed.txns)
    assert block.reconcile_status == "needs_review"
    assert block.reason == "unexplained_duplicates"

    retry = await import_statement(factory, household, st, _meta(11), household_wide=True)
    (retried,) = retry.blocks
    assert not retried.created
    assert retried.reason == block.reason


async def test_unreconciled_statement_is_flagged_and_latches_the_document(
    factory: async_sessionmaker[AsyncSession], household: uuid.UUID
) -> None:
    broken = NFCU_CARD.replace("$245.00", "$250.00")
    result = await import_statement(
        factory, household, _parse(broken), _meta(5), household_wide=True
    )
    assert result.blocks[0].reconcile_status == "needs_review"
    async with scoped_session(factory, household, None, household_wide=True) as s:
        doc = (await s.execute(select(Document))).scalar_one()
        assert (doc.parse_status, doc.import_review_latched) == ("needs_review", True)
        run = (await s.execute(select(ImportRun))).scalar_one()
        assert run.balance_diff == Decimal("-5.00")


async def test_two_same_day_identical_rows_both_persist() -> None:
    line = next(ln for ln in DEBIT.splitlines(keepends=True) if "Example Advance" in ln)
    text = DEBIT.replace(
        line,
        line.replace("$40.00      $40.00", "$20.00      $20.00")
        + line.replace("$40.00      $40.00", "$20.00      $40.00"),
    )
    block = _parse(text).blocks[0]
    twins = [t for t in block.txns if t.description == "Example Advance"]
    assert [t.ordinal for t in twins] == [1, 2]
    assert twins[0].fingerprint != twins[1].fingerprint


POD_OK_FIRST = DEBIT  # main block reconciles; the pod block we break below
BAD_POD = DEBIT.replace(
    "Ending Balance                                               $1.00",
    "Ending Balance                                               $9.00",
)
BAD_MAIN = DEBIT.replace("$40.00\n        Savings Pod", "$99.00\n        Savings Pod")


async def _doc_state(factory, hid):  # type: ignore[no-untyped-def]
    async with scoped_session(factory, hid, None, household_wide=True) as s:
        d = (await s.execute(select(Document))).scalar_one()
        return d.parse_status, d.import_review_latched


async def test_flagged_later_block_after_reconciled_block_flags_the_document(
    factory: async_sessionmaker[AsyncSession], household: uuid.UUID
) -> None:
    result = await import_statement(
        factory, household, _parse(BAD_POD), _meta(6), household_wide=True
    )
    assert [b.reconcile_status for b in result.blocks] == ["reconciled", "needs_review"]
    assert await _doc_state(factory, household) == ("needs_review", True)


async def test_reconciled_later_block_after_flagged_block_still_imports_and_stays_flagged(
    factory: async_sessionmaker[AsyncSession], household: uuid.UUID
) -> None:
    result = await import_statement(
        factory, household, _parse(BAD_MAIN), _meta(7), household_wide=True
    )
    assert [b.reconcile_status for b in result.blocks] == ["needs_review", "reconciled"]
    assert await _doc_state(factory, household) == ("needs_review", True)


async def test_balances_only_document_is_not_marked_parsed(
    factory: async_sessionmaker[AsyncSession], household: uuid.UUID
) -> None:
    text = (FIXTURES / "current" / "account_summary.sanitized.txt").read_text()
    result = await import_statement(factory, household, _parse(text), _meta(8), household_wide=True)
    assert result.balances_only
    # Never parsed, never silently pending: a person has to look (hard rule 6). No run, no rows.
    assert await _doc_state(factory, household) == ("needs_review", True)
    assert await _count(factory, household, ImportRun) == 0
    assert await _count(factory, household, Transaction) == 0


async def test_concurrent_imports_of_one_account_create_a_single_account(
    factory: async_sessionmaker[AsyncSession], household: uuid.UUID
) -> None:
    import asyncio

    st = _parse(NFCU_CARD)
    await asyncio.gather(
        *(
            import_statement(factory, household, st, _meta(20 + i), household_wide=True)
            for i in range(4)
        ),
        return_exceptions=True,
    )
    assert await _count(factory, household, Account) == 1


async def test_person_import_owns_a_private_account_and_a_stranger_makes_no_twin(
    factory: async_sessionmaker[AsyncSession], household: uuid.UUID, app_engine: Engine
) -> None:
    with app_engine.begin() as c:
        set_ctx(c, household, wide=True)
        alex = c.execute(
            table("person")
            .insert()
            .values(household_id=household, display_name="Alex Example", filing_role="primary")
            .returning(table("person").c.id)
        ).scalar_one()
        sam = c.execute(
            table("person")
            .insert()
            .values(household_id=household, display_name="Sam Example", filing_role="spouse")
            .returning(table("person").c.id)
        ).scalar_one()
    st = _parse(NFCU_CARD)
    await import_statement(factory, household, st, _meta(30), person_id=alex)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        acct = (await s.execute(select(Account))).scalar_one()
        assert (acct.owner_person_id, acct.visibility) == (alex, "private")
        assert (await s.execute(select(Document.person_id))).scalar_one() == alex
    # Sam cannot see Alex's private account: the import is refused, never a second account.
    with pytest.raises(AccountNotVisibleError):
        await import_statement(factory, household, st, _meta(31), person_id=sam)
    assert await _count(factory, household, Account) == 1


async def test_amount_with_more_than_two_decimals_fails_the_import_and_stores_nothing(
    factory: async_sessionmaker[AsyncSession], household: uuid.UUID
) -> None:
    st = _parse(DEBIT)
    block = st.blocks[0]
    bad_txn = dataclasses.replace(block.txns[0], amount=Decimal("-1.005"))
    bad = dataclasses.replace(
        st, blocks=(dataclasses.replace(block, txns=(bad_txn, *block.txns[1:])), *st.blocks[1:])
    )
    with pytest.raises(InvalidAmountError):
        await import_statement(factory, household, bad, _meta(9), household_wide=True)
    for model in (Transaction, ImportRun, Account, Document):
        assert await _count(factory, household, model) == 0


async def test_flagged_run_stores_its_reason_and_a_retry_returns_the_stored_one(
    factory: async_sessionmaker[AsyncSession], household: uuid.UUID
) -> None:
    broken = _parse(NFCU_CARD.replace("$245.00", "$250.00"))
    first = await import_statement(factory, household, broken, _meta(30), household_wide=True)
    assert first.blocks[0].reason == "balance_mismatch"
    async with scoped_session(factory, household, None, household_wide=True) as s:
        run = (await s.execute(select(ImportRun))).scalar_one()
        assert run.review_reason == "balance_mismatch"
    # The parser changed since: the retry parses clean but still reports what was stored.
    retry = await import_statement(
        factory, household, _parse(NFCU_CARD), _meta(30), household_wide=True
    )
    assert not retry.blocks[0].created
    assert retry.blocks[0].reason == "balance_mismatch"


async def test_reconciled_run_has_no_reason_and_legacy_flagged_run_is_never_null(
    factory: async_sessionmaker[AsyncSession], household: uuid.UUID
) -> None:
    ok = await import_statement(
        factory, household, _parse(NFCU_CARD), _meta(31), household_wide=True
    )
    assert ok.blocks[0].reason is None
    async with scoped_session(factory, household, None, household_wide=True) as s:
        run = (await s.execute(select(ImportRun))).scalar_one()
        assert run.review_reason is None
    # A flagged run from before the column existed: no stored reason; never reported as null.
    await import_statement(
        factory,
        household,
        _parse(NFCU_CARD.replace("$245.00", "$250.00")),
        _meta(32),
        household_wide=True,
    )
    async with scoped_session(factory, household, None, household_wide=True) as s:
        legacy = (
            await s.execute(select(ImportRun).where(ImportRun.reconcile_status == "needs_review"))
        ).scalar_one()
        await s.execute(
            update(ImportRun).where(ImportRun.id == legacy.id).values(review_reason=None)
        )
        await s.refresh(legacy)
        assert legacy.review_reason is None
        assert infer_review_reason(legacy) == "balance_mismatch"
    retry = await import_statement(
        factory,
        household,
        _parse(NFCU_CARD),  # reconciles now; the stored run still says it needs review
        _meta(32),
        household_wide=True,
    )
    assert retry.blocks[0].reason  # never null for needs_review


async def test_same_bytes_under_another_profile_write_nothing_and_do_not_flag(
    factory: async_sessionmaker[AsyncSession], household: uuid.UUID
) -> None:
    st = _parse(NFCU_CARD)
    first = await import_statement(factory, household, st, _meta(33), household_wide=True)
    counts = [await _count(factory, household, m) for m in (Transaction, ImportRun, Account)]
    other = dataclasses.replace(st, profile_id="renamed-profile")
    again = await import_statement(factory, household, other, _meta(33), household_wide=True)
    # Not a second account holding the same rows, and not a flagged run: the earlier run stands.
    assert [
        await _count(factory, household, m) for m in (Transaction, ImportRun, Account)
    ] == counts
    (block,) = again.blocks
    assert (block.created, block.run_id, block.reconcile_status, block.rows_inserted) == (
        False,
        first.blocks[0].run_id,
        "reconciled",
        0,
    )
    assert await _doc_state(factory, household) == ("parsed", False)


async def _first_run_fingerprints(
    factory: async_sessionmaker[AsyncSession], household: uuid.UUID
) -> list[str]:
    async with scoped_session(factory, household, None, household_wide=True) as s:
        run = (
            (
                await s.execute(
                    select(ImportRun).where(ImportRun.rows_parsed > 0).order_by(ImportRun.id)
                )
            )
            .scalars()
            .first()
        )
        assert run is not None
        return list(
            (
                await s.execute(
                    select(Transaction.import_fingerprint).where(
                        Transaction.account_id == run.account_id
                    )
                )
            )
            .scalars()
            .all()
        )


async def _unexplained(
    factory: async_sessionmaker[AsyncSession],
    household: uuid.UUID,
    *,
    document_id: uuid.UUID | None,
    shift_start_days: int = 0,
    extra: list[str] | None = None,
    only_first: bool = False,
) -> int:
    async with scoped_session(factory, household, None, household_wide=True) as s:
        run = (
            (
                await s.execute(
                    select(ImportRun).where(ImportRun.rows_parsed > 0).order_by(ImportRun.id)
                )
            )
            .scalars()
            .first()
        )
        assert run is not None
        fps = list(
            (
                await s.execute(
                    select(Transaction.import_fingerprint)
                    .where(Transaction.account_id == run.account_id)
                    .order_by(Transaction.posted_date)
                )
            )
            .scalars()
            .all()
        )
        assert fps
        if only_first:
            fps = fps[:1]
        s.expunge(run)
        if shift_start_days and run.period_start:
            run.period_start = run.period_start + timedelta(days=shift_start_days)
        return await _unexplained_duplicates(
            s,
            run.account_id,
            document_id or run.document_id,
            run,
            fps + (extra or []),  # type: ignore[arg-type]
        )


async def test_unexplained_duplicates_same_document_is_unexplained(
    factory: async_sessionmaker[AsyncSession], household: uuid.UUID
) -> None:
    await import_statement(factory, household, _parse(DEBIT), _meta(40), household_wide=True)
    n = len(await _first_run_fingerprints(factory, household))
    assert n > 0
    # Every row already belongs to the very document being imported.
    assert await _unexplained(factory, household, document_id=None) == n


async def test_unexplained_duplicates_overlap_in_both_windows_is_explained(
    factory: async_sessionmaker[AsyncSession], household: uuid.UUID
) -> None:
    await import_statement(factory, household, _parse(DEBIT), _meta(41), household_wide=True)
    other_doc = uuid.uuid4()
    assert await _unexplained(factory, household, document_id=other_doc) == 0
    # A partial overlap (this statement starts a few days later) still holds the shared rows.
    assert await _unexplained(factory, household, document_id=other_doc, only_first=True) == 0


async def test_unexplained_duplicates_row_outside_the_new_window_is_unexplained(
    factory: async_sessionmaker[AsyncSession], household: uuid.UUID
) -> None:
    await import_statement(factory, household, _parse(DEBIT), _meta(42), household_wide=True)
    other_doc = uuid.uuid4()
    # The new statement starts after the first row posted: that row is not in its window.
    assert (
        await _unexplained(
            factory, household, document_id=other_doc, shift_start_days=400, only_first=True
        )
        == 1
    )


async def test_unexplained_duplicates_skipped_fingerprint_with_no_row_is_unexplained(
    factory: async_sessionmaker[AsyncSession], household: uuid.UUID
) -> None:
    await import_statement(factory, household, _parse(DEBIT), _meta(43), household_wide=True)
    other_doc = uuid.uuid4()
    assert await _unexplained(factory, household, document_id=other_doc, extra=["f" * 64]) == 1
