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
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import groupby
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db_context import scoped_session
from app.domain.statement_parse import ParsedBlock, ParsedStatement, mask_long_digit_runs
from app.models import Account, Document, ImportRun, Transaction
from app.services.ledger_service import LedgerWriter, NewTransaction

MASK_LEN = 4
FINGERPRINT_CHUNK = 10_000
OVERLAP_WINDOW_DAYS = 3  # the ADR 0035 window for matching rows of two sources


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
    # Rows that may already be in this account from another file (``possible_overlap``).
    rows_overlap: int = 0


@dataclass(frozen=True)
class ImportResult:
    document_id: uuid.UUID
    blocks: tuple[BlockResult, ...]
    balances_only: bool = False
    # The same bytes were imported earlier into a different account; nothing was written.
    in_other_account: bool = False

    @property
    def rows_inserted(self) -> int:
        return sum(b.rows_inserted for b in self.blocks)


UNKNOWN_REASON = "unknown"
TOLERANCE = Decimal("0.01")


def infer_review_reason(run: ImportRun) -> str:
    """Best guess at why a run created before ``review_reason`` existed was flagged.

    Only from the stored figures (never by re-parsing): a missing balance, a diff over a cent, or
    skipped rows. A date problem leaves no trace in the figures, so it reads as ``unknown``.
    """
    if run.opening_balance is None or run.closing_balance is None:
        return "missing_balance"
    if run.balance_diff is not None and abs(run.balance_diff) > TOLERANCE:
        return "balance_mismatch"
    if run.rows_duplicate:
        return "unexplained_duplicates"
    return UNKNOWN_REASON


def stored_review_reason(run: ImportRun) -> str | None:
    """The reason to show for ``run``: only while it needs review (the column keeps the history
    after an accept). Stored, else inferred for a flagged legacy run."""
    if run.reconcile_status != "needs_review":
        return None
    return run.review_reason or infer_review_reason(run)


@dataclass(frozen=True)
class AccountTarget:
    """Where a CSV or OFX goes (a PDF passes none: its account follows its profile).

    ``account_id`` names an existing import account the uploader may write; otherwise a new
    account is made and is the uploader's own (private), or joint only if ``joint`` is set.
    ``new_token`` separates new accounts of a source that carries no account identity (a CSV).
    ``kind_from_file`` means the file states bank vs card (OFX), so an existing account of the
    other type is refused.
    """

    account_id: uuid.UUID | None = None
    joint: bool = False
    new_token: str | None = None
    kind_from_file: bool = False
    declared_kind: str | None = None  # the user's bank/card choice for a CSV


