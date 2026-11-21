"""Idempotent persistence of a parsed statement through the F6 import-run tables (ADR 0017).

``import_statement`` is the one write path for statement imports (ADR 0011). Per block of the
parsed statement it selects or creates the account, starts an ``import_run`` (``running``), inserts
transactions with ``ON CONFLICT (account_id, import_fingerprint) DO NOTHING`` so a re-import
inserts nothing, then closes the run with the reconciliation numbers. The database CHECKs make a
run ``reconciled`` only when opening + sum = closing within one cent; anything else is
``needs_review`` and latches the document (hard rule 6).

Re-importing the same file (same sha256) returns the earlier run and writes nothing. A different
file that overlaps (a re-downloaded statement, an overlapping period) gets its own run whose
duplicate rows are counted in ``rows_duplicate``.

The background import job runs household-wide, like a sync job (the caller passes it).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db_context import scoped_session
from app.domain.statement_parse import ParsedBlock, ParsedStatement, mask_long_digit_runs
from app.models import Account, Document, ImportRun, Transaction
from app.services.ledger_service import LedgerService, NewTransaction

MASK_LEN = 4


@dataclass(frozen=True)
class DocumentMeta:
    """What the caller knows about the stored file. ``storage_path`` is relative to the store."""

    sha256: str
    filename: str
    storage_path: str
    size_bytes: int
    mime: str = "application/pdf"


@dataclass(frozen=True)
class BlockResult:
    run_id: uuid.UUID
    account_id: uuid.UUID
    reconcile_status: str
    rows_parsed: int
    rows_inserted: int
    rows_duplicate: int
    created: bool  # False: this file was already imported; nothing was written
    reason: str | None = None


@dataclass(frozen=True)
class ImportResult:
    document_id: uuid.UUID
    blocks: tuple[BlockResult, ...]
    balances_only: bool = False

    @property
    def rows_inserted(self) -> int:
        return sum(b.rows_inserted for b in self.blocks)


def external_account_key(statement: ParsedStatement, block: ParsedBlock) -> str:
    """Stable id of an imported account: profile, last four digits, block hash. No full number."""
    return f"{statement.profile_id}:{block.account_last4 or 'none'}:{block.key}"


async def _document(
    s: AsyncSession,
    household_id: uuid.UUID,
    meta: DocumentMeta,
    profile_id: str,
    person_id: uuid.UUID | None,
) -> Document:
    doc = (
        await s.execute(
            select(Document).where(
                Document.household_id == household_id, Document.sha256 == meta.sha256
            )
        )
    ).scalar_one_or_none()
    if doc is not None:
        return doc
    doc = Document(
        id=uuid.uuid4(),
        household_id=household_id,
        person_id=person_id,
        kind="statement",
        filename=meta.filename,
        sha256=meta.sha256,
        storage_path=meta.storage_path,
        mime=meta.mime,
        size_bytes=meta.size_bytes,
        detected_profile=profile_id,
    )
    s.add(doc)
    await s.flush()
    return doc


class AccountNotVisibleError(RuntimeError):
    """The imported account exists but this session cannot see it (per-person visibility).

    Never create a twin: the unique key blocks it. Import as a household-wide job, or as the
    account's owner. The message carries no account data.
    """


async def _account(
    s: AsyncSession,
    household_id: uuid.UUID,
    statement: ParsedStatement,
    block: ParsedBlock,
    person_id: uuid.UUID | None,
) -> Account:
    key = external_account_key(statement, block)
    where = (
        Account.household_id == household_id,
        Account.source == "import",
        Account.external_account_id == key,
    )
    acct = (await s.execute(select(Account).where(*where))).scalar_one_or_none()
    if acct is not None:
        return acct
    last4 = block.account_last4
    await s.execute(
        pg_insert(Account)
        .values(
            id=uuid.uuid4(),
            household_id=household_id,
            source="import",
            external_account_id=key,
            name=f"{statement.institution} {last4}" if last4 else statement.institution,
            mask=last4[-MASK_LEN:] if last4 else None,
            type="credit" if statement.kind == "card" else "depository",
            # A person importing owns the account and it starts private (ADR 0008); the
            # household-wide job (no person) creates a joint, shared account.
            owner_person_id=person_id,
            visibility="private" if person_id else "shared",
        )
        .on_conflict_do_nothing(
            index_elements=["household_id", "external_account_id"],
            index_where=text("source = 'import'"),
        )
    )
    # Lost a race (or the row is hidden from this session): read the winner. Still nothing means
    # per-person visibility hides it, and we must not make a twin.
    acct = (await s.execute(select(Account).where(*where))).scalar_one_or_none()
    if acct is None:
        raise AccountNotVisibleError("imported account is not visible to this session")
    return acct


async def import_statement(
    factory: async_sessionmaker[AsyncSession],
    household_id: uuid.UUID,
    statement: ParsedStatement,
    meta: DocumentMeta,
    *,
    person_id: uuid.UUID | None = None,
    household_wide: bool = False,
) -> ImportResult:
    """Persist ``statement``. One database transaction: a failure leaves nothing behind.

    The import job passes ``household_wide`` (it may write any account, like a sync job). A request
    path never does (ADR 0008, ``test_household_wide_guard``): it passes its own ``person_id``.
    """
    async with scoped_session(factory, household_id, person_id, household_wide=household_wide) as s:
        doc = await _document(s, household_id, meta, statement.profile_id, person_id)
        if statement.status == "balances_only":
            # Nothing was reconciled and no transactions exist, so it is never `parsed`. Flag it
            # for a person (hard rule 6): there is no run to carry the flag, so set the document's
            # own status and latch. Storing the balances as snapshots is a follow-up.
            doc.parse_status = "needs_review"
            doc.import_review_latched = True
            await s.flush()
            return ImportResult(doc.id, (), balances_only=True)
        results: list[BlockResult] = []
        for block in statement.blocks:
            results.append(await _import_block(s, household_id, doc, statement, block, person_id))
        # A flagged run latches the document in the database (trigger), so decide once, after
        # every block, from fresh state: never write `parsed` over a latch.
        await s.refresh(doc)
        if all(r.reconcile_status in ("reconciled", "accepted") for r in results) and (
            not doc.import_review_latched
        ):
            doc.parse_status = "parsed"
        return ImportResult(doc.id, tuple(results))


def _window(start: date | None, end: date | None) -> tuple[date, date] | None:
    """The dates a statement can post: its period, plus the parser's 7 day posting grace."""
    if end is None:
        return None
    return (start or end - timedelta(days=62)), end + timedelta(days=7)


