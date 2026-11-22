"""The one writer of ledger money fields (ADR 0011, PLAN 20 money review).

``LedgerWriter`` is the only code allowed to create a ``Transaction`` or ``TransactionSplit``, or
to set a transaction's ``amount``, ``category_id``/``category_source``, ``is_transfer`` or
``transfer_group_id``. ``tests/test_ledger_single_writer.py`` is a lint over ``app/`` that fails
the build if any other module does; the real guarantee is RLS plus the database triggers. Reads
live in ``app.services.ledger``.

It works on a session the caller opened with ``scoped_session`` (RLS context, one unit of work,
committed by the caller). Pure checks live in ``app.domain.ledger_rules``; this module loads rows,
runs them, persists, and audits.

**Who is acting.** ``household_id`` is taken from the session's RLS context and the argument must
match it. User edits (``set_category``, ``set_amount``, transfers, splits) need ``user_id`` and are
always audited. Automated categorisation goes through ``apply_category`` with a closed
``AutoCategorySource``: precedence is user > rule > plaid > ml, a lower source returns ``False``
and changes nothing, and it is never audited row by row, nor are ingest inserts: the import or
sync run is the record. A request can never pick the audit path: ``"user"`` is not an auto source.

**Failure semantics.** Every check runs before a row is touched. A database failure leaves the
caller's transaction failed: roll back (``scoped_session`` does on exit). Database errors are
translated and raised outside the ``except`` block, so no SQL parameters (descriptions, amounts)
ride on ``__cause__`` or ``__context__``. A row the caller cannot see or write behaves as missing
(``LedgerNotFoundError``). Deadlocks and serialization failures become ``LedgerRetryableError``.
Paired legs are always locked together, ordered by id.
"""

from __future__ import annotations

import enum
import uuid
from collections import Counter
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import TypeVar

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.sql.elements import ColumnElement

from app.db_context import is_household_wide
from app.domain.category_precedence import can_replace_category
from app.domain.ledger_rules import (
    LedgerRuleError,
    SupersedeConflict,
    TransferLeg,
    TransferPairError,
    first_present,
    outranks,
    validate_amount,
    validate_splits,
    validate_transfer_pair,
)
from app.models import Account, BankConnection, Category, Transaction, TransactionSplit
from app.models._common import TRANSACTION_SOURCES
from app.security.client_ip import IPAddress
from app.services import audit

T = TypeVar("T")

RLS_DENIED = "42501"
RETRYABLE_SQLSTATES = frozenset({"40001", "40P01", "55P03"})
LOCK_ATTEMPTS = 3
CONNECTOR_SOURCES = ("plaid", "simplefin")
# What a sync may refresh on an existing row. Category, amount and the rest have their own rules.
_UPSERT_FIELDS = (
    "posted_date",
    "authorized_date",
    "description_raw",
    "merchant_name",
    "plaid_pfc_primary",
    "plaid_pfc_detailed",
    "is_pending",
    "pending_external_id",
)


class LedgerNotFoundError(LedgerRuleError):
    """The row is missing, in another household, or not writable by this session."""


class TransactionNotFoundError(LedgerNotFoundError):
    pass


class AccountNotFoundError(LedgerNotFoundError):
    pass


class CategoryNotFoundError(LedgerNotFoundError):
    pass


class SupersededTransactionError(LedgerRuleError):
    """A superseded row is history only (ADR 0017): it can't be edited or paired."""


class LedgerRetryableError(RuntimeError):
    """A deadlock, serialization or lock-timeout failure. Roll back and retry the unit of work."""


class AutoCategorySource(enum.StrEnum):
    """Who set a category without a person doing it. ``user`` is deliberately absent."""

    RULE = "rule"
    PLAID = "plaid"
    ML = "ml"


@dataclass(frozen=True)
class NewTransaction:
    """A transaction to insert. ``amount`` is signed: outflow negative (Plaid already negated)."""

    account_id: uuid.UUID
    posted_date: date
    amount: Decimal
    description_raw: str
    source: str
    external_id: str | None = None
    import_fingerprint: str | None = None
    import_run_id: uuid.UUID | None = None
    authorized_date: date | None = None
    merchant_name: str | None = None
    plaid_pfc_primary: str | None = None
    plaid_pfc_detailed: str | None = None
    category_id: uuid.UUID | None = None
    category_source: AutoCategorySource | None = None
    is_pending: bool = False
    pending_external_id: str | None = None