class AccountTargetError(RuntimeError):
    """The requested target can't be used. ``reason`` is a fixed code, never content."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def external_account_key(
    statement: ParsedStatement,
    block: ParsedBlock,
    *,
    person_id: uuid.UUID | None = None,
    target: AccountTarget | None = None,
) -> str:
    """Stable id of an imported account: profile, last four digits, block key. No full number.

    A CSV/OFX account (a ``target`` is given) also carries its owner, so two people never share
    one, and a CSV its own token, since it has no account identity.
    """
    key = f"{statement.profile_id}:{block.account_last4 or 'none'}:{block.key}"
    if target is None:
        return key
    owner = "joint" if target.joint or person_id is None else str(person_id)
    return f"{key}:{owner}" + (f":{target.new_token}" if target.new_token else "")


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
    target: AccountTarget | None = None,
    household_wide: bool = False,
) -> Account:
    if target is not None and target.account_id is not None:
        chosen = (
            await s.execute(
                select(Account).where(
                    Account.id == target.account_id,
                    Account.household_id == household_id,
                    Account.source == "import",
                )
            )
        ).scalar_one_or_none()
        owned_elsewhere = (
            chosen is not None
            and chosen.owner_person_id is not None
            and chosen.owner_person_id != person_id
            and not household_wide
        )
        if chosen is None or owned_elsewhere:
            # One answer for "no such account" and "someone else's": no existence oracle.
            raise AccountNotVisibleError("imported account is not visible to this session")
        wanted = statement.kind if target.kind_from_file else target.declared_kind
        if wanted is not None and (chosen.type == "credit") != (wanted == "card"):
            raise AccountTargetError("account_kind_mismatch")
        return chosen
    if target is not None and person_id is None and not target.joint and not household_wide:
        raise AccountTargetError("joint_not_chosen")
    joint = target is not None and (target.joint or person_id is None)
    key = external_account_key(statement, block, person_id=person_id, target=target)
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
            owner_person_id=None if joint else person_id,
            visibility="private" if person_id and not joint else "shared",
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
    target: AccountTarget | None = None,
) -> ImportResult:
    """Persist ``statement``. One database transaction: a failure leaves nothing behind.

    The import job passes ``household_wide`` (it may write any account, like a sync job). A request
    path never does (ADR 0008, ``test_household_wide_guard``): it passes its own ``person_id``.
    """
    async with scoped_session(factory, household_id, person_id, household_wide=household_wide) as s:
        doc = await _document(s, household_id, meta, statement.profile_id, person_id)
        # A CSV/OFX has a chosen account, so the same bytes sent to another account must not be
        # imported twice: any earlier run of the document wins.
        elsewhere = await _runs_under_other_profile(
            s, doc.id, None if target is not None else statement.profile_id
        )
        if elsewhere:
            # The same bytes were already imported under another profile. Their accounts are keyed
            # by profile, so importing again would copy every row into a second account and
            # double-count it. Report the earlier runs and write nothing.
            return ImportResult(
                doc.id,
                tuple(elsewhere),
                in_other_account=await _in_other_account(
                    s, household_id, elsewhere, statement, person_id, target
                ),
            )
        if statement.status == "balances_only":
            # Nothing was reconciled and no transactions exist, so it is never `parsed`. Flag it
            # for a person (hard rule 6): there is no run to carry the flag, so set the document's
            # own status and latch. Storing the balances as snapshots is a follow-up.
            doc.parse_status = "needs_review"
            doc.import_review_latched = True
            await s.flush()
            return ImportResult(doc.id, (), balances_only=True)
        if target is not None and target.account_id is not None and len(statement.blocks) != 1:
            raise AccountTargetError("target_needs_single_account")
        results: list[BlockResult] = []
        for block in statement.blocks:
            results.append(
                await _import_block(
                    s, household_id, doc, statement, block, person_id, target, household_wide
                )
            )
        # A flagged run latches the document in the database (trigger), so decide once, after
        # every block, from fresh state: never write `parsed` over a latch.
        await s.refresh(doc)
        if all(r.reconcile_status in ("reconciled", "accepted") for r in results) and (
            not doc.import_review_latched
        ):
            doc.parse_status = "parsed"
        return ImportResult(doc.id, tuple(results))


async def _in_other_account(
    s: AsyncSession,
    household_id: uuid.UUID,
    earlier: list[BlockResult],
    statement: ParsedStatement,
    person_id: uuid.UUID | None,
    target: AccountTarget | None,
) -> bool:
    """True if a CSV/OFX sent to a chosen account was imported before into another one."""
    if target is None:
        return False
    if target.account_id is not None:
        return any(r.account_id != target.account_id for r in earlier)
    wanted = {
        external_account_key(statement, b, person_id=person_id, target=target)
        for b in statement.blocks
    }
    keys = (
        (
            await s.execute(
                select(Account.external_account_id).where(
                    Account.household_id == household_id,
                    Account.id.in_([r.account_id for r in earlier]),
                )
            )
        )
        .scalars()
        .all()
    )
    return any(k not in wanted for k in keys)


async def _runs_under_other_profile(
    s: AsyncSession, document_id: uuid.UUID, profile_id: str | None
) -> list[BlockResult]:
    """The document's finished runs when none were made under ``profile_id``; else empty."""
    runs = (
        (
            await s.execute(
                select(ImportRun)
                .where(
                    ImportRun.document_id == document_id,
                    ImportRun.status == "succeeded",
                    ImportRun.account_id.is_not(None),
                )
                .order_by(ImportRun.created_at, ImportRun.id)
            )
        )
        .scalars()
        .all()
    )
    if not runs or (profile_id is not None and any(r.profile == profile_id for r in runs)):
        return []
    return [
        BlockResult(
            r.id,
            r.account_id,  # type: ignore[arg-type]  # filtered NOT NULL above
            r.reconcile_status,
            r.rows_parsed,
            0,
            r.rows_parsed,
            created=False,
            reason=stored_review_reason(r) if r.reconcile_status == "needs_review" else None,
        )
        for r in runs
    ]


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
    rows: list[Any] = []
    # asyncpg allows 32767 bind parameters per statement: look up in chunks.
    for i in range(0, len(fingerprints), FINGERPRINT_CHUNK):
        rows.extend(
            (
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
                        Transaction.import_fingerprint.in_(fingerprints[i : i + FINGERPRINT_CHUNK]),
                    )
                )
            ).all()
        )
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


