"""Statement import through the F6 import-run tables: idempotent, reconciled or flagged."""

from __future__ import annotations

import dataclasses
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db_context import scoped_session
from app.domain.ledger_rules import InvalidAmountError
from app.domain.statement_parse import ParsedStatement, detect_profile, parse_statement
from app.importers.profile_loader import load_profiles
from app.models import Account, Document, ImportRun, Transaction
from app.services.statement_import import (
    AccountNotVisibleError,
    DocumentMeta,
    import_statement,
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
