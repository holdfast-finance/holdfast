"""The Plaid slot ledger and the sandbox/real-data coexistence rule (ADR 0034, ADR 0033 section 7).

Everything here runs on a caller-supplied session, so each check sits in the writing transaction.
The coexistence checks take one per-household advisory lock first, shared by link-token,
exchange and ``ImportService``.

"Real data" means any account, import run, manual entry or production connection. Accounts that
belong to a sandbox connection are not real data. Visibility follows the caller's row-level
security: another person's private account is not visible to the owner's session. The
``import_run`` read policy follows document and account visibility (migration 0005), so
``has_real_data()`` can miss another person's private *imported* accounts as well as manual ones
(M1 adds its own manual-account check). Connections are household-wide, so connected data is
never missed.

TODO(next-migration): add a SECURITY DEFINER ``household_has_real_data(hid)`` that returns a
boolean only, like ``plaid_slot_count``, and call it here so the answer no longer depends on the
caller's visibility.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.plaid.link import RESERVATION_TTL_HOURS
from app.models import Account, BankConnection, ImportRun, PlaidLinkSlot
from app.services.errors import ApiError


def client_fp(client_id: str) -> str:
    """16 lowercase hex characters of SHA-256 of the Plaid client id (ADR 0034 section 1)."""
    return hashlib.sha256(client_id.strip().encode("utf-8")).hexdigest()[:16]


async def lock_household(s: AsyncSession, household_id: uuid.UUID) -> None:
    """Serialize link-token, exchange and imports for one household until commit."""
    await s.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"holdfast:coexist:{household_id}"},
    )


async def slot_count(s: AsyncSession, fp: str, env: str) -> int:
    """Slots a client has used in ``env``, across households (a number only)."""
    value = await s.scalar(text("SELECT plaid_slot_count(:fp, :env)"), {"fp": fp, "env": env})
    return int(value or 0)


async def sweep_stale(s: AsyncSession, household_id: uuid.UUID) -> int:
    """Move this household's ``reserved`` rows older than the token lifetime to ``unknown``,
    and drop their sealed token in the same UPDATE. They still count."""
    cutoff = datetime.now(UTC) - timedelta(hours=RESERVATION_TTL_HOURS)
    result = await s.execute(
        update(PlaidLinkSlot)
        .where(
            PlaidLinkSlot.household_id == household_id,
            PlaidLinkSlot.state == "reserved",
            PlaidLinkSlot.created_at < cutoff,
        )
        .values(state="unknown", link_token_enc=None, resolved_at=func.now())
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def has_sandbox_connection(s: AsyncSession) -> bool:
    return bool(await s.scalar(select(exists().where(BankConnection.env == "sandbox"))))


async def has_real_data(s: AsyncSession) -> bool:
    production = exists().where(BankConnection.env == "production")
    imports = exists().where(ImportRun.id.is_not(None))
    own_accounts = exists().where(
        or_(
            Account.bank_connection_id.is_(None),
            Account.bank_connection_id.in_(
                select(BankConnection.id).where(BankConnection.env == "production")
            ),
        )
    )
    return bool(await s.scalar(select(or_(production, imports, own_accounts))))


async def assert_can_link(s: AsyncSession, env: str) -> None:
    """Refuse a sandbox link on real data and a production link beside a sandbox connection."""
    if env == "sandbox":
        if await has_real_data(s):
            raise ApiError(
                "sandbox_on_real_data",
                "This household has real data. Sandbox links need an empty household.",
            )
    elif await has_sandbox_connection(s):
        raise ApiError(
            "sandbox_connection_present",
            "Remove the sandbox connection first, then link a real bank.",
        )


async def assert_can_import(s: AsyncSession, household_id: uuid.UUID) -> None:
    """Statement and CSV uploads are refused beside a sandbox connection."""
    await lock_household(s, household_id)
    if await has_sandbox_connection(s):
        raise ApiError(
            "sandbox_connection_present",
            "Remove the sandbox connection first, then import real statements.",
        )
