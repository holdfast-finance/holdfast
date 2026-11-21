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
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, case, func, select, tuple_
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.db_context import scoped_session
from app.models import (
    Account,
    BalanceSnapshot,
    BankConnection,
    ImportRun,
    Institution,
    Transaction,
)
from app.schemas.ledger import (
    AccountList,
    AccountOut,
    CurrencyTotals,
    Direction,
    TransactionOut,
    TransactionPage,
    TransactionSummary,
)
from app.services.context import RequestContext
from app.services.errors import ApiError
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
    return conds


def validate_filters(f: TransactionFilters) -> None:
    if len(f.account_ids) > MAX_ACCOUNT_FILTERS:
        raise ApiError("validation_error", "Too many accounts selected.")
    if f.q is not None and len(f.q) > MAX_QUERY_LENGTH:
        raise ApiError("validation_error", "Keep the search to 200 characters or fewer.")
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

    async def transactions(
        self, f: TransactionFilters, *, limit: int, cursor: str | None
    ) -> TransactionPage:
        validate_filters(f)
        stmt = (
            self._base(
                Transaction.id,
                Transaction.account_id,
                Transaction.posted_date,
                Transaction.description_raw,
                Transaction.amount,
                Account.currency,
                Transaction.is_pending,
                Transaction.is_transfer,
                ImportRun.reconcile_status,
            )
            .outerjoin(ImportRun, ImportRun.id == Transaction.import_run_id)
            .where(*_conditions(f))
        )
        if cursor:
            stmt = stmt.where(
                tuple_(Transaction.posted_date, Transaction.id) < tuple_(*decode_cursor(cursor))
            )
        stmt = stmt.order_by(Transaction.posted_date.desc(), Transaction.id.desc()).limit(limit + 1)
        async with self._session() as s:
            rows = (await s.execute(stmt)).all()
        page = rows[:limit]
        items = [
            TransactionOut.model_validate(
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
                }
            )
            for r in page
        ]
        next_cursor = (
            encode_cursor(page[-1][2], page[-1][0]) if len(rows) > limit and page else None
        )
        return TransactionPage(items=items, next_cursor=next_cursor)

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
