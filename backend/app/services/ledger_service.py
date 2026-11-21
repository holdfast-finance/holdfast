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
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TypeVar

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.sql.elements import ColumnElement

from app.domain.ledger_rules import (
    LedgerRuleError,
    TransferLeg,
    TransferPairError,
    validate_amount,
    validate_splits,
    validate_transfer_pair,
)
from app.models import Account, Category, Transaction, TransactionSplit
from app.models._common import TRANSACTION_SOURCES
from app.security.client_ip import IPAddress
from app.services import audit

T = TypeVar("T")

RLS_DENIED = "42501"
RETRYABLE_SQLSTATES = frozenset({"40001", "40P01", "55P03"})
LOCK_ATTEMPTS = 3


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


# Higher wins. "user" is not an AutoCategorySource; an unknown or missing source ranks lowest.
SOURCE_RANK = {"ml": 1, "plaid": 2, "rule": 3, "user": 4}


def _rank(source: str | None) -> int:
    return SOURCE_RANK.get(source or "", 0)


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
        if _rank(txn.category_source) > _rank(auto.value):
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
        if self._user_id is None and not self._s.info.get("household_wide"):
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