async def _unexplained_duplicates(
    s: AsyncSession,
    account_id: uuid.UUID,
    document_id: uuid.UUID,
    run: ImportRun,
    fingerprints: list[str],
) -> int:
    """How many skipped rows are not explained by an overlapping statement of the same account.

    A skipped row is explained when a row from another document on the same account matches its
    fingerprint (date, amount, normalized description, same-day ordinal) inside both statement
    windows: the same real transaction printed on two overlapping statements. A row with no run,
    from this same document, or dated outside either window is a fingerprint collision or a
    misread date: unexplained. Residual risk: two genuinely different purchases that collide on
    all four across overlapping statements cannot be told apart.
    """
    if not fingerprints:
        return 0
    rows = (
        await s.execute(
            select(
                Transaction.posted_date,
                Transaction.import_run_id,
                ImportRun.document_id,
                ImportRun.period_start,
                ImportRun.period_end,
            )
            .outerjoin(ImportRun, ImportRun.id == Transaction.import_run_id)
            .where(
                Transaction.account_id == account_id,
                Transaction.import_fingerprint.in_(fingerprints),
            )
        )
    ).all()
    mine = _window(run.period_start, run.period_end)
    bad = len(fingerprints) - len(rows)  # skipped but not found: cannot explain
    for posted, run_id, doc_id, p_start, p_end in rows:
        theirs = _window(p_start, p_end)
        explained = (
            run_id is not None
            and doc_id != document_id
            and mine is not None
            and theirs is not None
            and mine[0] <= posted <= mine[1]
            and theirs[0] <= posted <= theirs[1]
        )
        bad += 0 if explained else 1
    return bad


async def _import_block(
    s: AsyncSession,
    household_id: uuid.UUID,
    doc: Document,
    statement: ParsedStatement,
    block: ParsedBlock,
    person_id: uuid.UUID | None,
) -> BlockResult:
    acct = await _account(s, household_id, statement, block, person_id)
    prior = (
        (
            await s.execute(
                select(ImportRun).where(
                    ImportRun.document_id == doc.id,
                    ImportRun.account_id == acct.id,
                    ImportRun.status == "succeeded",
                    ImportRun.profile == statement.profile_id,
                )
            )
        )
        .scalars()
        .first()
    )
    if prior is not None:
        reason = None
        if prior.reconcile_status == "needs_review":
            reason = block.reconciliation.reason
            # A successful run with balanced parsed rows was flagged for its skipped rows.
            if block.reconciliation.status == "reconciled" and prior.rows_duplicate:
                reason = "unexplained_duplicates"
        return BlockResult(
            prior.id,
            acct.id,
            prior.reconcile_status,
            prior.rows_parsed,
            0,
            prior.rows_parsed,
            created=False,
            reason=reason,
        )

    run = ImportRun(
        id=uuid.uuid4(),
        household_id=household_id,
        document_id=doc.id,
        account_id=acct.id,
        profile=statement.profile_id,
        status="running",
        period_start=statement.period_start,
        period_end=statement.period_end,
        started_at=datetime.now(UTC),
    )
    s.add(run)
    await s.flush()

    ledger = LedgerService(s, household_id)
    inserted = 0
    skipped: list[str] = []
    for t in block.txns:
        new = NewTransaction(
            account_id=acct.id,
            posted_date=t.posted_date,
            amount=t.amount,
            description_raw=mask_long_digit_runs(t.description),
            source="import",
            import_fingerprint=t.fingerprint,
            import_run_id=run.id,
        )
        if await ledger.insert_transaction(new, skip_duplicates=True) is not None:
            inserted += 1
        else:
            skipped.append(t.fingerprint)

    rec = block.reconciliation
    run.opening_balance = block.opening_balance
    run.closing_balance = block.closing_balance
    run.txn_sum = block.txn_sum
    run.rows_parsed = len(block.txns)
    run.rows_inserted = inserted
    run.rows_duplicate = len(block.txns) - inserted
    # A skipped row must be a re-import or an overlapping period. Otherwise the balances tie out
    # on the parsed sum while the ledger holds fewer rows: never call that reconciled.
    unexplained = await _unexplained_duplicates(s, acct.id, doc.id, run, skipped)
    run.reconcile_status = (
        "reconciled" if rec.status == "reconciled" and not unexplained else "needs_review"
    )
    run.status = "succeeded"
    run.finished_at = datetime.now(UTC)
    await s.flush()
    return BlockResult(
        run.id,
        acct.id,
        run.reconcile_status,
        len(block.txns),
        inserted,
        run.rows_duplicate,
        created=True,
        reason=rec.reason or ("unexplained_duplicates" if unexplained else None),
    )
