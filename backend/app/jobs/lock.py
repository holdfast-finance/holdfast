"""Per-household job lock: a Postgres session-level advisory lock (ADR 0016).

At most one job runs per household at a time, across every worker process. The lock lives on a
dedicated pooled connection for the whole run and is released explicitly; if the worker dies, the
connection drops and Postgres releases it, so there is no TTL to tune and no stale lock to reap.
Advisory locks need no table grants, and the key is not household data, so RLS doesn't apply.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# First key of the two-int advisory lock: "HFJB". Keeps job locks apart from any other advisory
# lock use (Alembic, future features), which must pick a different namespace.
LOCK_NAMESPACE = 0x48464A42


def lock_key(household_id: uuid.UUID) -> int:
    """Second key: the household UUID's first 32 bits (random for v4 ids). A collision between
    two households only serializes their jobs; it never mixes their data."""
    return int.from_bytes(household_id.bytes[:4], "big", signed=True)


_TRY_LOCK = text("SELECT pg_try_advisory_lock(:ns, :key)")
_UNLOCK = text("SELECT pg_advisory_unlock(:ns, :key)")


@asynccontextmanager
async def household_lock(engine: AsyncEngine, household_id: uuid.UUID) -> AsyncIterator[bool]:
    """Try the household's lock without waiting. Yields whether it was acquired.

    The connection stays checked out until the block exits, then the lock is released. If the
    release fails the connection is invalidated (closed), which releases it server-side.
    """
    params = {"ns": LOCK_NAMESPACE, "key": lock_key(household_id)}
    async with engine.connect() as conn:
        acquired = bool((await conn.execute(_TRY_LOCK, params)).scalar_one())
        await conn.commit()  # the lock is session-level: it outlives this transaction
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    released = bool((await conn.execute(_UNLOCK, params)).scalar_one())
                    await conn.commit()
                except BaseException:
                    await conn.invalidate()
                    raise
                if not released:  # pragma: no cover - would mean the session lost the lock
                    await conn.invalidate()
