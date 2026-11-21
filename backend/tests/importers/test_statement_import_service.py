"""Statement import through the F6 import-run tables: idempotent, reconciled or flagged."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db_context import scoped_session
from app.domain.statement_parse import ParsedStatement, detect_profile, parse_statement
from app.importers.profile_loader import load_profiles
from app.models import Account, Document, ImportRun, Transaction
from app.services.statement_import import DocumentMeta, import_statement
from tests.importers.test_statement_profiles import DEBIT, NFCU_CARD

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
        assert amounts == [Decimal("-90.00"), Decimal("-5.00"), Decimal("60.00")]
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


async def test_unreconciled_statement_is_flagged_and_latches_the_document(
    factory: async_sessionmaker[AsyncSession], household: uuid.UUID
) -> None:
    broken = NFCU_CARD.replace("$135.00", "$140.00")
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
    text = DEBIT.replace(
        "        10/31/25 Paycheck Advance                                  $50.00      $50.00\n",
        "        10/31/25 Paycheck Advance                                  $25.00      $25.00\n"
        "        10/31/25 Paycheck Advance                                  $25.00      $50.00\n",
    )
    block = _parse(text).blocks[0]
    twins = [t for t in block.txns if t.description == "Paycheck Advance"]
    assert [t.ordinal for t in twins] == [1, 2]
    assert twins[0].fingerprint != twins[1].fingerprint
