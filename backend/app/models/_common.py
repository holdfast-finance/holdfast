"""Shared column types and helpers for the ORM models.

Conventions (Phase 1 contract):

- PKs are ``uuid DEFAULT gen_random_uuid()``. Timestamps are ``timestamptz DEFAULT now()``.
- Money is ``NUMERIC(14,2)``, quantities ``NUMERIC(28,10)``, prices ``NUMERIC(18,8)``. All map to
  ``Decimal``; never float.
- Enum-like columns are ``text`` + ``CHECK (col IN (...))``. The allowed values live here as
  tuples so API schemas can reuse them instead of retyping the strings.
- Every household-scoped table carries ``household_id`` (FK ``household`` ON DELETE CASCADE,
  indexed). Child tables also carry it (denormalized) and point at their parent with a composite
  FK ``(parent_id, household_id) -> parent(id, household_id)``, so a child row can never belong to
  a different household than its parent. Parents expose ``UNIQUE (id, household_id)`` for this.
- Models define no ORM relationships on purpose: lazy loading is a footgun under asyncio, and
  explicit queries keep RLS behaviour obvious.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import mapped_column

UUIDPK = Annotated[
    uuid.UUID, mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
]
CreatedAt = Annotated[datetime, mapped_column(server_default=func.now())]
HouseholdId = Annotated[
    uuid.UUID, mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
]
Money = Annotated[Decimal, mapped_column(Numeric(14, 2))]
Quantity = Annotated[Decimal, mapped_column(Numeric(28, 10))]
Price = Annotated[Decimal, mapped_column(Numeric(18, 8))]
# Fractions in [0, 1] such as confidence scores and alert thresholds (0.8000 = 80%). Not money.
# Every Ratio column carries ``check_ratio``.
Ratio = Annotated[Decimal, mapped_column(Numeric(5, 4))]
# Percentages such as APR, stored as the percent itself (24.9900 = 24.99%), *not* a fraction like
# Ratio. Not money.
Rate = Annotated[Decimal, mapped_column(Numeric(7, 4))]

# --------------------------------------------------------------------------- enum values
FILING_ROLES = ("primary", "spouse", "dependent")
USER_ROLES = ("owner", "member")
BANK_PROVIDERS = ("plaid", "simplefin")
BANK_ENVS = ("sandbox", "production")
ACCOUNT_SOURCES = ("plaid", "simplefin", "import", "manual")
ACCOUNT_TYPES = ("depository", "credit", "loan", "investment", "other")
TAX_TREATMENTS = ("taxable", "traditional", "roth", "hsa")
ACCOUNT_VISIBILITIES = ("private", "shared", "summary_only")
TRANSACTION_SOURCES = ("plaid", "simplefin", "import", "manual")
CATEGORY_SOURCES = ("rule", "plaid", "ml", "user")
CATEGORY_KINDS = ("income", "expense", "transfer", "savings", "excluded")
CATEGORY_ORIGINS = ("system", "user")
CREATED_BY = ("user", "agent")
INVESTMENT_TXN_TYPES = ("buy", "sell", "cancel", "cash", "fee", "transfer")
BUDGET_PERIODS = ("monthly", "biweekly", "annual")
BUDGET_METHODS = ("category", "envelope", "zero_based")
CREDIT_BUREAUS = ("equifax", "experian", "transunion")
INQUIRY_KINDS = ("hard", "soft")
TAX_YEAR_STATUSES = ("open", "prepared", "filed")
TAX_SOURCE_TYPES = ("document", "transaction", "manual", "agent")
CONVERSATION_KINDS = ("chat", "monthly_review", "year_end")
MESSAGE_ROLES = ("system", "user", "assistant", "tool")
FINDING_STATUSES = ("open", "accepted", "dismissed", "done")
FINDING_SEVERITIES = ("info", "low", "medium", "high")
# ``pending_setup``: linked, token sealed, nothing ingested until the owner maps its accounts
# (ADR 0033). Every scheduled job skips it.
BANK_CONNECTION_STATUSES = ("active", "needs_relink", "error", "disconnected", "pending_setup")
LIABILITY_KINDS = ("credit", "student", "mortgage", "auto", "personal", "other")
FILING_STATUSES = ("single", "mfj", "mfs", "hoh", "qss")
DOCUMENT_PARSE_STATUSES = ("pending", "parsed", "needs_review", "failed")
JOB_RUN_STATUSES = ("queued", "running", "retrying", "succeeded", "failed")
# Import and sync runs (ADR 0017).
IMPORT_RUN_STATUSES = ("queued", "running", "succeeded", "failed")
# pending -> reconciled | needs_review; needs_review -> accepted (attributed). Enforced by the
# ``import_run_guard`` trigger; ``accepted`` is never written on insert.
RECONCILE_STATUSES = ("pending", "reconciled", "needs_review", "accepted")
SYNC_RUN_STATUSES = ("running", "succeeded", "failed")
# Connectors (Phase 2, ADRs 0033 and 0034).
# What happens to one connector account: waiting for the owner, landing in an account, or never.
CONNECTOR_LINK_STATES = ("pending", "mapped", "ignored")
# The Plaid slot ledger. Only ``abandoned`` stops counting against the client's 10 Items.
PLAID_SLOT_STATES = ("reserved", "linked", "abandoned", "unknown")
# 16 lowercase hex characters of SHA-256 of a Plaid client id: tells clients apart, reveals none.
CLIENT_FP_PATTERN = "^[0-9a-f]{16}$"
# A short machine code (a connector status such as ``con.auth``, a ledger flag such as
# ``second_public_token``), never a message: no spaces or quotes, so free text can't be stored.
CODE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
CODE_MAX = 64
# Statement reconciliation tolerance in currency units: |closing - (opening + sum)| <= 0.01.
RECONCILE_TOLERANCE_SQL = "0.01"
# An exception class name (optionally dotted), never a message: no spaces, quotes or colons, so
# text that might carry a token or account number can't be stored as an "error class".
ERROR_CLASS_PATTERN = r"^[A-Za-z_][A-Za-z0-9_.]*$"
ERROR_CLASS_MAX = 200
# A bank-profile id such as ``first_example_bank.checking`` (``profiles/*.yaml``).
PROFILE_ID_PATTERN = r"^[a-z0-9][a-z0-9_.-]*$"
PROFILE_ID_MAX = 100
# A document's location *relative to* DOCUMENT_STORE: slash-separated segments that each start
# with a letter or digit. No leading slash, no ``.`` / ``..`` segments, no backslashes, so a stored
# path can never point outside the store.
STORAGE_PATH_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$"
STORAGE_PATH_MAX = 512
# Lower-case hex SHA-256 (document hashes and import fingerprints).
SHA256_HEX_PATTERN = "^[0-9a-f]{64}$"

# Job framework (ADR 0016). Shared by the ``job_run`` CHECKs and the Python-side validation, so
# both reject the same strings. POSIX and Python ``re`` read these identically.
# ``<area>.<action>``: lowercase, at least one dot (so it can never shadow a cron like heartbeat).
JOB_NAME_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
JOB_NAME_MAX = 100
# Caller-chosen dedupe key, e.g. ``sync:<item-uuid>:2026-11-20T06``. No spaces, quotes or slashes,
# so a token or URL can't be passed off as a key by accident.
IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9:._-]*$"
IDEMPOTENCY_KEY_MAX = 200
# Canonical lowercase UUID text, as ``str(uuid.UUID)`` renders it.
UUID_TEXT_PATTERN = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
DOCUMENT_KINDS = (
    "statement",
    "w2",
    "1099-int",
    "1099-div",
    "1099-b",
    "1099-r",
    "1099-nec",
    "1099-misc",
    "1099-g",
    "1099-k",
    "1099-sa",
    "1098",
    "1098-e",
    "1098-t",
    "5498",
    "5498-sa",
    "1095-a",
    "k-1",
    "credit_report",
    "receipt",
    "export",
    "other",
)


def is_valid_storage_path(value: str) -> bool:
    """Python twin of ``ck_document_storage_path_relative``: a path inside DOCUMENT_STORE."""
    return len(value) <= STORAGE_PATH_MAX and re.fullmatch(STORAGE_PATH_PATTERN, value) is not None


def check_error_class(column: str = "error_class") -> CheckConstraint:
    """The column holds an exception class name or NULL, never a message (hard rule 3)."""
    return CheckConstraint(
        f"{column} IS NULL OR (length({column}) <= {ERROR_CLASS_MAX}"
        f" AND {column} ~ '{ERROR_CLASS_PATTERN}')",
        name=f"{column}_is_class_name",
    )


def check_code(column: str) -> CheckConstraint:
    """The column holds a short machine code or NULL (``CODE_PATTERN``), never free text."""
    return CheckConstraint(
        f"{column} IS NULL OR (length({column}) <= {CODE_MAX} AND {column} ~ '{CODE_PATTERN}')",
        name=f"{column}_is_code",
    )


def check_in(column: str, values: tuple[str, ...], *, nullable: bool = False) -> CheckConstraint:
    """``CHECK (column IN (...))`` named ``ck_<table>_<column>``.

    ``values`` are module constants above, never user input.
    """
    allowed = ", ".join(f"'{v}'" for v in values)
    expr = f"{column} IN ({allowed})"
    if nullable:
        expr = f"{column} IS NULL OR {expr}"
    return CheckConstraint(expr, name=column)


def parent_key() -> UniqueConstraint:
    """``UNIQUE (id, household_id)``: the target of child tables' composite FKs."""
    return UniqueConstraint("id", "household_id")


