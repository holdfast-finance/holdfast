"""Fernet key rotation: ``python -m app.security.rotation`` (``make rotate-keys``). ADR 0015.

Re-seals exactly three columns under the primary ``FERNET_KEY`` entry. Each row is its own
transaction: ``SELECT ... FOR UPDATE``, then a compare-and-swap ``UPDATE`` that only lands while
the column still equals the bytes just read. A failed swap is re-read once under a fresh lock; a
second failure is a conflict. Plaintext from a lost snapshot is never written.

The command runs as ``holdfast_app`` (no superuser, no BYPASSRLS). It walks households listed in
``app_user`` (readable without a household context), sets the RLS context per household, and binds
``household_wide=True`` because a background job must see every person's rows. It logs and audits
counts only. It exits non-zero unless every row is on the primary kid (failed, conflicted and the
final residual scan are all zero).
"""

# ruff: noqa: S608  (identifiers come from the constant TARGETS table; values are bound)
from __future__ import annotations

import asyncio
import sys
import uuid
from collections.abc import Awaitable, Callable, Collection
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.db import create_engine, create_session_factory
from app.db_context import scoped_session, unscoped_session
from app.logging import configure_logging, get_logger
from app.security import crypto
from app.services import audit

log = get_logger(__name__)

# Test seam: runs after the row is read and sealed, before the swap, on the locked transaction.
Hook = Callable[[int, str, AsyncSession], Awaitable[None]]


@dataclass(frozen=True)
class Target:
    table: str
    column: str
    pk: tuple[str, ...]
    household_scoped: bool
    has_updated_at: bool


# The whole rotator list. A new ciphertext column needs an ADR first.
TARGETS: tuple[Target, ...] = (
    Target("household_setting", "value_enc", ("household_id", "key"), True, True),
    Target("bank_connection", "access_token_enc", ("id",), True, True),
    Target("app_user", "totp_secret_enc", ("id",), False, False),
)


@dataclass
class Counts:
    rotated: int = 0
    already_current: int = 0
    skipped_null: int = 0
    failed: int = 0
    conflicted: int = 0
    gone: int = 0
    residual: int = 0  # rows still not on the primary kid after the run

    @property
    def clean(self) -> bool:
        return self.failed == 0 and self.conflicted == 0 and self.residual == 0


def _where(t: Target) -> str:
    return " AND ".join(f'"{c}" = :p_{c}' for c in t.pk)


def _order(t: Target) -> str:
    return ", ".join(f'"{c}"' for c in t.pk)


async def _rotate_row(
    s: AsyncSession,
    t: Target,
    ident: dict[str, Any],
    ring: crypto.KeyRing,
    counts: Counts,
    hook: Hook | None,
) -> None:
    params = {f"p_{k}": v for k, v in ident.items()}
    select_sql = text(f'SELECT "{t.column}" FROM "{t.table}" WHERE {_where(t)} FOR UPDATE')
    touch = ", updated_at = now()" if t.has_updated_at else ""
    swap_sql = text(
        f'UPDATE "{t.table}" SET "{t.column}" = :new{touch} '
        f'WHERE {_where(t)} AND "{t.column}" = :old'
    )
    for attempt in (1, 2):
        row = (await s.execute(select_sql, params)).first()
        if row is None:
            counts.gone += 1
            await s.rollback()
            return
        if row[0] is None:
            counts.skipped_null += 1
            await s.rollback()
            return
        old = bytes(row[0])
        if crypto.is_current(old, ring):
            counts.already_current += 1
            await s.rollback()
            return
        try:
            new = crypto.seal_with(crypto.open_with(old, ring), ring)
        except crypto.DecryptionError:
            counts.failed += 1
            await s.rollback()
            return
        if hook is not None:
            await hook(attempt, t.table, s)
        result = await s.execute(swap_sql, {**params, "new": new, "old": old})
        if result.rowcount == 1:  # type: ignore[attr-defined]
            await s.commit()
            counts.rotated += 1
            return
        await s.rollback()
    counts.conflicted += 1