async def _possible_overlap(
    s: AsyncSession,
    account_id: uuid.UUID,
    document_id: uuid.UUID,
    rows: list[tuple[date, Decimal]],
) -> int:
    """How many just-inserted rows have a live row of another document in the account with the
    same amount within ``OVERLAP_WINDOW_DAYS``.

    The fingerprint also compares the description, which two sources word differently, so a bank
    CSV row and the PDF row for the same purchase both get stored. Matching is order preserving
    per amount (each existing row pairs at most once), so equal same-day amounts count right.
    Nothing is removed: a person looks at the rows.
    """
    if not rows:
        return 0
    window = timedelta(days=OVERLAP_WINDOW_DAYS)
    existing = (
        await s.execute(
            select(Transaction.posted_date, Transaction.amount)
            .join(ImportRun, ImportRun.id == Transaction.import_run_id)
            .where(
                Transaction.account_id == account_id,
                ImportRun.document_id != document_id,
                Transaction.superseded_by_id.is_(None),
                Transaction.posted_date >= min(d for d, _ in rows) - window,
                Transaction.posted_date <= max(d for d, _ in rows) + window,
            )
        )
    ).all()

    def grouped(pairs_: Iterable[tuple[date, Decimal]]) -> dict[Decimal, list[date]]:
        ordered = sorted(pairs_, key=lambda p: (p[1], p[0]))
        return {a: [d for d, _ in grp] for a, grp in groupby(ordered, key=lambda p: p[1])}

    by_amount = grouped((posted, amount) for posted, amount in existing)
    mine = grouped(rows)
    pairs = 0
    for amount, dates in mine.items():
        theirs = by_amount.get(amount, [])
        j = 0
        for ours in dates:
            while j < len(theirs) and theirs[j] < ours - window:
                j += 1
            if j < len(theirs) and theirs[j] <= ours + window:
                pairs += 1
                j += 1
    return pairs


async def _import_block(
    s: AsyncSession,
    household_id: uuid.UUID,
    doc: Document,
    statement: ParsedStatement,
    block: ParsedBlock,
    person_id: uuid.UUID | None,
    target: AccountTarget | None = None,
    household_wide: bool = False,
) -> BlockResult:
    acct = await _account(s, household_id, statement, block, person_id, target, household_wide)
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
            if prior.review_reason:
                reason = prior.review_reason
            else:  # flagged before the reason was stored: best guess from this parse
                reason = block.reconciliation.reason
                if block.reconciliation.status == "reconciled" and prior.rows_duplicate:
                    reason = "unexplained_duplicates"
                reason = reason or infer_review_reason(prior)
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

    ledger = LedgerWriter(s, household_id)
    inserted = 0
    skipped: list[str] = []
    added: list[tuple[date, Decimal]] = []
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
            added.append((t.posted_date, t.amount))
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
    reason = rec.reason if rec.status != "reconciled" else None
    if rec.status == "reconciled" and unexplained:
        reason = "unexplained_duplicates"
    overlap = 0
    if target is not None and target.account_id is not None:
        overlap = await _possible_overlap(s, acct.id, doc.id, added)
        if overlap:
            reason = f"{reason},possible_overlap" if reason else "possible_overlap"
    run.reconcile_status = "needs_review" if reason or rec.status != "reconciled" else "reconciled"
    flagged = run.reconcile_status == "needs_review"
    run.review_reason = (reason or UNKNOWN_REASON) if flagged else None
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
        reason=run.review_reason,
        rows_overlap=overlap,
    )
