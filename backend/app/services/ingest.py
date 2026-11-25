"""IngestService: one connector batch into the ledger (PHASE-2 3.4, ADR 0033, ADR 0035).

``apply`` runs in the caller's session and unit of work and never commits. The session must be
household-wide (a job) or the service must be given a named ``user_id`` (a request bound to the
authenticated user); the ledger verbs refuse an anonymous person session.

Process removals before modified and added rows, except when a posted row in the
same batch names the removed pending ID. Defer that removal until after the upserts
so the pending-to-posted transition can preserve the existing row's metadata.

The matcher's coverage edge (ADR 0035 1 and 6). A statement row on the last covered day (or the
first) can have its connector twin dated 1 to 3 days outside coverage, because the statement
prints the transaction date and the connector's posting date lags. The pure matcher drops covered
imports, so on its own that pair would stay as a live duplicate. Ingest therefore hands the
matcher the covered import rows within ``OVERLAP_WINDOW_DAYS`` of an interval edge, as
candidates only when a connector twin has the exact amount and authorization date.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db_context import is_household_wide
from app.domain.categories import pfc_category_slug
from app.domain.connector import ConnectorAccount, ConnectorTxn, SyncBatch
from app.domain.ledger_rules import LedgerRuleError
from app.domain.statement_parse import mask_long_digit_runs
from app.domain.supersede import (
    OVERLAP_WINDOW_DAYS,
    ConnectorCandidate,
    CoverageInterval,
    ImportCandidate,
    is_covered,
    match,
)
from app.models import (
    Account,
    AccountCoverage,
    BalanceSnapshot,
    BankConnection,
    Category,
    ConnectorAccountLink,
    ImportRun,
    SyncRun,
    Transaction,
    TransactionSplit,
)
from app.security.client_ip import IPAddress
from app.services.ledger_service import (
    AutoCategorySource,
    LedgerWriter,
    NewTransaction,
    TransactionNotFoundError,
    UpsertAction,
)


@dataclass
class IngestCounts:
    """What one ``apply`` did. ``writes`` is zero when a batch is replayed."""

    added: int = 0
    modified: int = 0
    unchanged: int = 0
    removed: int = 0
    pending_posted: int = 0
    skipped_covered: int = 0
    skipped_unmapped: int = 0
    skipped_currency: int = 0
    accounts_pending: int = 0
    balances_written: int = 0
    balances_skipped_manual: int = 0
    superseded: int = 0
    held: int = 0
    held_pending_user_metadata: int = 0
    supersede_conflicts: int = 0
    matching_deferred: bool = False
    ledger: dict[str, int] = field(default_factory=dict)  # LedgerWriter.counts for the unit

    @property
    def writes(self) -> int:
        return (
            self.added
            + self.modified
            + self.removed
            + self.pending_posted
            + self.balances_written
            + self.superseded
            + self.accounts_pending
        )


class IngestService:
    def __init__(
        self,
        session: AsyncSession,
        household_id: uuid.UUID,
        *,
        user_id: uuid.UUID | None = None,
        ip: IPAddress | None = None,
    ) -> None:
        self._s = session
        self._household_id = household_id
        self._user_id = user_id
        self._writer = LedgerWriter(session, household_id, user_id=user_id, ip=ip)

    async def apply(
        self, connection_id: uuid.UUID, batch: SyncBatch, sync_run_id: uuid.UUID | None = None
    ) -> IngestCounts:
        if self._user_id is None and not await is_household_wide(self._s):
            raise LedgerRuleError("ingest needs a household-wide session or a named user")
        counts = IngestCounts()
        conn = (
            await self._s.execute(select(BankConnection).where(BankConnection.id == connection_id))
        ).scalar_one_or_none()
        if conn is None:
            raise LedgerRuleError("connection not found")

        await self._s.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"holdfast:ingest:{connection_id}"},
        )
        mapped, currencies = await self._resolve_accounts(conn, batch, counts)
        await self._balances(connection_id, batch, mapped, counts)
        coverage = await self._coverage(set(mapped.values()))
        slugs = await self._category_ids()
        transactions = (*batch.modified, *batch.added)
        posting_pending_ids = {
            (txn.external_account_id, txn.pending_external_id)
            for txn in transactions
            if txn.pending_external_id and not txn.is_pending
        }
        deferred_removals: list[tuple[str, str]] = []
        for removal in batch.removed:
            if removal in posting_pending_ids:
                deferred_removals.append(removal)
            else:
                await self._remove_txn(removal, mapped, counts)
        held_pending: set[tuple[str, str]] = set()
        for txn in transactions:
            await self._apply_txn(
                txn, mapped, currencies, coverage, slugs, conn.provider, counts, held_pending
            )
        for removal in deferred_removals:
            if removal not in held_pending:
                await self._remove_txn(removal, mapped, counts)
        counts.held_pending_user_metadata = len(held_pending)

        if batch.history_complete:
            await self.match_accounts(set(mapped.values()), counts, coverage)
        else:
            counts.matching_deferred = True  # ADR 0035 4: Plaid fills history newest first

        counts.ledger = dict(self._writer.counts)
        if sync_run_id is not None:
            await self._record(sync_run_id, counts)
        return counts

    # ---------------------------------------------------------------- accounts

    async def _resolve_accounts(
        self, conn: BankConnection, batch: SyncBatch, counts: IngestCounts
    ) -> tuple[dict[str, uuid.UUID], dict[uuid.UUID, str]]:
        connection_id = conn.id
        links = {
            link.external_account_id: link
            for link in (
                await self._s.execute(
                    select(ConnectorAccountLink).where(
                        ConnectorAccountLink.bank_connection_id == connection_id
                    )
                )
            ).scalars()
        }
        reported: dict[str, ConnectorAccount | None] = {
            a.external_account_id: a for a in batch.accounts
        }
        for ext in (
            *(t.external_account_id for t in batch.added),
            *(t.external_account_id for t in batch.modified),
            *(b.external_account_id for b in batch.balances),
            *(ext for ext, _ in batch.removed),
        ):
            reported.setdefault(ext, None)
        account_ids = {link.account_id for link in links.values() if link.account_id is not None}
        currencies = dict(
            (
                await self._s.execute(
                    select(Account.id, Account.currency).where(
                        Account.id.in_(account_ids),
                        Account.bank_connection_id == connection_id,
                        Account.source == conn.provider,
                    )
                )
            )
            .tuples()
            .all()
        )
        mapped: dict[str, uuid.UUID] = {}
        for ext, acct in reported.items():
            if acct is not None and acct.currency is None:
                counts.skipped_currency += 1  # never assume USD
                continue
            link = links.get(ext)
            if link is None:
                await self._s.execute(
                    pg_insert(ConnectorAccountLink)
                    .values(
                        id=uuid.uuid4(),
                        household_id=self._household_id,
                        bank_connection_id=connection_id,
                        external_account_id=ext,
                        state="pending",
                    )
                    .on_conflict_do_nothing()
                )
                counts.accounts_pending += 1
                continue
            if link.state != "mapped" or link.account_id is None:
                continue  # ignored, or still waiting for the owner
            currency = currencies.get(link.account_id)
            if currency is None:
                continue  # not writable by this connection; rows count as unmapped
            if acct is not None and currency != acct.currency:
                counts.skipped_currency += 1
                continue
            mapped[ext] = link.account_id
        return mapped, currencies

    # ---------------------------------------------------------------- balances

    async def _balances(
        self,
        connection_id: uuid.UUID,
        batch: SyncBatch,
        mapped: dict[str, uuid.UUID],
        counts: IngestCounts,
    ) -> None:
        for bal in batch.balances:
            account_id = mapped.get(bal.external_account_id)
            if account_id is None:
                continue
            values = {
                "current": bal.current,
                "available": bal.available,
                "limit_amt": bal.limit,
                "bank_connection_id": connection_id,
            }
            stmt = pg_insert(BalanceSnapshot).values(
                account_id=account_id,
                as_of_date=bal.as_of,
                household_id=self._household_id,
                **values,
            )
            # One row per (account, day), never summed. A manual snapshot (no connection) is kept.
            stmt = stmt.on_conflict_do_update(
                index_elements=["account_id", "as_of_date"],
                set_={k: stmt.excluded[k] for k in values},
                where=(
                    BalanceSnapshot.bank_connection_id.is_not(None)
                    & (
                        BalanceSnapshot.current.is_distinct_from(stmt.excluded.current)
                        | BalanceSnapshot.available.is_distinct_from(stmt.excluded.available)
                        | BalanceSnapshot.limit_amt.is_distinct_from(stmt.excluded.limit_amt)
                        | BalanceSnapshot.bank_connection_id.is_distinct_from(
                            stmt.excluded.bank_connection_id
                        )
                    )
                ),
            )
            result = await self._s.execute(stmt.returning(BalanceSnapshot.account_id))
            if result.first() is not None:
                counts.balances_written += 1
            else:
                manual = (
                    await self._s.execute(
                        select(BalanceSnapshot.bank_connection_id).where(
                            BalanceSnapshot.account_id == account_id,
                            BalanceSnapshot.as_of_date == bal.as_of,
                        )
                    )
                ).scalar_one_or_none()
                if manual is None:
                    counts.balances_skipped_manual += 1

    # ------------------------------------------------------------ transactions

    async def _coverage(
        self, account_ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, list[CoverageInterval]]:
        out: dict[uuid.UUID, list[CoverageInterval]] = defaultdict(list)
        if not account_ids:
            return out
        rows = await self._s.execute(
            select(AccountCoverage.account_id, AccountCoverage.start_date, AccountCoverage.end_date)
            .where(AccountCoverage.account_id.in_(account_ids))
            .order_by(AccountCoverage.start_date, AccountCoverage.id)
        )
        for account_id, start, end in rows:
            out[account_id].append(CoverageInterval(start, end))
        return out

    async def _category_ids(self) -> dict[str, uuid.UUID]:
        rows = await self._s.execute(select(Category.slug, Category.id))
        return {slug: cid for slug, cid in rows}

    async def _remove_txn(
        self,
        removal: tuple[str, str],
        mapped: dict[str, uuid.UUID],
        counts: IngestCounts,
    ) -> None:
        external_account_id, external_id = removal
        account_id = mapped.get(external_account_id)
        if account_id is not None and await self._writer.remove_external(account_id, external_id):
            counts.removed += 1

    async def _apply_txn(
        self,
        txn: ConnectorTxn,
        mapped: dict[str, uuid.UUID],
        currencies: dict[uuid.UUID, str],
        coverage: dict[uuid.UUID, list[CoverageInterval]],
        slugs: dict[str, uuid.UUID],
        provider: str,
        counts: IngestCounts,
        held_pending: set[tuple[str, str]],
    ) -> None:
        account_id = mapped.get(txn.external_account_id)
        if account_id is None:
            counts.skipped_unmapped += 1
            return
        if txn.currency is not None and txn.currency != currencies[account_id]:
            counts.skipped_currency += 1
            return
        if is_covered(txn.posted_date, coverage.get(account_id, ()), is_pending=txn.is_pending):
            counts.skipped_covered += 1
            # The statement owns this day, so the pending row it replaces has no posted twin.
            if txn.pending_external_id:
                removal = (txn.external_account_id, txn.pending_external_id)
                if await self._pending_has_user_metadata(account_id, txn.pending_external_id):
                    held_pending.add(removal)
                else:
                    await self._remove_txn(removal, mapped, counts)
            return
        slug = pfc_category_slug(txn.pfc_primary, txn.pfc_detailed)
        category_id = slugs.get(slug) if slug else None
        # SEAM (C3e): inline rules run here and set category_source RULE before the write.
        new = NewTransaction(
            account_id=account_id,
            posted_date=txn.posted_date,
            amount=txn.amount,  # already signed: outflow negative
            description_raw=mask_long_digit_runs(txn.description_raw),
            source=provider,
            external_id=txn.external_id,
            authorized_date=txn.authorized_date,
            merchant_name=txn.merchant_name,
            plaid_pfc_primary=txn.pfc_primary,
            plaid_pfc_detailed=txn.pfc_detailed,
            category_id=category_id,
            category_source=AutoCategorySource.PLAID if category_id else None,
            is_pending=txn.is_pending,
            pending_external_id=txn.pending_external_id,
        )
        if txn.pending_external_id and not txn.is_pending:
            existing = await self._ids(account_id, txn.external_id, txn.pending_external_id)
            if existing["pending"] and not existing["posted"]:
                try:
                    async with self._s.begin_nested():
                        await self._writer.post_pending(txn.pending_external_id, new)
                    counts.pending_posted += 1
                    return
                except TransactionNotFoundError:
                    pass  # raced away: the posted row simply arrives without a pending twin
        outcome = await self._writer.upsert_external(new)
        if outcome.action is UpsertAction.INSERTED:
            counts.added += 1
        elif outcome.action is UpsertAction.UPDATED:
            counts.modified += 1
        else:
            counts.unchanged += 1

    async def _pending_has_user_metadata(self, account_id: uuid.UUID, external_id: str) -> bool:
        row = await self._s.scalar(
            select(Transaction)
            .where(
                Transaction.account_id == account_id,
                Transaction.external_id == external_id,
                Transaction.is_pending.is_(True),
            )
            .with_for_update()
        )
        if row is None:
            return False
        if (
            row.notes is not None
            or row.category_source == "user"
            or row.is_tax_relevant
            or row.tax_tag is not None
            or row.receipt_document_id is not None
        ):
            return True
        return bool(
            await self._s.scalar(
                select(
                    select(TransactionSplit.id)
                    .where(TransactionSplit.transaction_id == row.id)
                    .exists()
                )
            )
        )

    async def _ids(
        self, account_id: uuid.UUID, posted_ext: str, pending_ext: str
    ) -> dict[str, bool]:
        found = set(
            (
                await self._s.execute(
                    select(Transaction.external_id).where(
                        Transaction.account_id == account_id,
                        Transaction.external_id.in_((posted_ext, pending_ext)),
                    )
                )
            ).scalars()
        )
        return {"posted": posted_ext in found, "pending": pending_ext in found}

    # ---------------------------------------------------------------- matching

    async def match_accounts(
        self,
        account_ids: set[uuid.UUID],
        counts: IngestCounts | None = None,
        coverage: dict[uuid.UUID, list[CoverageInterval]] | None = None,
    ) -> IngestCounts:
        """Run the ADR 0035 matcher over whole accounts and supersede the non-held pairs.

        Blocks that need review are excluded until accepted.
        """
        counts = counts if counts is not None else IngestCounts()
        coverage = coverage if coverage is not None else await self._coverage(account_ids)
        for account_id in sorted(account_ids):
            imports, connectors = await self._candidates(account_id, coverage.get(account_id, []))
            for pair in match(imports, connectors, coverage_by_account=coverage):
                if pair.held:
                    counts.held += 1
                    continue
                try:
                    async with self._s.begin_nested():
                        await self._writer.supersede_transaction(pair.import_id, pair.connector_id)
                except LedgerRuleError:
                    # SupersedeConflict or a lost race: both rows stay live. The writer counted it.
                    counts.supersede_conflicts += 1
                else:
                    counts.superseded += 1
        counts.ledger = dict(self._writer.counts)
        return counts

    async def _candidates(
        self, account_id: uuid.UUID, intervals: Sequence[CoverageInterval]
    ) -> tuple[list[ImportCandidate], list[ConnectorCandidate]]:
        env_row = (
            await self._s.execute(
                select(BankConnection.env, Account.adopted_at)
                .join(Account, Account.bank_connection_id == BankConnection.id)
                .where(Account.id == account_id)
            )
        ).first()
        if env_row is None:
            return [], []
        env, adopted_at = env_row
        rows = (
            await self._s.execute(
                select(Transaction, ImportRun.reconcile_status)
                .outerjoin(ImportRun, Transaction.import_run_id == ImportRun.id)
                .where(Transaction.account_id == account_id)
                .order_by(Transaction.posted_date, Transaction.id)
            )
        ).all()
        winners = {r.superseded_by_id for r, _ in rows if r.superseded_by_id is not None}
        edge = timedelta(days=OVERLAP_WINDOW_DAYS)
        imports: list[ImportCandidate] = []
        connectors: list[ConnectorCandidate] = []
        for r, reconcile_status in rows:
            in_supersession = r.superseded_by_id is not None or r.id in winners
            if r.source == "import" and r.import_fingerprint is not None:
                if reconcile_status == "needs_review":
                    continue
                after = adopted_at is not None and r.created_at >= adopted_at
                # Coverage edge: a covered statement row near an interval boundary can have its
                # connector twin just outside it. Offer it to the matcher (module docstring).
                near_edge = any(
                    i.start_date <= r.posted_date <= i.end_date
                    and (i.end_date - r.posted_date <= edge or r.posted_date - i.start_date <= edge)
                    for i in intervals
                )
                imports.append(
                    ImportCandidate(
                        id=r.id,
                        account_id=r.account_id,
                        amount=r.amount,
                        posted_date=r.posted_date,
                        import_fingerprint=r.import_fingerprint,
                        description_raw=r.description_raw,
                        is_pending=r.is_pending,
                        in_supersession=in_supersession,
                        arrived_after_adoption=after,
                        coverage_edge=near_edge,
                    )
                )
            elif r.source in ("plaid", "simplefin") and r.external_id is not None:
                connectors.append(
                    ConnectorCandidate(
                        id=r.id,
                        account_id=r.account_id,
                        amount=r.amount,
                        posted_date=r.posted_date,
                        external_id=r.external_id,
                        description_raw=r.description_raw,
                        source=r.source,
                        connection_env=env,
                        authorized_date=r.authorized_date,
                        is_pending=r.is_pending,
                        in_supersession=in_supersession,
                    )
                )
        return imports, connectors

    # ------------------------------------------------------------------ record

    async def _record(self, sync_run_id: uuid.UUID, counts: IngestCounts) -> None:
        await self._s.execute(
            update(SyncRun)
            .where(SyncRun.id == sync_run_id)
            .values(
                rows_added=SyncRun.rows_added + counts.added + counts.pending_posted,
                rows_modified=SyncRun.rows_modified + counts.modified,
                rows_removed=SyncRun.rows_removed + counts.removed,
                rows_superseded=SyncRun.rows_superseded + counts.superseded,
                rows_skipped_covered=SyncRun.rows_skipped_covered + counts.skipped_covered,
                rows_held=SyncRun.rows_held + counts.held + counts.held_pending_user_metadata,
            )
        )