async def _pks(
    s: AsyncSession, t: Target, only: Collection[uuid.UUID] | None
) -> list[dict[str, Any]]:
    cols = ", ".join(f'"{c}"' for c in t.pk)
    scope = ""
    params: dict[str, Any] = {}
    if only is not None and t.table == "app_user":
        scope = ' WHERE "household_id" = ANY(:hids)'
        params["hids"] = list(only)
    rows = (
        await s.execute(text(f'SELECT {cols} FROM "{t.table}"{scope} ORDER BY {_order(t)}'), params)
    ).all()
    await s.rollback()
    return [dict(zip(t.pk, r, strict=True)) for r in rows]


async def _household_ids(
    factory: async_sessionmaker[AsyncSession], only: Collection[uuid.UUID] | None
) -> list[uuid.UUID]:
    async with unscoped_session(factory) as s:
        rows = await s.execute(text("SELECT DISTINCT household_id FROM app_user ORDER BY 1"))
        return [r[0] for r in rows if only is None or r[0] in only]


async def _walk(
    factory: async_sessionmaker[AsyncSession],
    handle: Callable[[AsyncSession, Target, dict[str, Any]], Awaitable[None]],
    only: Collection[uuid.UUID] | None,
) -> None:
    """Call ``handle`` for every row of every target, one household context at a time."""
    for t in TARGETS:
        if not t.household_scoped:
            async with unscoped_session(factory) as s:
                for ident in await _pks(s, t, only):
                    await handle(s, t, ident)
    households = await _household_ids(factory, only)
    for hid in households:
        async with scoped_session(factory, hid, None, household_wide=True) as s:
            for t in TARGETS:
                if t.household_scoped:
                    for ident in await _pks(s, t, only):
                        await handle(s, t, ident)


async def rotate(
    factory: async_sessionmaker[AsyncSession],
    ring: crypto.KeyRing,
    *,
    hook: Hook | None = None,
    only: Collection[uuid.UUID] | None = None,
) -> Counts:
    """Rotate every row (``only`` limits to some households: tests, never the CLI)."""
    counts = Counts()

    async def rotate_one(s: AsyncSession, t: Target, ident: dict[str, Any]) -> None:
        try:
            await _rotate_row(s, t, ident, ring, counts, hook)
        except SQLAlchemyError as exc:
            await s.rollback()
            counts.failed += 1
            log.error("keys.rotate.row_error", table=t.table, error=type(exc).__name__)

    await _walk(factory, rotate_one, only)

    async def scan(s: AsyncSession, t: Target, ident: dict[str, Any]) -> None:
        params = {f"p_{k}": v for k, v in ident.items()}
        row = (
            await s.execute(text(f'SELECT "{t.column}" FROM "{t.table}" WHERE {_where(t)}'), params)
        ).first()
        await s.rollback()
        if row is not None and row[0] is not None and not crypto.is_current(bytes(row[0]), ring):
            counts.residual += 1

    # Catches rows an old-key writer added mid-run and rows a failed row left behind.
    await _walk(factory, scan, only)
    return counts


async def _run() -> int:
    settings = get_settings()
    try:
        ring = crypto.load_ring(settings.fernet_key)
    except crypto.CryptoError as exc:
        log.error("keys.rotate.bad_ring", error=str(exc))
        return 2
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        counts = await rotate(factory, ring)
        detail = asdict(counts)
        await audit.record(
            factory,
            "keys.rotate",
            household_id=None,
            outcome="success" if counts.clean else "failure",
            detail=detail,
        )
    finally:
        await engine.dispose()
    log.info("keys.rotate.done", **detail)
    if counts.clean:
        print(
            f"rotated={counts.rotated} already_current={counts.already_current} "
            f"skipped_null={counts.skipped_null} failed=0 conflicted=0 residual=0\n"
            "Run again: when it reports rotated=0 failed=0, remove retired keys from FERNET_KEY."
        )
        return 0
    print(
        f"NOT CLEAN rotated={counts.rotated} failed={counts.failed} "
        f"conflicted={counts.conflicted} residual={counts.residual}\n"
        "Keep every key in FERNET_KEY. Fix the failures and run again.",
        file=sys.stderr,
    )
    return 1


def main() -> None:
    configure_logging()
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
