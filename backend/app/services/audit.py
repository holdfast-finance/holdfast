"""Audit log writes and the owner's audit page.

``detail`` never carries tokens, codes, keys, passwords or emails of failed logins. The outcome
(``success`` / ``failure``) lives in ``detail.outcome``.

Writes use the ORM (no RETURNING: pre-auth rows fail the SELECT policy, ADR 0008). A row with a
household id must be written from a session scoped to that household (RLS WITH CHECK).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db_context import scoped_session, unscoped_session
from app.models import AppUser, AuditLog, Person
from app.schemas.household import AuditEntry, AuditPage
from app.security.client_ip import IPAddress

Outcome = Literal["success", "failure"]
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def add(
    s: AsyncSession,
    action: str,
    *,
    household_id: uuid.UUID | None,
    user_id: uuid.UUID | None = None,
    ip: IPAddress | None = None,
    outcome: Outcome = "success",
    entity: str | None = None,
    entity_id: uuid.UUID | str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Queue an audit row on ``s`` (committed with the caller's unit of work)."""
    s.add(
        AuditLog(
            id=uuid.uuid4(),
            household_id=household_id,
            user_id=user_id,
            action=action,
            entity=entity,
            entity_id=None if entity_id is None else str(entity_id),
            detail={**(detail or {}), "outcome": outcome},
            ip=ip,
        )
    )


async def record(
    factory: async_sessionmaker[AsyncSession],
    action: str,
    *,
    household_id: uuid.UUID | None,
    user_id: uuid.UUID | None = None,
    ip: IPAddress | None = None,
    outcome: Outcome = "success",
    entity: str | None = None,
    entity_id: uuid.UUID | str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Write one audit row in its own transaction (survives the caller's rollback)."""
    kwargs: dict[str, Any] = {
        "household_id": household_id,
        "user_id": user_id,
        "ip": ip,
        "outcome": outcome,
        "entity": entity,
        "entity_id": entity_id,
        "detail": detail,
    }
    if household_id is None:
        async with unscoped_session(factory) as s:
            add(s, action, **kwargs)
    else:
        async with scoped_session(factory, household_id, None) as s:
            add(s, action, **kwargs)


def _cursor(at: datetime, row_id: uuid.UUID) -> str:
    return f"{at.isoformat()}|{row_id}"


def _parse_cursor(value: str) -> tuple[datetime, uuid.UUID] | None:
    at, _, rid = value.partition("|")
    try:
        return datetime.fromisoformat(at), uuid.UUID(rid)
    except ValueError:
        return None


async def page(s: AsyncSession, *, limit: int, before: str | None) -> AuditPage:
    """Newest first. RLS limits rows to the session's household."""
    limit = max(1, min(limit, MAX_LIMIT))
    stmt = (
        select(AuditLog, Person.display_name)
        .outerjoin(AppUser, AppUser.id == AuditLog.user_id)
        .outerjoin(Person, Person.id == AppUser.person_id)
        .order_by(AuditLog.at.desc(), AuditLog.id.desc())
        .limit(limit + 1)
    )
    if before:
        cursor = _parse_cursor(before)
        if cursor is not None:
            at, rid = cursor
            stmt = stmt.where(or_(AuditLog.at < at, and_(AuditLog.at == at, AuditLog.id < rid)))
    rows = (await s.execute(stmt)).all()
    more = len(rows) > limit
    rows = rows[:limit]
    items = [
        AuditEntry(
            id=log.id,
            created_at=log.at,
            action=log.action,
            actor_display_name=name,
            ip=None if log.ip is None else str(log.ip),
            outcome="failure" if (log.detail or {}).get("outcome") == "failure" else "success",
        )
        for log, name in rows
    ]
    last = rows[-1][0] if rows else None
    return AuditPage(items=items, next_before=_cursor(last.at, last.id) if more and last else None)
