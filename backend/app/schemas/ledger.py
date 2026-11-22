"""Read schemas for accounts and transactions (U1a). Amounts are decimal strings (hard rule 1)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from pydantic import Field

from app.schemas.common import DecimalStr, Model, MoneyStr

AccountVisibility = Literal["private", "shared", "summary_only"]
AccountType = Literal["depository", "credit", "loan", "investment", "other"]
AccountSource = Literal["plaid", "simplefin", "import", "manual"]
ReconcileStatus = Literal["pending", "reconciled", "needs_review", "accepted"]
Direction = Literal["inflow", "outflow"]
CategorySource = Literal["user", "rule", "plaid", "ml"]


class AccountOut(Model):
    id: uuid.UUID
    name: str
    type: AccountType
    source: AccountSource
    # The linked institution's name, when the account came through a bank connection.
    institution_name: str | None
    mask: str | None  # at most the last four digits, never a full number
    currency: str
    visibility: AccountVisibility
    is_hidden: bool
    # Live rows only (superseded rows excluded), so these match what the transaction list shows.
    transaction_count: int
    earliest_posted_date: date | None
    latest_posted_date: date | None
    # The newest balance_snapshot, as reported (assets positive, owed negative). Null when no
    # snapshot exists; statement imports do not write one yet, so nothing is derived or guessed.
    latest_balance: MoneyStr | None
    latest_balance_as_of: date | None


class AccountList(Model):
    items: list[AccountOut]


class TransactionOut(Model):
    id: uuid.UUID
    account_id: uuid.UUID
    posted_date: date
    description: str
    amount: MoneyStr  # outflow negative, inflow positive
    currency: str
    is_pending: bool
    is_transfer: bool
    # Reconciliation of the import run that created the row; null for rows not from an import.
    import_reconcile_status: ReconcileStatus | None
    needs_review: bool
    # Derived on read from the merchant name or description (ADR 0026); not stored.
    merchant_key: str | None
    category_id: uuid.UUID | None
    category_source: CategorySource | None
    has_splits: bool = Field(
        description="True when the row has split parts. Category filters and summaries use the "
        "parent row's category only; a split parent is not matched by its parts."
    )
    # Whether this reader may change the row: the account has no owner, or the reader owns it.
    # Never derived from `visibility`. False for a principal with no person.
    can_edit: bool


class CategoryChoiceIn(Model):
    # Null clears the category as a choice (source stays "user"); release hands it back instead.
    category_id: uuid.UUID | None


class TransactionPage(Model):
    items: list[TransactionOut]
    next_cursor: str | None


class CurrencyTotals(Model):
    currency: str
    inflow: DecimalStr
    outflow: DecimalStr  # negative or zero
    net: DecimalStr
    count: int


class TransactionSummary(Model):
    # One entry per currency among the matching rows; currencies are never added together.
    totals: list[CurrencyTotals]