class UpsertAction(enum.StrEnum):
    INSERTED = "inserted"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class UpsertOutcome:
    """What ``upsert_external`` did. The caller folds these into the sync run's counts."""

    transaction_id: uuid.UUID
    action: UpsertAction
    splits_cleared: bool = False  # the amount changed on a split row
    transfer_dissolved: bool = False  # the amount changed on a paired leg and broke the pair
    category_kept: bool = False  # a user or rule category held against the incoming one


@dataclass(frozen=True)
class SplitInput:
    amount: Decimal
    category_id: uuid.UUID | None = None
    tax_tag: str | None = None
    note: str | None = None


def _translate(exc: Exception, denied: type[LedgerNotFoundError]) -> Exception:
    """Map a database failure to a typed error that carries no SQL parameters."""
    if isinstance(exc, StaleDataError):  # RLS filtered the UPDATE: visible but not writable
        return TransactionNotFoundError("transaction not found")
    if isinstance(exc, DBAPIError):
        state = getattr(exc.orig, "sqlstate", None)
        if state == RLS_DENIED:
            return denied("not found")
        if state in RETRYABLE_SQLSTATES:
            return LedgerRetryableError("ledger write conflicted; retry the unit of work")
        if isinstance(state, str) and state.startswith("23"):
            return LedgerRuleError("write violates a ledger constraint")
    return exc


