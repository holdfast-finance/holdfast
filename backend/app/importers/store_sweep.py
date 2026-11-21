"""Remove orphaned statement files from the document store.

The upload route never deletes a stored original in the request path (it is named by hash and
another request may be using it), so failed imports leave files behind. This sweep removes:

- ``statements/<household uuid>/<sha>.pdf`` with no ``document`` row pointing at it, and
- stale ``.upload-*`` temp files anywhere under ``statements/``,

both only once older than ``min_age`` so an upload in flight is never touched. It deliberately
ignores everything else under the store, including files from the earlier flat layout
(``statements/<sha>.pdf``) and anything loaded by other scripts: only the layout this route writes
is swept. It is a background-job helper (it reads every household), not a request path.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db_context import scoped_session
from app.models import Document

STORE_SUBDIR = "statements"
TEMP_PREFIX = ".upload-"
DEFAULT_MIN_AGE = timedelta(hours=1)


@dataclass(frozen=True)
class SweepResult:
    orphans_removed: int
    temps_removed: int


def _old(path: Path, cutoff_ns: int) -> bool:
    try:
        return path.stat().st_mtime_ns < cutoff_ns
    except OSError:
        return False


async def sweep_store(
    factory: async_sessionmaker[AsyncSession],
    document_store: Path,
    *,
    min_age: timedelta = DEFAULT_MIN_AGE,
    now_ns: int | None = None,
) -> SweepResult:
    root = document_store / STORE_SUBDIR
    if not root.is_dir():
        return SweepResult(0, 0)
    cutoff = (time.time_ns() if now_ns is None else now_ns) - (
        min_age // timedelta(microseconds=1)
    ) * 1000
    orphans = temps = 0
    for temp in root.glob(f"{TEMP_PREFIX}*"):
        if temp.is_file() and _old(temp, cutoff):
            temp.unlink(missing_ok=True)
            temps += 1
    for directory in root.iterdir():
        try:
            household_id = uuid.UUID(directory.name)
        except ValueError:
            continue  # not this route's layout
        if not directory.is_dir() or directory.is_symlink():
            continue
        for temp in directory.glob(f"{TEMP_PREFIX}*"):
            if temp.is_file() and _old(temp, cutoff):
                temp.unlink(missing_ok=True)
                temps += 1
        candidates = {
            f"{STORE_SUBDIR}/{directory.name}/{p.name}": p
            for p in directory.glob("*.pdf")
            if not p.name.startswith(".") and p.is_file() and _old(p, cutoff)
        }
        if not candidates:
            continue
        async with scoped_session(factory, household_id, None, household_wide=True) as s:
            known = set(
                (
                    await s.execute(
                        select(Document.storage_path).where(
                            Document.storage_path.in_(list(candidates))
                        )
                    )
                )
                .scalars()
                .all()
            )
        for relative, path in candidates.items():
            if relative not in known:
                path.unlink(missing_ok=True)
                orphans += 1
    return SweepResult(orphans, temps)
