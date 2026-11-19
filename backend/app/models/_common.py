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
CATEGORY_KINDS = ("income", "expense", "transfer", "savings")
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
BANK_CONNECTION_STATUSES = ("active", "needs_relink", "error", "disconnected")
LIABILITY_KINDS = ("credit", "student", "mortgage", "auto", "personal", "other")
FILING_STATUSES = ("single", "mfj", "mfs", "hoh", "qss")
DOCUMENT_PARSE_STATUSES = ("pending", "parsed", "needs_review", "failed")
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
