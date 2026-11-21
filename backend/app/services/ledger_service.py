"""The one writer of ledger money fields (ADR 0011, PLAN 20 money review).

``LedgerService`` is the only code allowed to create a ``Transaction`` or ``TransactionSplit``, or
to set a transaction's ``amount``, ``category_id``/``category_source``, ``is_transfer`` or
``transfer_group_id``. ``tests/test_ledger_single_writer.py`` fails the build if any other module
under ``app/`` does. Reads live in ``app.services.ledger``.

It works on a session the caller opened with ``scoped_session`` (RLS context, one unit of work,
committed by the caller). Pure checks live in ``app.domain.ledger_rules``; this module loads rows,
runs them, persists, and audits user-visible edits (category set by a user, transfers, splits,
amount changes). Bulk ingest inserts are not audited row by row: the import/sync run is the record.

A row the caller cannot see or write (other household, private account, summary-only) behaves as
missing: ``TransactionNotFoundError``. Messages never carry amounts or descriptions.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.domain.ledger_rules import (
    LedgerRuleError,
    TransferLeg,
    TransferPairError,
    validate_amount,
    validate_splits,
    validate_transfer_pair,
)
from app.models import Account, Category, Transaction, TransactionSplit
from app.models._common import CATEGORY_SOURCES, TRANSACTION_SOURCES
from app.security.client_ip import IPAddress
from app.services import audit


class TransactionNotFoundError(LedgerRuleError):
    """No such transaction for this session (missing, other household, or not writable)."""


class CategoryNotFoundError(LedgerRuleError):
    """No such category in this household."""


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
    category_source: str | None = None
    is_pending: bool = False
    pending_external_id: str | None = None


@dataclass(frozen=True)
class SplitInput:
    amount: Decimal
    category_id: uuid.UUID | None = None
    tax_tag: str | None = None
    note: str | None = None


class LedgerService:
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
        self._ip = ip

    # ------------------------------------------------------------------ insert

    async def insert_transaction(
        self, new: NewTransaction, *, skip_duplicates: bool = False
    ) -> uuid.UUID | None:
        """Insert one row. With ``skip_duplicates`` a conflict on the idempotency key (import
        fingerprint, else external id) inserts nothing and returns ``None``."""
        validate_amount(new.amount)
        if new.source not in TRANSACTION_SOURCES:
            raise LedgerRuleError("unknown transaction source")
        if new.category_source is not None and new.category_source not in CATEGORY_SOURCES:
            raise LedgerRuleError("unknown category source")
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
            category_source=new.category_source,
            is_pending=new.is_pending,
            pending_external_id=new.pending_external_id,
        )
        if skip_duplicates:
            if new.import_fingerprint is not None:
                keys = ["account_id", "import_fingerprint"]
            elif new.external_id is not None:
                keys = ["account_id", "external_id"]
            else:
                raise LedgerRuleError("skip_duplicates needs an import fingerprint or external id")
            stmt = stmt.on_conflict_do_nothing(index_elements=keys)
        try:
            return (await self._s.execute(stmt.returning(Transaction.id))).scalar_one_or_none()
        except IntegrityError as exc:
            raise LedgerRuleError("transaction violates a ledger constraint") from exc

    # ---------------------------------------------------------------- category

    async def set_category(
        self,
        transaction_id: uuid.UUID,
        category_id: uuid.UUID | None,
        *,
        source: str = "user",
    ) -> None:
        """Set (or clear, with ``None``) a transaction's category. User edits are audited."""
        if source not in CATEGORY_SOURCES:
            raise LedgerRuleError("unknown category source")
        if category_id is not None:
            await self._require_category(category_id)
        (txn,) = await self._lock([transaction_id])
        previous = txn.category_id
        txn.category_id = category_id
        txn.category_source = None if category_id is None else source
        await self._flush()
        if source == "user":
            self._audit(
                "ledger.category_set",
                transaction_id,
                {
                    "category_id": None if category_id is None else str(category_id),
                    "previous_category_id": None if previous is None else str(previous),
                },
            )

    # ------------------------------------------------------------------ amount

    async def set_amount(
        self,
        transaction_id: uuid.UUID,
        amount: Decimal,
        *,
        splits: Sequence[SplitInput] | None = None,
    ) -> None:
        """Change an amount. A split transaction needs its new splits in the same call; a
        transfer leg may not change sign relative to its partner."""
        validate_amount(amount)
        (txn,) = await self._lock([transaction_id])
        has_splits = await self._has_splits(txn.id)
        if has_splits and splits is None:
            raise LedgerRuleError("this transaction is split: pass new splits with the amount")
        if txn.transfer_group_id is not None:
            partner = await self._partner(txn)
            await self._check_pair(txn, partner, amount_a=amount)
        txn.amount = amount
        await self._flush()
        if splits is not None:
            await self._replace_splits(txn, splits)
        self._audit("ledger.amount_set", transaction_id, {})

    # ---------------------------------------------------------------- transfers

    async def mark_transfer_pair(self, a_id: uuid.UUID, b_id: uuid.UUID) -> uuid.UUID:
        """Link two transactions as the two legs of one transfer. Returns the group id."""
        if a_id == b_id:
            raise TransferPairError("a transfer pairs two different transactions")
        a, b = await self._lock([a_id, b_id])
        for leg in (a, b):
            if leg.superseded_by_id is not None:
                raise TransferPairError("a superseded transaction cannot be a transfer leg")
            if leg.transfer_group_id is not None:
                raise TransferPairError("transaction is already in a transfer pair")
        await self._check_pair(a, b)
        group = uuid.uuid4()
        for leg in (a, b):
            leg.is_transfer = True
            leg.transfer_group_id = group
        await self._flush()
        self._audit("ledger.transfer_paired", a_id, {"group_id": str(group), "other": str(b_id)})
        return group

    async def mark_transfer_single(self, transaction_id: uuid.UUID) -> None:
        """Mark a one-legged transfer (the other side is an unlinked account)."""
        (txn,) = await self._lock([transaction_id])
        if txn.transfer_group_id is not None:
            raise TransferPairError("transaction is already in a transfer pair")
        txn.is_transfer = True
        await self._flush()
        self._audit("ledger.transfer_marked", transaction_id, {})

    async def unmark_transfer(self, transaction_id: uuid.UUID) -> None:
        """Clear the transfer flag. A paired transfer is dissolved on both legs together."""
        (txn,) = await self._lock([transaction_id])
        legs = [txn]
        if txn.transfer_group_id is not None:
            rows = (
                (
                    await self._s.execute(
                        select(Transaction)
                        .where(Transaction.transfer_group_id == txn.transfer_group_id)
                        .order_by(Transaction.id)
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            legs = list(rows)
        for leg in legs:
            leg.is_transfer = False
            leg.transfer_group_id = None
        await self._flush()
        self._audit("ledger.transfer_unmarked", transaction_id, {"legs": len(legs)})

    # ------------------------------------------------------------------ splits

    async def write_splits(self, transaction_id: uuid.UUID, parts: Sequence[SplitInput]) -> None:
        """Replace all splits of a transaction. They must sum exactly to its amount."""
        (txn,) = await self._lock([transaction_id])
        await self._replace_splits(txn, parts)
        self._audit("ledger.splits_written", transaction_id, {"parts": len(parts)})

    async def clear_splits(self, transaction_id: uuid.UUID) -> None:
        (txn,) = await self._lock([transaction_id])
        await self._s.execute(
            delete(TransactionSplit).where(TransactionSplit.transaction_id == txn.id)
        )
        await self._flush()
        self._audit("ledger.splits_cleared", transaction_id, {})

    # ----------------------------------------------------------------- helpers

    async def _lock(self, ids: Sequence[uuid.UUID]) -> list[Transaction]:
        """Load and row-lock transactions in id order (no deadlocks); return in request order."""
        rows = (
            (
                await self._s.execute(
                    select(Transaction)
                    .where(Transaction.id.in_(ids))
                    .order_by(Transaction.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        by_id = {r.id: r for r in rows}
        if any(i not in by_id for i in ids):
            raise TransactionNotFoundError("transaction not found")
        return [by_id[i] for i in ids]

    async def _flush(self) -> None:
        try:
            await self._s.flush()
        except StaleDataError as exc:  # RLS filtered the UPDATE: visible but not writable
            raise TransactionNotFoundError("transaction not found") from exc
        except IntegrityError as exc:
            raise LedgerRuleError("write violates a ledger constraint") from exc

    async def _require_category(self, category_id: uuid.UUID) -> None:
        found = (
            await self._s.execute(select(Category.id).where(Category.id == category_id))
        ).scalar_one_or_none()
        if found is None:
            raise CategoryNotFoundError("category not found")

    async def _has_splits(self, transaction_id: uuid.UUID) -> bool:
        return (
            await self._s.execute(
                select(TransactionSplit.id)
                .where(TransactionSplit.transaction_id == transaction_id)
                .limit(1)
            )
        ).first() is not None

    async def _replace_splits(self, txn: Transaction, parts: Sequence[SplitInput]) -> None:
        validate_splits(txn.amount, [p.amount for p in parts])
        for p in parts:
            if p.category_id is not None:
                await self._require_category(p.category_id)
        await self._s.execute(
            delete(TransactionSplit).where(TransactionSplit.transaction_id == txn.id)
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

    async def _partner(self, txn: Transaction) -> Transaction:
        rows = (
            (
                await self._s.execute(
                    select(Transaction)
                    .where(
                        Transaction.transfer_group_id == txn.transfer_group_id,
                        Transaction.id != txn.id,
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        if len(rows) != 1:
            raise TransferPairError("transfer group is not exactly two legs")
        return rows[0]

    async def _currency(self, account_id: uuid.UUID) -> str:
        currency = (
            await self._s.execute(select(Account.currency).where(Account.id == account_id))
        ).scalar_one_or_none()
        if currency is None:
            raise TransactionNotFoundError("account not found")
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

    def _audit(self, action: str, transaction_id: uuid.UUID, detail: dict[str, object]) -> None:
        audit.add(
            self._s,
            action,
            household_id=self._household_id,
            user_id=self._user_id,
            ip=self._ip,
            entity="transaction",
            entity_id=transaction_id,
            detail=detail,
        )