def child_fk(column: str, parent: str, *, ondelete: str = "CASCADE") -> ForeignKeyConstraint:
    """Composite FK ``(column, household_id) -> parent(id, household_id)``.

    ``ondelete="SET NULL"`` becomes ``SET NULL (column)`` (Postgres 15+): only the reference is
    cleared, never the child's ``household_id``.
    """
    action = f"SET NULL ({column})" if ondelete == "SET NULL" else ondelete
    return ForeignKeyConstraint(
        [column, "household_id"],
        [f"{parent}.id", f"{parent}.household_id"],
        ondelete=action,
    )


# A string whose digits (after stripping everything else) number at most four: a mask, never a
# full account or card number. ``4111-1111-1111-1111`` fails, ``XXXX-1234`` passes.
MASK_SQL = "regexp_replace({col}, '\\D', '', 'g') ~ '^\\d{{0,4}}$'"

# An SSN-shaped number: nine digits as ``123-45-6789``, ``123 45 6789`` or ``123456789``, not
# embedded in a longer digit run. The back-reference makes both separators match, so ZIP+4
# (``12345-6789``) and 10-digit phone numbers pass. Masked forms (``XXX-XX-6789``) pass too.
SSN_PATTERN = "(^|[^0-9])[0-9]{3}([- ]?)[0-9]{2}\\2[0-9]{4}([^0-9]|$)"