class LedgerWriter:
    def __init__(
        self,
        session: AsyncSession,
        household_id: uuid.UUID,
        *,
        user_id: uuid.UUID | None = None,
        ip: IPAddress | None = None,
    ) -> None:
        bound = session.info.get("household_id")
        if bound is None or bound != household_id:
            raise ValueError("LedgerWriter needs a session scoped to this household")
        self._s = session
        self._household_id: uuid.UUID = bound
        self._user_id = user_id
        self._ip = ip
        # Per-unit-of-work tallies for the caller's sync run (no per-row audit, PHASE-2 3.3):
        # ``rows_superseded``, ``rows_unsuperseded``, ``splits_kept_on_old``,
        # ``supersede_conflict``,
        # ``supersede_amount_mismatch``, ``splits_cleared``, ``transfers_dissolved``.
        self.counts: Counter[str] = Counter()

    # ------------------------------------------------------------------ insert

    async def insert_transaction(
        self, new: NewTransaction, *, skip_duplicates: bool = False
    ) -> uuid.UUID | None:
        """Insert one row.

        With ``skip_duplicates`` a conflict on **either** idempotency key (account + import
        fingerprint, account + external id) inserts nothing and returns ``None``; the row must
        carry at least one key. Without it, a conflict raises ``LedgerRuleError`` and leaves the
        caller's transaction failed: roll back.
        """
        validate_amount(new.amount)
        if new.source not in TRANSACTION_SOURCES:
            raise LedgerRuleError("unknown transaction source")
        category_source: str | None = None
        if new.category_source is not None:
            try:
                category_source = AutoCategorySource(new.category_source).value
            except ValueError:
                raise LedgerRuleError("unknown category source") from None
        if skip_duplicates and new.import_fingerprint is None and new.external_id is None:
            raise LedgerRuleError("skip_duplicates needs an import fingerprint or external id")
        if new.category_id is not None:
            await self._require_category(new.category_id)
        stmt = pg_insert(Transaction).values(
            id=uuid.uuid4(),
            household_id=self._household_id,
            account_id=new.account_id,
            posted_date=new.posted_date,
            amount=new.amount,
            description_raw=new.description_raw,
            source=new.source,
            external_id=new.external_id,
            import_fingerprint=new.import_fingerprint,
            import_run_id=new.import_run_id,
            authorized_date=new.authorized_date,
            merchant_name=new.merchant_name,
            plaid_pfc_primary=new.plaid_pfc_primary,
            plaid_pfc_detailed=new.plaid_pfc_detailed,
            category_id=new.category_id,
            category_source=category_source,
            is_pending=new.is_pending,
            pending_external_id=new.pending_external_id,
        )
        if skip_duplicates:
            stmt = stmt.on_conflict_do_nothing()  # any unique conflict: fingerprint or external id
        result = await self._db(
            self._s.execute(stmt.returning(Transaction.id)), AccountNotFoundError
        )
        return result.scalar_one_or_none()

    # ---------------------------------------------------------------- category

    async def set_category(self, transaction_id: uuid.UUID, category_id: uuid.UUID | None) -> None:
        """A person sets (or clears, with ``None``) a category. Needs ``user_id``; audited.

        Clearing is a choice too: the source stays ``user`` so automation does not refill the row.
        ``release_category`` hands a row back to automation.
        """
        user = self._require_user()
        if category_id is not None:
            await self._require_category(category_id)
        txn = (await self._lock_group([transaction_id]))[transaction_id]
        self._require_live(txn)
        previous = txn.category_id
        txn.category_id = category_id
        txn.category_source = "user"
        await self._flush()
        self._audit(
            "ledger.category_set",
            transaction_id,
            {
                "category_id": None if category_id is None else str(category_id),
                "previous_category_id": None if previous is None else str(previous),
            },
            user,
        )

    async def release_category(self, transaction_id: uuid.UUID) -> None:
        """Hand a row back to automation: clear ``category_source`` and keep the category until an
        automated source replaces it. Needs ``user_id``; audited."""
        user = self._require_user()
        txn = (await self._lock_group([transaction_id]))[transaction_id]
        self._require_live(txn)
        previous = txn.category_source
        txn.category_source = None
        await self._flush()
        self._audit(
            "ledger.category_released",
            transaction_id,
            {"previous_source": previous},
            user,
        )

    async def apply_category(
        self,
        transaction_id: uuid.UUID,
        category_id: uuid.UUID | None,
        source: AutoCategorySource,
    ) -> bool:
        """Automated categorisation (rules, Plaid, ML). Not audited per row.

        Precedence is user > rule > plaid > ml. A source never overwrites a higher-ranked one: the
        row is left untouched and this returns ``False``. An equal or higher source applies and
        returns ``True``. A user's choice (including "no category") always holds.
        """
        try:
            auto = AutoCategorySource(source)
        except ValueError:
            raise LedgerRuleError("unknown category source") from None
        if category_id is not None:
            await self._require_category(category_id)
        txn = (await self._lock_group([transaction_id]))[transaction_id]
        self._require_live(txn)
        if not can_replace_category(txn.category_source, auto.value):
            return False
        txn.category_id = category_id
        txn.category_source = None if category_id is None else auto.value
        await self._flush()
        return True

    # ------------------------------------------------------------------ amount

    async def set_amount(
        self,
        transaction_id: uuid.UUID,
        amount: Decimal,
        *,
        splits: Sequence[SplitInput] | None = None,
    ) -> None:
        """Change an amount. A split transaction needs its new splits in the same call. A
        transfer leg keeps its sign and magnitude: unmark the transfer first to change either."""
        user = self._require_user()
        validate_amount(amount)
        if splits is not None:
            await self._check_splits(amount, splits)
        rows = await self._lock_group([transaction_id])
        txn = rows[transaction_id]
        self._require_live(txn)
        if await self._has_splits(txn.id) and splits is None:
            raise LedgerRuleError("this transaction is split: pass new splits with the amount")
        if txn.transfer_group_id is not None:
            partner = self._partner(txn, rows)
            await self._check_pair(txn, partner, amount_a=amount)
        txn.amount = amount
        await self._flush()
        if splits is not None:
            await self._write_split_rows(txn, splits)
        self._audit("ledger.amount_set", transaction_id, {}, user)

    # ---------------------------------------------------------------- transfers

    async def mark_transfer_pair(self, a_id: uuid.UUID, b_id: uuid.UUID) -> uuid.UUID:
        """Link two transactions as the two legs of one transfer. Returns the group id."""
        user = self._require_user()
        return await self._pair(a_id, b_id, "ledger.transfer_paired", user, {})

    async def apply_transfer_pair(
        self, a_id: uuid.UUID, b_id: uuid.UUID, *, job_run_id: uuid.UUID | None = None
    ) -> uuid.UUID:
        """Automated pairing (the transfer matcher). Same checks as ``mark_transfer_pair`` plus a
        re-check after the lock: neither leg may be pending, and neither may be a one-legged
        transfer a person marked since the matcher loaded it.

        Needs an acting ``user_id`` or a household-wide (system job) session, so a request handler
        cannot call it as "the system". Audited as ``ledger.transfer_auto_paired``.
        """
        if self._user_id is None and not await is_household_wide(self._s):
            raise LedgerRuleError("automatic pairing needs an acting user or a system job session")
        detail: dict[str, object] = {
            "actor": "transfer_matcher",
            "job_run_id": None if job_run_id is None else str(job_run_id),
        }
        return await self._pair(
            a_id, b_id, "ledger.transfer_auto_paired", self._user_id, detail, auto=True
        )

    async def _pair(
        self,
        a_id: uuid.UUID,
        b_id: uuid.UUID,
        action: str,
        user: uuid.UUID | None,
        extra: dict[str, object],
        *,
        auto: bool = False,
    ) -> uuid.UUID:
        if a_id == b_id:
            raise TransferPairError("a transfer pairs two different transactions")
        rows = await self._lock_group([a_id, b_id])
        a, b = rows[a_id], rows[b_id]
        for leg in (a, b):
            self._require_live(leg)
            if leg.transfer_group_id is not None:
                raise TransferPairError("transaction is already in a transfer pair")
            if auto and (leg.is_pending or leg.is_transfer):
                raise TransferPairError("transaction changed since it was matched")
        await self._check_pair(a, b)
        group = uuid.uuid4()
        for leg in (a, b):
            leg.is_transfer = True
            leg.transfer_group_id = group
        await self._flush()
        detail: dict[str, object] = {"group_id": str(group), "other": str(b_id), **extra}
        self._audit(action, a_id, detail, user)
        return group

    async def mark_transfer_single(self, transaction_id: uuid.UUID) -> None:
        """Mark a one-legged transfer (the other side is an unlinked account)."""
        user = self._require_user()
        txn = (await self._lock_group([transaction_id]))[transaction_id]
        self._require_live(txn)
        if txn.transfer_group_id is not None:
            raise TransferPairError("transaction is already in a transfer pair")
        txn.is_transfer = True
        await self._flush()
        self._audit("ledger.transfer_marked", transaction_id, {}, user)

    async def unmark_transfer(self, transaction_id: uuid.UUID) -> None:
        """Clear the transfer flag. A paired transfer is dissolved on both legs together, and
        only if this session can write both."""
        user = self._require_user()
        rows = await self._lock_group([transaction_id])
        txn = rows[transaction_id]
        legs = [txn]
        if txn.transfer_group_id is not None:
            legs = [r for r in rows.values() if r.transfer_group_id == txn.transfer_group_id]
            if len(legs) != 2:
                raise TransferPairError("both legs of a transfer must be writable to unmark it")
        for leg in legs:
            leg.is_transfer = False
            leg.transfer_group_id = None
        await self._flush()
        self._audit("ledger.transfer_unmarked", transaction_id, {"legs": len(legs)}, user)

    # ------------------------------------------------------------------ splits

    async def write_splits(self, transaction_id: uuid.UUID, parts: Sequence[SplitInput]) -> None:
        """Replace all splits of a transaction. They must sum exactly to its amount."""
        user = self._require_user()
        txn = (await self._lock_group([transaction_id]))[transaction_id]
        self._require_live(txn)
        await self._check_splits(txn.amount, parts)
        await self._write_split_rows(txn, parts)
        self._audit("ledger.splits_written", transaction_id, {"parts": len(parts)}, user)

    async def clear_splits(self, transaction_id: uuid.UUID) -> None:
        user = self._require_user()
        txn = (await self._lock_group([transaction_id]))[transaction_id]
        self._require_live(txn)
        await self._db(
            self._s.execute(
                delete(TransactionSplit).where(TransactionSplit.transaction_id == txn.id)
            ),
            TransactionNotFoundError,
        )
        self._audit("ledger.splits_cleared", transaction_id, {}, user)

    # -------------------------------------------------------------- sync verbs (PHASE-2 3.3)

    async def upsert_external(self, new: NewTransaction) -> UpsertOutcome:
        """Insert or update a connector row, keyed on ``(account_id, external_id)``.

        Never replaces a ``user`` or ``rule`` category. A changed amount clears the row's splits
        and, for a paired transfer leg, dissolves the pair (the legs no longer match); both are
        reported on the outcome and tallied in ``counts``. Not audited per row: the sync run is
        the record.
        """
        validate_amount(new.amount)
        if new.external_id is None or new.source not in CONNECTOR_SOURCES:
            raise LedgerRuleError("upsert_external needs a connector row with an external id")
        existing_id = await self._id_by_external(new.account_id, new.external_id)
        if existing_id is None:
            inserted = await self.insert_transaction(new, skip_duplicates=True)
            if inserted is not None:
                return UpsertOutcome(inserted, UpsertAction.INSERTED)
            existing_id = await self._id_by_external(new.account_id, new.external_id)
            if existing_id is None:
                raise LedgerRetryableError("row appeared and vanished; retry the unit of work")
        rows = await self._lock_group([existing_id])
        txn = rows[existing_id]
        self._require_live(txn)
        changed = False
        for attr in _UPSERT_FIELDS:
            value = getattr(new, attr)
            if getattr(txn, attr) != value:
                setattr(txn, attr, value)
                changed = True
        category_kept = False
        if new.category_id is not None and new.category_source is not None:
            incoming = AutoCategorySource(new.category_source).value
            if txn.category_id != new.category_id or txn.category_source != incoming:
                if txn.category_source in ("user", "rule"):
                    category_kept = True
                elif can_replace_category(txn.category_source, incoming):
                    await self._require_category(new.category_id)
                    txn.category_id = new.category_id
                    txn.category_source = incoming
                    changed = True
        splits_cleared = dissolved = False
        if txn.amount != new.amount:
            splits_cleared = await self._has_splits(txn.id)
            if splits_cleared:
                await self._delete_splits(txn.id)
                self.counts["splits_cleared"] += 1
            if txn.transfer_group_id is not None:
                partner = self._partner(txn, rows)
                try:
                    await self._check_pair(txn, partner, amount_a=new.amount)
                except TransferPairError:
                    for leg in (txn, partner):
                        leg.is_transfer = False
                        leg.transfer_group_id = None
                    dissolved = True
                    self.counts["transfers_dissolved"] += 1
            txn.amount = new.amount
            changed = True
        await self._flush()
        return UpsertOutcome(
            txn.id,
            UpsertAction.UPDATED if changed else UpsertAction.UNCHANGED,
            splits_cleared=splits_cleared,
            transfer_dissolved=dissolved,
            category_kept=category_kept,
        )

    async def remove_external(self, account_id: uuid.UUID, external_id: str) -> bool:
        """Delete a connector row (Plaid ``removed``). ``False`` when there is no such row.

        A winner is un-superseded first, so the row it replaced counts again and gets back the
        state the winner holds. A paired leg's partner is left to the Phase 4 orphan trigger.
        """
        tid = await self._id_by_external(account_id, external_id)
        if tid is None:
            return False
        replaced = (
            await self._db(
                self._s.execute(select(Transaction.id).where(Transaction.superseded_by_id == tid)),
                TransactionNotFoundError,
            )
        ).scalar_one_or_none()
        if replaced is not None:
            await self.unsupersede_transaction(replaced)
        txn = (await self._lock_group([tid]))[tid]
        result = await self._db(
            self._s.execute(delete(Transaction).where(Transaction.id == tid)),
            TransactionNotFoundError,
        )
        if getattr(result, "rowcount", 0) != 1:
            raise TransactionNotFoundError("transaction not found")
        self._s.expunge(txn)
        return True

    async def post_pending(self, pending_external_id: str, posted: NewTransaction) -> uuid.UUID:
        """Replace a pending row with its posted row in one unit; returns the posted row's id.

        The posted row takes the pending row's category when that source outranks its own, the
        notes, tax and receipt fields, the transfer state, and the splits when the amount is
        unchanged. When the amount changed, the splits go with the pending row and a paired
        pending leg's pair is dissolved. Both are tallied in ``counts``.
        """
        validate_amount(posted.amount)
        if posted.external_id is None or posted.source not in CONNECTOR_SOURCES:
            raise LedgerRuleError("post_pending needs a connector row with an external id")
        if posted.is_pending:
            raise LedgerRuleError("the posted row cannot be pending")
        if posted.pending_external_id not in (None, pending_external_id):
            raise LedgerRuleError("pending_external_id does not match the posted row")
        pending_id = await self._id_by_external(posted.account_id, pending_external_id)
        if pending_id is None:
            raise TransactionNotFoundError("pending transaction not found")
        if await self._id_by_external(posted.account_id, posted.external_id) is not None:
            raise LedgerRuleError("posted transaction already exists")
        posted_id = await self.insert_transaction(
            replace(posted, pending_external_id=pending_external_id), skip_duplicates=True
        )
        if posted_id is None:
            raise LedgerRetryableError("posted row appeared meanwhile; retry the unit of work")
        rows = await self._lock_group([pending_id, posted_id])
        pending, post = rows[pending_id], rows[posted_id]
        self._require_live(pending)
        if not pending.is_pending:
            raise LedgerRuleError("the replaced row is not pending")
        if outranks(pending.category_source, post.category_source):
            post.category_id, post.category_source = pending.category_id, pending.category_source
        post.notes = pending.notes
        post.is_tax_relevant = pending.is_tax_relevant
        post.tax_tag = pending.tax_tag
        post.receipt_document_id = pending.receipt_document_id
        await self._flush()
        if pending.amount == post.amount:
            await self._move_splits(pending.id, post.id)
            await self._move_transfer_state(pending, post)
        else:
            if await self._has_splits(pending.id):
                self.counts["splits_cleared"] += 1
            if pending.transfer_group_id is not None:
                group = pending.transfer_group_id
                for leg in [r for r in rows.values() if r.transfer_group_id == group]:
                    leg.is_transfer = False
                    leg.transfer_group_id = None
                self.counts["transfers_dissolved"] += 1
            elif pending.is_transfer:
                pending.is_transfer = False
                post.is_transfer = True
            await self._flush()
        await self._db(
            self._s.execute(delete(Transaction).where(Transaction.id == pending_id)),
            TransactionNotFoundError,
        )
        self._s.expunge(pending)
        return posted_id

    # ------------------------------------------------------------------- supersede

    async def supersede_transaction(self, old_id: uuid.UUID, new_id: uuid.UUID) -> None:
        """A connector row (``new``) replaces an imported row (``old``). The single definition,
        PHASE-2 3.3: preconditions first (nothing is written if one fails), then ``new`` takes
        what people corrected on ``old``, then ``old`` is marked. Not audited per row.

        Raises ``LedgerRuleError("supersede_amount_mismatch")`` for unequal amounts and
        ``SupersedeConflict`` for transfer state that can't be taken over. Both rows stay live.
        """
        if old_id == new_id:
            raise LedgerRuleError("supersede_same_row")
        rows = await self._lock_group([old_id, new_id])
        old, new = rows[old_id], rows[new_id]
        if old.account_id != new.account_id:
            raise LedgerRuleError("supersede_account_mismatch")
        if old.source != "import" or new.source not in CONNECTOR_SOURCES:
            raise LedgerRuleError("supersede_source")
        if not await self._is_production(new.account_id):
            raise LedgerRuleError("supersede_not_production")
        if (
            old.is_pending
            or new.is_pending
            or old.superseded_by_id is not None
            or new.superseded_by_id is not None
            or await self._in_supersession(old_id, new_id)
        ):
            raise LedgerRuleError("supersede_not_eligible")
        if old.amount != new.amount:
            self.counts["supersede_amount_mismatch"] += 1
            raise LedgerRuleError("supersede_amount_mismatch")
        if new.is_transfer and (
            not old.is_transfer or new.transfer_group_id != old.transfer_group_id
        ):
            self.counts["supersede_conflict"] += 1
            raise SupersedeConflict("supersede_transfer_conflict")
        # 1. Category by rank. (C3b adds category_rule_id to this move.)
        if outranks(old.category_source, new.category_source):
            new.category_id, new.category_source = old.category_id, old.category_source
        # 2. Splits.
        if await self._has_splits(old_id):
            if await self._has_splits(new_id):
                self.counts["splits_kept_on_old"] += 1
            else:
                await self._move_splits(old_id, new_id)
        # 3. Transfer state. No rejection row is written.
        if not new.is_transfer:
            await self._move_transfer_state(old, new)
        # 4 to 6. Tax, receipt, notes.
        new.is_tax_relevant = new.is_tax_relevant or old.is_tax_relevant
        new.tax_tag = first_present(new.tax_tag, old.tax_tag)
        if new.receipt_document_id is None:
            new.receipt_document_id = old.receipt_document_id
        new.notes = first_present(new.notes, old.notes)
        await self._flush()
        # 7. Mark. The Phase 4 trigger moves rejections, links and recurring members.
        old.superseded_by_id = new_id
        await self._flush()
        self.counts["rows_superseded"] += 1

    async def unsupersede_transaction(self, old_id: uuid.UUID) -> None:
        """Undo ``supersede_transaction``: ``old`` is live again and takes back what the winner
        holds where its own value is empty (or lower-ranked, for the category). The winner's
        transfer state is cleared, so deleting it afterwards leaves no orphan leg."""
        found = (
            await self._db(
                self._s.execute(
                    select(Transaction.superseded_by_id).where(Transaction.id == old_id)
                ),
                TransactionNotFoundError,
            )
        ).first()
        if found is None:
            raise TransactionNotFoundError("transaction not found")
        w_id: uuid.UUID | None = found[0]
        if w_id is None:
            raise LedgerRuleError("transaction is not superseded")
        rows = await self._lock_group([old_id, w_id])
        old, win = rows[old_id], rows[w_id]
        if old.superseded_by_id != w_id:
            raise LedgerRetryableError("supersession changed while locking; retry the unit of work")
        old.superseded_by_id = None  # the trigger moves the derived rows back
        await self._flush()
        if not await self._has_splits(old_id):
            await self._move_splits(w_id, old_id)
        if (win.is_transfer or win.transfer_group_id is not None) and not old.is_transfer:
            await self._move_transfer_state(win, old)
        old.is_tax_relevant = old.is_tax_relevant or win.is_tax_relevant
        old.tax_tag = first_present(old.tax_tag, win.tax_tag)
        if old.receipt_document_id is None:
            old.receipt_document_id = win.receipt_document_id
        old.notes = first_present(old.notes, win.notes)
        if outranks(win.category_source, old.category_source):
            old.category_id, old.category_source = win.category_id, win.category_source
        await self._flush()
        self.counts["rows_unsuperseded"] += 1

    async def _id_by_external(self, account_id: uuid.UUID, external_id: str) -> uuid.UUID | None:
        return (
            await self._db(
                self._s.execute(
                    select(Transaction.id).where(
                        Transaction.account_id == account_id,
                        Transaction.external_id == external_id,
                    )
                ),
                TransactionNotFoundError,
            )
        ).scalar_one_or_none()

    async def _delete_splits(self, transaction_id: uuid.UUID) -> None:
        await self._db(
            self._s.execute(
                delete(TransactionSplit).where(TransactionSplit.transaction_id == transaction_id)
            ),
            TransactionNotFoundError,
        )

    async def _move_splits(self, source_id: uuid.UUID, target_id: uuid.UUID) -> None:
        """Re-parent every split row (amounts are equal, so the exact-sum check still holds)."""
        await self._db(
            self._s.execute(
                update(TransactionSplit)
                .where(TransactionSplit.transaction_id == source_id)
                .values(transaction_id=target_id)
            ),
            TransactionNotFoundError,
        )

    async def _move_transfer_state(self, src: Transaction, dst: Transaction) -> None:
        """``src`` leaves its group (or single mark) first, then ``dst`` takes it."""
        group = src.transfer_group_id
        if group is None and not src.is_transfer:
            return
        src.is_transfer = False
        src.transfer_group_id = None
        await self._flush()
        dst.is_transfer = True
        dst.transfer_group_id = group
        await self._flush()

    async def _is_production(self, account_id: uuid.UUID) -> bool:
        env = (
            await self._db(
                self._s.execute(
                    select(BankConnection.env)
                    .join(Account, Account.bank_connection_id == BankConnection.id)
                    .where(Account.id == account_id)
                ),
                AccountNotFoundError,
            )
        ).scalar_one_or_none()
        return env == "production"

    async def _in_supersession(self, a: uuid.UUID, b: uuid.UUID) -> bool:
        found = await self._db(
            self._s.execute(
                select(Transaction.id).where(Transaction.superseded_by_id.in_((a, b))).limit(1)
            ),
            TransactionNotFoundError,
        )
        return found.first() is not None

    # ----------------------------------------------------------------- helpers

    async def _db(self, awaitable: Awaitable[T], denied: type[LedgerNotFoundError]) -> T:
        """Await a database call; raise translated errors outside the handler (clean chain)."""
        failure: Exception | None = None
        try:
            return await awaitable
        except (DBAPIError, StaleDataError) as exc:
            failure = _translate(exc, denied)
        raise failure

    async def _flush(self) -> None:
        await self._db(self._s.flush(), TransactionNotFoundError)

    def _require_user(self) -> uuid.UUID:
        if self._user_id is None:
            raise LedgerRuleError("this edit needs an acting user")
        return self._user_id

    @staticmethod
    def _require_live(txn: Transaction) -> None:
        if txn.superseded_by_id is not None:
            raise SupersededTransactionError("a superseded transaction cannot be changed")

    async def _lock_group(self, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Transaction]:
        """Lock the requested rows and every visible leg of their transfer groups, in id order.

        The group ids are read unlocked, so after locking they are re-read: if a row joined or
        left a group meanwhile, lock again (bounded), else ask the caller to retry.
        """
        wanted = set(ids)
        for _ in range(LOCK_ATTEMPTS):
            seen = (
                await self._db(
                    self._s.execute(
                        select(Transaction.transfer_group_id).where(Transaction.id.in_(wanted))
                    ),
                    TransactionNotFoundError,
                )
            ).scalars()
            groups = {g for g in seen if g is not None}
            cond: ColumnElement[bool] = Transaction.id.in_(wanted)
            if groups:
                cond = cond | Transaction.transfer_group_id.in_(groups)
            locked = (
                (
                    await self._db(
                        self._s.execute(
                            select(Transaction)
                            .where(cond)
                            .order_by(Transaction.id)
                            .with_for_update()
                            .execution_options(populate_existing=True)
                        ),
                        TransactionNotFoundError,
                    )
                )
                .scalars()
                .all()
            )
            by_id = {r.id: r for r in locked}
            if any(i not in by_id for i in wanted):
                raise TransactionNotFoundError("transaction not found")
            # A row that joined a group after the unlocked read needs that group locked too.
            if {by_id[i].transfer_group_id for i in wanted} - {None} <= groups:
                return by_id
        raise LedgerRetryableError("transfer group changed while locking; retry the unit of work")

    @staticmethod
    def _partner(txn: Transaction, rows: dict[uuid.UUID, Transaction]) -> Transaction:
        others = [
            r
            for r in rows.values()
            if r.transfer_group_id == txn.transfer_group_id and r is not txn
        ]
        if len(others) != 1:
            raise TransferPairError("both legs of a transfer must be writable to change it")
        return others[0]

    async def _require_category(self, category_id: uuid.UUID) -> None:
        found = (
            await self._db(
                self._s.execute(select(Category.id).where(Category.id == category_id)),
                TransactionNotFoundError,
            )
        ).scalar_one_or_none()
        if found is None:
            raise CategoryNotFoundError("category not found")

    async def _has_splits(self, transaction_id: uuid.UUID) -> bool:
        result = await self._db(
            self._s.execute(
                select(TransactionSplit.id)
                .where(TransactionSplit.transaction_id == transaction_id)
                .limit(1)
            ),
            TransactionNotFoundError,
        )
        return result.first() is not None

    async def _check_splits(self, amount: Decimal, parts: Sequence[SplitInput]) -> None:
        """Every split rule, before any row is touched."""
        validate_splits(amount, [p.amount for p in parts])
        for category_id in {p.category_id for p in parts if p.category_id is not None}:
            await self._require_category(category_id)

    async def _write_split_rows(self, txn: Transaction, parts: Sequence[SplitInput]) -> None:
        await self._db(
            self._s.execute(
                delete(TransactionSplit).where(TransactionSplit.transaction_id == txn.id)
            ),
            TransactionNotFoundError,
        )
        for p in parts:
            self._s.add(
                TransactionSplit(
                    id=uuid.uuid4(),
                    household_id=self._household_id,
                    transaction_id=txn.id,
                    category_id=p.category_id,
                    amount=p.amount,
                    tax_tag=p.tax_tag,
                    note=p.note,
                )
            )
        await self._flush()

    async def _currency(self, account_id: uuid.UUID) -> str:
        currency = (
            await self._db(
                self._s.execute(select(Account.currency).where(Account.id == account_id)),
                AccountNotFoundError,
            )
        ).scalar_one_or_none()
        if currency is None:
            raise AccountNotFoundError("account not found")
        return currency

    async def _check_pair(
        self, a: Transaction, b: Transaction, *, amount_a: Decimal | None = None
    ) -> None:
        def leg(t: Transaction, amount: Decimal, currency: str) -> TransferLeg:
            return TransferLeg(t.id, t.household_id, t.account_id, amount, currency)

        validate_transfer_pair(
            leg(a, a.amount if amount_a is None else amount_a, await self._currency(a.account_id)),
            leg(b, b.amount, await self._currency(b.account_id)),
        )

    def _audit(
        self,
        action: str,
        transaction_id: uuid.UUID,
        detail: dict[str, object],
        user: uuid.UUID | None,
    ) -> None:
        audit.add(
            self._s,
            action,
            household_id=self._household_id,
            user_id=user,
            ip=self._ip,
            entity="transaction",
            entity_id=transaction_id,
            detail=detail,
        )
