"""Statement import schemas (I5b). Amounts are decimal strings (hard rule 1)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from app.schemas.common import Model, MoneyStr

ReconcileStatus = Literal["pending", "reconciled", "needs_review", "accepted"]
RunStatus = Literal["queued", "running", "succeeded", "failed"]


class ReconcileNumbers(Model):
    """closing - (opening + txn_sum) must be within $0.01 for a run to be ``reconciled``."""

    opening_balance: MoneyStr | None
    closing_balance: MoneyStr | None
    txn_sum: MoneyStr | None
    balance_diff: MoneyStr | None


class ImportRunOut(ReconcileNumbers):
    id: uuid.UUID
    document_id: uuid.UUID
    filename: str  # sanitised at upload; never the client's raw name
    account_id: uuid.UUID | None
    account_name: str | None
    currency: str | None  # the account's currency; null when the run has no account
    status: RunStatus
    reconcile_status: ReconcileStatus
    # Why a needs_review run was flagged (a fixed code such as ``balance_mismatch``; ``unknown``
    # when an older run never stored one). Null for reconciled runs.
    reason: str | None
    period_start: date | None
    period_end: date | None
    rows_parsed: int
    rows_inserted: int
    rows_duplicate: int
    # Exception class name only; the server never sends parser messages (they can hold digits).
    error_class: str | None
    created_at: datetime
    # Set once a person accepted a needs_review run. The accepting user is in the audit log.
    reviewed_at: datetime | None


class ImportRunPage(Model):
    items: list[ImportRunOut]
    next_cursor: str | None


class ImportBlockOut(ReconcileNumbers):
    """One account's block of the uploaded statement."""

    run_id: uuid.UUID
    account_id: uuid.UUID
    account_name: str
    currency: str
    reconcile_status: ReconcileStatus
    period_start: date | None
    period_end: date | None
    rows_parsed: int
    rows_inserted: int
    rows_duplicate: int
    # False: this file was already imported and nothing was written for this block.
    created: bool
    # Why a block that isn't reconciled needs a look (a fixed code such as ``balance_mismatch`` or
    # ``missing_balance``), stored on the run.
    reason: str | None


class ImportBalanceOut(Model):
    """Balances read from a statement that had no transactions. Not stored anywhere yet."""

    account_last4: str | None
    opening_balance: MoneyStr | None
    closing_balance: MoneyStr | None


class ImportResultOut(Model):
    document_id: uuid.UUID
    filename: str
    # False: the file (same SHA-256) was already stored; the response describes the earlier import.
    created: bool
    balances_only: bool
    blocks: list[ImportBlockOut]
    # Only for ``balances_only`` uploads (no transactions, so no run): what the file said.
    balances: list[ImportBalanceOut]
