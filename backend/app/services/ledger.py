"""Read-only views of accounts and transactions under the caller's RLS context (U1a).

Every query runs in ``scoped_session`` with the caller's household and person, so row-level
security enforces tenancy and per-person account visibility; nothing here filters by household.
Superseded rows (ADR 0017) are excluded from every result.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import Select, case, func, select, tuple_
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.db_context import scoped_session
from app.domain.ledger_rules import LedgerRuleError
from app.domain.merchant import merchant_key
from app.models import (
    Account,
    BalanceSnapshot,
    BankConnection,
    ImportRun,
    Institution,
    Transaction,
    TransactionSplit,
)
from app.schemas.ledger import (
    AccountList,
    AccountOut,
    CategorySource,
    CurrencyTotals,
    Direction,
    TransactionOut,
    TransactionPage,
    TransactionSummary,
)
from app.services.context import RequestContext
from app.services.errors import ApiError
from app.services.ledger_service import (
    CategoryNotFoundError,
    LedgerNotFoundError,
    LedgerRetryableError,
    LedgerWriter,
    SupersededTransactionError,
)
from app.services.sessions import Principal

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
MAX_ACCOUNT_FILTERS = 100
MAX_QUERY_LENGTH = 200
_ZERO = Decimal("0")
_CENT = Decimal("0.01")


def _cents(value: Decimal) -> Decimal:
    """Sums of NUMERIC(14,2) are exact at two places; this only fixes the scale of an empty sum."""
    return value.quantize(_CENT)


@dataclass(frozen=True)
class TransactionFilters:
    account_ids: list[uuid.UUID] = field(default_factory=list)
    date_from: date | None = None
    date_to: date | None = None
    q: str | None = None
    direction: Direction | None = None
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    exclude_transfers: bool = False
    category_ids: list[uuid.UUID] = field(default_factory=list)
    uncategorized: bool = False
    category_source: CategorySource | None = None
    transfer: Literal["only", "exclude"] | None = None


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def encode_cursor(posted: date, tx_id: uuid.UUID) -> str:
    raw = json.dumps([posted.isoformat(), str(tx_id)], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_cursor(cursor: str) -> tuple[date, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        posted, tx_id = json.loads(raw)
        if not isinstance(posted, str) or not isinstance(tx_id, str):
            raise TypeError("cursor parts must be strings")
        return date.fromisoformat(posted), uuid.UUID(tx_id)
    except (binascii.Error, ValueError, TypeError, UnicodeDecodeError, OverflowError):
        raise ApiError("validation_error", "That page cursor isn't valid.") from None


def _conditions(f: TransactionFilters) -> list[ColumnElement[bool]]:
    conds: list[ColumnElement[bool]] = [Transaction.superseded_by_id.is_(None)]
    if f.account_ids:
        conds.append(Transaction.account_id.in_(f.account_ids))
    if f.date_from is not None:
        conds.append(Transaction.posted_date >= f.date_from)
    if f.date_to is not None:
        conds.append(Transaction.posted_date <= f.date_to)
    if f.q:
        conds.append(Transaction.description_raw.ilike(f"%{_escape_like(f.q)}%", escape="\\"))
    if f.direction == "inflow":
        conds.append(Transaction.amount > 0)
    elif f.direction == "outflow":
        conds.append(Transaction.amount < 0)
    if f.min_amount is not None:
        conds.append(Transaction.amount >= f.min_amount)
    if f.max_amount is not None:
        conds.append(Transaction.amount <= f.max_amount)
    if f.exclude_transfers:
        conds.append(Transaction.is_transfer.is_(False))
    if f.transfer == "only":
        conds.append(Transaction.is_transfer.is_(True))
    elif f.transfer == "exclude":
        conds.append(Transaction.is_transfer.is_(False))
    if f.category_ids:
        conds.append(Transaction.category_id.in_(f.category_ids))
    if f.uncategorized:
        conds.append(Transaction.category_id.is_(None))
    if f.category_source is not None:
        conds.append(Transaction.category_source == f.category_source)
    return conds


def validate_filters(f: TransactionFilters) -> None:
    if len(f.account_ids) > MAX_ACCOUNT_FILTERS:
        raise ApiError("validation_error", "Too many accounts selected.")
    if f.q is not None and len(f.q) > MAX_QUERY_LENGTH:
        raise ApiError("validation_error", "Keep the search to 200 characters or fewer.")
    if len(f.category_ids) > MAX_ACCOUNT_FILTERS:
        raise ApiError("validation_error", "Too many categories selected.")
    if f.uncategorized and (f.category_ids or f.category_source is not None):
        raise ApiError(
            "validation_error", "Needs-category can't be combined with a category or a source."
        )
    if f.transfer == "only" and f.exclude_transfers:
        raise ApiError("validation_error", "Transfers can't be both only and excluded.")
    if f.date_from and f.date_to and f.date_from > f.date_to:
        raise ApiError("validation_error", "The start date is after the end date.")
    if f.min_amount is not None and f.max_amount is not None and f.min_amount > f.max_amount:
        raise ApiError("validation_error", "The minimum amount is above the maximum.")


class LedgerService:
    def __init__(self, ctx: RequestContext, principal: Principal) -> None:
        self.ctx = ctx
        self.principal = principal

    def _session(self) -> AbstractAsyncContextManager[AsyncSession]:
        return scoped_session(
            self.ctx.factory, self.principal.household_id, self.principal.person_id
        )

    async def accounts(self) -> AccountList:
        live = (
            select(
                Transaction.account_id.label("account_id"),
                func.count().label("n"),
                func.min(Transaction.posted_date).label("first"),
                func.max(Transaction.posted_date).label("last"),
            )
            .where(Transaction.superseded_by_id.is_(None))
            .group_by(Transaction.account_id)
            .subquery()
        )
        newest = (
            select(BalanceSnapshot.account_id, BalanceSnapshot.current, BalanceSnapshot.as_of_date)
            .ext(distinct_on(BalanceSnapshot.account_id))
            .order_by(BalanceSnapshot.account_id, BalanceSnapshot.as_of_date.desc())
            .subquery()
        )
        stmt = (
            select(
                Account,
                Institution.name,
                func.coalesce(live.c.n, 0),
                live.c.first,
                live.c.last,
                newest.c.current,
                newest.c.as_of_date,
            )
            .outerjoin(BankConnection, BankConnection.id == Account.bank_connection_id)
            .outerjoin(Institution, Institution.id == BankConnection.institution_id)
            .outerjoin(live, live.c.account_id == Account.id)
            .outerjoin(newest, newest.c.account_id == Account.id)
            .order_by(Account.name, Account.id)
        )
        async with self._session() as s:
            rows = (await s.execute(stmt)).all()
        return AccountList(
            items=[
                AccountOut.model_validate(
                    {
                        "id": a.id,
                        "name": a.name,
                        "type": a.type,
                        "source": a.source,
                        "institution_name": inst,
                        "mask": a.mask,
                        "currency": a.currency,
                        "visibility": a.visibility,
                        "is_hidden": a.is_hidden,
                        "transaction_count": n,
                        "earliest_posted_date": first,
                        "latest_posted_date": last,
                        "latest_balance": balance,
                        "latest_balance_as_of": as_of if balance is not None else None,
                    }
                )
                for a, inst, n, first, last, balance, as_of in rows
            ]
        )

    def _base(self, *cols: Any) -> Select[Any]:
        return (
            select(*cols)
            .select_from(Transaction)
            .join(Account, Account.id == Transaction.account_id)
        )

    def _row_stmt(self) -> Select[Any]:
        has_splits = (
            select(TransactionSplit.id)
            .where(TransactionSplit.transaction_id == Transaction.id)
            .exists()
        )
        return self._base(
            Transaction.id,
            Transaction.account_id,
            Transaction.posted_date,
            Transaction.description_raw,
            Transaction.amount,
            Account.currency,
            Transaction.is_pending,
            Transaction.is_transfer,
            ImportRun.reconcile_status,
            Transaction.merchant_name,
            Transaction.category_id,
            Transaction.category_source,
            has_splits,
            Account.owner_person_id,
        ).outerjoin(ImportRun, ImportRun.id == Transaction.import_run_id)

    def _can_edit(self, owner_person_id: uuid.UUID | None) -> bool:
        # The exact predicate of the C6a brief: no owner, or the reader is the owner. Never reads
        # `visibility`. A principal with no person can't edit. C3b swaps this for
        # app_account_writable() and adds an equivalence test.
        person = self.principal.person_id
        return person is not None and (owner_person_id is None or owner_person_id == person)

    def _out(self, r: Any) -> TransactionOut:
        return TransactionOut.model_validate(
            {
                "id": r[0],
                "account_id": r[1],
                "posted_date": r[2],
                "description": r[3],
                "amount": r[4],
                "currency": r[5],
                "is_pending": r[6],
                "is_transfer": r[7],
                "import_reconcile_status": r[8],
                "needs_review": r[8] == "needs_review",
                "merchant_key": merchant_key(r[9], r[3]),
                "category_id": r[10],
                "category_source": r[11],
                "has_splits": bool(r[12]),
                "can_edit": self._can_edit(r[13]),
            }
        )

    async def transactions(
        self, f: TransactionFilters, *, limit: int, cursor: str | None
    ) -> TransactionPage:
        validate_filters(f)
        stmt = self._row_stmt().where(*_conditions(f))
        if cursor:
            stmt = stmt.where(
                tuple_(Transaction.posted_date, Transaction.id) < tuple_(*decode_cursor(cursor))
            )
        stmt = stmt.order_by(Transaction.posted_date.desc(), Transaction.id.desc()).limit(limit + 1)
        async with self._session() as s:
            rows = (await s.execute(stmt)).all()
        page = rows[:limit]
        items = [self._out(r) for r in page]
        next_cursor = (
            encode_cursor(page[-1][2], page[-1][0]) if len(rows) > limit and page else None
        )
        return TransactionPage(items=items, next_cursor=next_cursor)

    async def _one(self, s: AsyncSession, transaction_id: uuid.UUID) -> TransactionOut:
        row = (
            await s.execute(self._row_stmt().where(Transaction.id == transaction_id))
        ).one_or_none()
        if row is None:
            raise ApiError("not_found", "That transaction wasn't found.")
        return self._out(row)

    async def get(self, transaction_id: uuid.UUID) -> TransactionOut:
        """One live row the reader can see; a superseded row is history and reads as missing."""
        stmt = self._row_stmt().where(
            Transaction.id == transaction_id, Transaction.superseded_by_id.is_(None)
        )
        async with self._session() as s:
            row = (await s.execute(stmt)).one_or_none()
        if row is None:
            raise ApiError("not_found", "That transaction wasn't found.")
        return self._out(row)

    async def set_category(
        self, transaction_id: uuid.UUID, category_id: uuid.UUID | None
    ) -> TransactionOut:
        return await self._edit(
            transaction_id, lambda w: w.set_category(transaction_id, category_id)
        )

    async def release_category(self, transaction_id: uuid.UUID) -> TransactionOut:
        return await self._edit(transaction_id, lambda w: w.release_category(transaction_id))

    async def _edit(
        self, transaction_id: uuid.UUID, op: Callable[[LedgerWriter], Awaitable[None]]
    ) -> TransactionOut:
        """Run one LedgerWriter edit in the reader's own person-scoped session (never wide).

        A row that is missing, invisible or not writable is one 404 (RLS decides). Money and
        category fields are written only by LedgerWriter (ADR 0011).
        """
        failure: ApiError | None = None
        try:
            async with self._session() as s:
                writer = LedgerWriter(
                    s, self.principal.household_id, user_id=self.principal.user_id, ip=self.ctx.ip
                )
                await op(writer)
                return await self._one(s, transaction_id)
        except SupersededTransactionError:
            failure = ApiError("conflict", "That transaction was replaced and can't be changed.")
        except CategoryNotFoundError:
            failure = ApiError("not_found", "That category wasn't found.")
        except LedgerNotFoundError:
            failure = ApiError("not_found", "That transaction wasn't found.")
        except LedgerRetryableError:
            failure = ApiError("conflict", "That transaction changed. Try again.")
        except LedgerRuleError:
            failure = ApiError("conflict", "That change isn't allowed on this transaction.")
        raise failure

    async def summary(self, f: TransactionFilters) -> TransactionSummary:
        validate_filters(f)
        stmt = (
            self._base(
                Account.currency,
                func.coalesce(func.sum(case((Transaction.amount > 0, Transaction.amount))), _ZERO),
                func.coalesce(func.sum(case((Transaction.amount < 0, Transaction.amount))), _ZERO),
                func.coalesce(func.sum(Transaction.amount), _ZERO),
                func.count(),
            )
            .where(*_conditions(f))
            .group_by(Account.currency)
            .order_by(Account.currency)
        )
        async with self._session() as s:
            rows = (await s.execute(stmt)).all()
        return TransactionSummary(
            totals=[
                CurrencyTotals.model_validate(
                    {
                        "currency": c,
                        "inflow": _cents(i),
                        "outflow": _cents(o),
                        "net": _cents(n),
                        "count": k,
                    }
                )
                for c, i, o, n, k in rows
            ]
        )