def check_masked(column: str) -> CheckConstraint:
    """Reject anything that looks like a full account number: at most four digits in total.

    Masks like ``0000`` or ``XXXX-1234`` pass. Named ``ck_<table>_<column>_masked``.
    """
    return CheckConstraint(
        f"{column} IS NULL OR {MASK_SQL.format(col=column)}", name=f"{column}_masked"
    )


def check_no_ssn(expr: str, name: str) -> CheckConstraint:
    """Reject SSN-shaped values in free text (``expr`` may be a cast such as ``col::text``)."""
    return CheckConstraint(f"{expr} IS NULL OR {expr} !~ '{SSN_PATTERN}'", name=name)


def is_valid_mask(value: str | None) -> bool:
    """Python twin of the mask CHECK, for API validation before the database rejects it."""
    return value is None or len(re.sub(r"\D", "", value)) <= 4


def check_ratio(column: str) -> CheckConstraint:
    """``CHECK (column BETWEEN 0 AND 1)`` for Ratio fractions. Named ``ck_<table>_<col>_ratio``."""
    return CheckConstraint(f"{column} IS NULL OR {column} BETWEEN 0 AND 1", name=f"{column}_ratio")


def check_non_negative(column: str) -> CheckConstraint:
    """``CHECK (column >= 0)`` for magnitudes. Named ``ck_<table>_<column>_non_negative``."""
    return CheckConstraint(f"{column} IS NULL OR {column} >= 0", name=f"{column}_non_negative")
