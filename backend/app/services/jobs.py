"""Job runs: enqueue (idempotent) and the ``job_run`` state transitions the worker drives.

ADR 0016. ``job_run`` is the source of truth; arq is the delivery mechanism. Every write here runs
as holdfast_app in the run's household context, so RLS (FORCE) applies to the worker too.

State machine (each transition is a conditional UPDATE, so a stale or duplicate delivery is a
no-op)::

    queued ──claim──> running ──succeed──> succeeded
    retrying ─claim─^    │ └──fail (attempts left)──> retrying
    running (orphan) ─^  └──fail (no attempts left)─> failed

A ``running`` row may be re-claimed only by a runner holding the household lock, and only once
its ``started_at`` is older than the job's timeout plus arq's backstop margin
(``JobSpec.orphan_after``). The lock alone isn't proof: if the lock connection drops mid-run,
Postgres releases the lock while the handler keeps going. By ``orphan_after`` the runner's
timeout and arq's backstop have both cancelled that execution, so the row is an orphan of a dead
worker. Every mark after a claim also matches the attempt number it claimed, so an execution
that lost its row to a re-claim can't overwrite the newer attempt's outcome.

**Enqueue authority.** The worker runs handlers household-wide, so enqueue is the only place
the requester's own rights apply. It takes the *authenticated* household and person, never the
payload's word for them, and checks every referenced id in that person's RLS-scoped session
before anything is recorded or queued: a ``Ref`` the job writes (the default) must pass the
table's UPDATE policy, a ``Ref(..., access="read")`` its SELECT policy.

**Redelivery.** Pushing a run to arq is always safe to repeat: arq drops a push whose
``_job_id`` it still holds, and a second delivery can only claim the run under the household
lock with a conditional UPDATE. So a duplicate enqueue re-pushes any unfinished run, and the
worker's sweeper (``app.jobs.runner.sweep_stalled_runs``) re-pushes runs arq has lost.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db_context import scoped_session
from app.jobs.queue import JobQueue
from app.jobs.registry import JobPayload, JobSpec, check_idempotency_key
from app.logging import get_logger
from app.models import JobRun
from app.services.errors import ApiError

log = get_logger(__name__)

ERROR_MAX = 200
CLAIMABLE = ("queued", "retrying", "running")
PENDING = ("queued", "retrying")
# Enqueue waits at most this long for a row lock (a referenced row another transaction holds
# FOR UPDATE, or a concurrent enqueue of the same key), then answers 409 busy.
ENQUEUE_LOCK_TIMEOUT = "2s"
_SET_LOCK_TIMEOUT = text(f"SET LOCAL lock_timeout = '{ENQUEUE_LOCK_TIMEOUT}'")
LOCK_NOT_AVAILABLE = "55P03"  # SQLSTATE lock_not_available: lock_timeout expired


class IdempotencyConflict(ValueError):
    """The idempotency key was already used for the same job with a different payload or by a
    different requester."""


class JobReferenceNotVisible(LookupError):
    """The payload names a household or row the requester can't see, or can't write when the
    job writes it. Render it as not found.

    The message names the job and the field, never the id.
    """


@dataclass(frozen=True)
class EnqueueResult:
    run_id: uuid.UUID
    created: bool  # False: an existing run with this key was returned (no new side effect)
    status: str


async def _check_references(
    session: AsyncSession, spec: JobSpec[Any], payload: JobPayload, household_id: uuid.UUID
) -> None:
    """Every ``Ref`` field must name a row the session (the requester's rights) may use.

    For a write reference the lookup takes a ``FOR KEY SHARE`` row lock. Postgres applies a
    table's UPDATE policies (their USING clause) to any row a ``SELECT ... FOR UPDATE/SHARE``
    locks, and drops the rows that fail them, so the lookup finds exactly the rows the requester
    could update directly. ``KEY SHARE`` is the weakest row lock (it blocks only deletes and key
    changes) and lasts until enqueue commits. Locking needs the UPDATE privilege on the table:
    a table the app can't update has nothing for a job to write either, so use ``access="read"``.

    References are checked in id order (then table name), so two enqueues that lock the same
    rows always take them in the same order and can't deadlock each other.
    """
    refs = sorted(
        spec.payload.refs.items(),
        key=lambda item: (getattr(payload, item[0]), item[1].model.__table__.name),
    )
    for field, ref in refs:
        table = ref.model.__table__
        stmt = select(table.c.id).where(
            table.c.id == getattr(payload, field),
            table.c.household_id == household_id,  # RLS does this too; belt and braces
        )
        if ref.access == "write":
            stmt = stmt.with_for_update(read=True, key_share=True)
        if (await session.execute(stmt)).first() is None:
            raise JobReferenceNotVisible(f"{spec.name}: {field} not found")


async def push(
    queue: JobQueue,
    job_name: str,
    household_id: uuid.UUID,
    run_id: uuid.UUID,
    payload_json: str,
    status: str,
    next_attempt_at: datetime | None,
) -> bool:
    """(Re-)push an unfinished run to arq under ``_job_id=run_id``. Returns whether it pushed.

    ``queued`` and ``running`` go out now; ``retrying`` waits for ``next_attempt_at``. arq
    drops the push if it still holds that job id (queued, deferred or in progress), so this only
    ever revives a run arq has lost. Finished runs are never pushed.
    """
    if status not in CLAIMABLE:
        return False
    args = (job_name, str(household_id), str(run_id), payload_json)
    if status == "retrying" and next_attempt_at is not None:
        await queue.enqueue_job(*args, _job_id=str(run_id), _defer_until=next_attempt_at)
    else:
        await queue.enqueue_job(*args, _job_id=str(run_id))
    return True


async def enqueue[P: JobPayload](
    factory: async_sessionmaker[AsyncSession],
    queue: JobQueue,
    spec: JobSpec[P],
    payload: P | Mapping[str, Any],
    *,
    household_id: uuid.UUID,
    requested_by: uuid.UUID | None,
    idempotency_key: str,
) -> EnqueueResult:
    """Validate, authorize, record and queue one run. Same ``(household, job, key)`` twice = one
    run.

    ``household_id`` and ``requested_by`` come from the authenticated caller (the session's
    user), never from ``payload``. ``requested_by`` is the person whose rights the payload's
    ids are checked against. ``None`` is for system callers and gets RLS's person-less view,
    never a household-wide one: it may *read* shared, summary-only and joint rows and *write*
    joint rows only (plus tables with no per-person rule). A worker-side scheduler that needs a
    person's own rows uses ``app.jobs.runner.enqueue_household_wide``, which ``app.api`` and
    ``app.services`` can't import.

    Raises ``InvalidJobPayload`` / ``InvalidIdempotencyKey`` before touching Postgres or Redis,
    and ``JobReferenceNotVisible`` (nothing recorded or queued) if the payload names another
    household or a row the requester can't use. The check runs for duplicates too, so a key
    can't be used to probe someone else's run. ``ApiError("busy")`` (409, nothing recorded or
    queued) if a referenced row or the key stays locked by another transaction for
    ``ENQUEUE_LOCK_TIMEOUT``.

    ``requested_by`` is recorded on the run (``job_run.requested_by``) and handed to the handler
    as ``JobContext.requested_by``. A handler that enqueues a follow-up passes it on, so a
    chained job keeps its requester. Reusing a key under another requester raises
    ``IdempotencyConflict``, like a different payload.

    A duplicate whose run is unfinished is pushed to arq again (see :func:`push`), which heals a
    crash between commit and push and a run arq dropped (its backstop timeout).
    """
    return await _enqueue(
        factory,
        queue,
        spec,
        payload,
        household_id=household_id,
        requested_by=requested_by,
        household_wide=False,
        idempotency_key=idempotency_key,
    )


async def _enqueue[P: JobPayload](
    factory: async_sessionmaker[AsyncSession],
    queue: JobQueue,
    spec: JobSpec[P],
    payload: P | Mapping[str, Any],
    *,
    household_id: uuid.UUID,
    requested_by: uuid.UUID | None,
    household_wide: bool,
    idempotency_key: str,
) -> EnqueueResult:
    """:func:`enqueue`, with the reference check optionally household-wide.

    The household-wide check is for the worker's own schedulers only, through
    ``app.jobs.runner.enqueue_household_wide`` (``tests/jobs/test_job_boundaries.py`` fails if
    anything else calls this).
    """
    validated = spec.validate(payload)
    check_idempotency_key(idempotency_key)
    if validated.household_id != household_id:
        raise JobReferenceNotVisible(f"{spec.name}: household_id not found")
    body = validated.model_dump(mode="json")

    try:
        run_id, status, created, next_attempt_at = await _record(
            factory,
            spec,
            validated,
            body,
            household_id,
            requested_by,
            household_wide,
            idempotency_key,
        )
    except OperationalError as exc:
        if getattr(exc.orig, "sqlstate", None) != LOCK_NOT_AVAILABLE:
            raise
        log.info("job.enqueue_busy", job=spec.name)
        raise ApiError("busy", "That item is busy. Try again in a moment.") from None

    pushed = await push(
        queue,
        spec.name,
        household_id,
        run_id,
        validated.model_dump_json(),
        status,
        next_attempt_at,
    )
    log.info(
        "job.enqueued",
        job=spec.name,
        run_id=str(run_id),
        created=created,
        status=status,
        pushed=pushed,
    )
    return EnqueueResult(run_id=run_id, created=created, status=status)


async def _record(
    factory: async_sessionmaker[AsyncSession],
    spec: JobSpec[Any],
    validated: JobPayload,
    body: dict[str, Any],
    household_id: uuid.UUID,
    requested_by: uuid.UUID | None,
    household_wide: bool,
    idempotency_key: str,
) -> tuple[uuid.UUID, str, bool, datetime | None]:
    """Check references and insert (or find) the run, in one transaction with a lock timeout.

    Returns ``(run_id, status, created, next_attempt_at)``.
    """
    async with scoped_session(
        factory, household_id, requested_by, household_wide=household_wide
    ) as s:
        await s.execute(_SET_LOCK_TIMEOUT)
        await _check_references(s, spec, validated, household_id)
        stmt = (
            insert(JobRun)
            .values(
                household_id=household_id,
                job_name=spec.name,
                idempotency_key=idempotency_key,
                payload=body,
                requested_by=requested_by,
                max_attempts=spec.retry.max_attempts,
            )
            .on_conflict_do_nothing(index_elements=["household_id", "job_name", "idempotency_key"])
            .returning(JobRun.id)
        )
        new_id = (await s.execute(stmt)).scalar_one_or_none()
        next_attempt_at: datetime | None = None
        if new_id is not None:
            run_id, status, created = new_id, "queued", True
        else:
            row = (
                await s.execute(
                    select(
                        JobRun.id,
                        JobRun.status,
                        JobRun.payload,
                        JobRun.requested_by,
                        JobRun.next_attempt_at,
                    ).where(
                        JobRun.household_id == household_id,
                        JobRun.job_name == spec.name,
                        JobRun.idempotency_key == idempotency_key,
                    )
                )
            ).one()
            # One message for both cases, so a conflict doesn't tell a caller which one it was.
            if row.payload != body or row.requested_by != requested_by:
                raise IdempotencyConflict(
                    f"{spec.name}: key reused with a different payload or requester"
                )
            run_id, status, created = row.id, row.status, False
            next_attempt_at = row.next_attempt_at
    return run_id, status, created, next_attempt_at


@dataclass(frozen=True)
class StalledRun:
    id: uuid.UUID
    job_name: str
    status: str
    payload: dict[str, Any]
    started_at: datetime | None
    next_attempt_at: datetime | None
    now: datetime  # the database's clock when the row was read


async def stalled_runs(
    factory: async_sessionmaker[AsyncSession],
    household_id: uuid.UUID,
    *,
    pending_grace: timedelta,
    running_after: timedelta,
    limit: int,
) -> list[StalledRun]:
    """Unfinished runs of one household that arq may have lost, oldest first.

    ``queued`` for longer than ``pending_grace``, ``retrying`` more than ``pending_grace`` past
    ``next_attempt_at``, and ``running`` for longer than ``running_after`` (the caller narrows
    that per job, from its timeout). Read-only; the database's clock is the only clock.
    """
    now = func.now()
    async with scoped_session(factory, household_id, None) as s:
        rows = (
            await s.execute(
                select(
                    JobRun.id,
                    JobRun.job_name,
                    JobRun.status,
                    JobRun.payload,
                    JobRun.started_at,
                    JobRun.next_attempt_at,
                    now.label("now"),
                )
                .where(
                    JobRun.household_id == household_id,
                    or_(
                        (JobRun.status == "queued") & (JobRun.enqueued_at < now - pending_grace),
                        (JobRun.status == "retrying")
                        & (
                            func.coalesce(JobRun.next_attempt_at, JobRun.enqueued_at)
                            < now - pending_grace
                        ),
                        (JobRun.status == "running") & (JobRun.started_at < now - running_after),
                    ),
                )
                .order_by(JobRun.enqueued_at)
                .limit(limit)
            )
        ).all()
    return [
        StalledRun(
            id=r.id,
            job_name=r.job_name,
            status=r.status,
            payload=r.payload,
            started_at=r.started_at,
            next_attempt_at=r.next_attempt_at,
            now=r.now,
        )
        for r in rows
    ]


def _where(run_id: uuid.UUID, statuses: tuple[str, ...]) -> Any:
    return (JobRun.id == run_id) & JobRun.status.in_(statuses)


def _owned(run_id: uuid.UUID, attempt: int) -> Any:
    """The row is still ``running`` *this* attempt (not a later re-claim of it)."""
    return _where(run_id, ("running",)) & (JobRun.attempts == attempt)


@dataclass(frozen=True)
class Claim:
    attempt: int
    payload: dict[str, Any]  # job_run.payload: what the handler runs on, never Redis's copy
    requested_by: uuid.UUID | None  # job_run.requested_by, recorded at enqueue


def _orphaned(spec: JobSpec[Any]) -> Any:
    """A ``running`` row old enough that no execution of it can still be live."""
    return (JobRun.status == "running") & (JobRun.started_at < func.now() - spec.orphan_after)


async def claim(
    factory: async_sessionmaker[AsyncSession],
    household_id: uuid.UUID,
    run_id: uuid.UUID,
    spec: JobSpec[Any],
) -> Claim | None:
    """Mark the run ``running`` and count the attempt. Call only while holding the household
    lock. Returns the attempt number and the stored payload, or None when the run isn't
    claimable: done, failed, unknown, another job's run, ``running`` for less than
    ``spec.orphan_after`` (maybe still live), or an orphan with no attempts left, which is
    marked failed here."""
    pending = JobRun.status.in_(PENDING)
    mine = (JobRun.id == run_id) & (JobRun.job_name == spec.name)
    async with scoped_session(factory, household_id, None) as s:
        row = (
            await s.execute(
                update(JobRun)
                .where(mine, pending | _orphaned(spec), JobRun.attempts < JobRun.max_attempts)
                .values(
                    status="running",
                    attempts=JobRun.attempts + 1,
                    started_at=func.now(),
                    next_attempt_at=None,
                )
                .returning(JobRun.attempts, JobRun.payload, JobRun.requested_by)
            )
        ).one_or_none()
        if row is None:
            # A worker died on the last attempt: close the run out.
            await s.execute(
                update(JobRun)
                .where(mine, _orphaned(spec))
                .values(
                    status="failed",
                    finished_at=func.now(),
                    last_error=func.coalesce(JobRun.last_error, "WorkerLost"),
                )
            )
            return None
        return Claim(attempt=row.attempts, payload=row.payload, requested_by=row.requested_by)


class RunLost(RuntimeError):
    """The run's row was no longer ``running`` this attempt when the handler finished (rolls
    its work back)."""


async def mark_succeeded(session: AsyncSession, run_id: uuid.UUID, attempt: int) -> None:
    """Mark success inside the handler's session, so it commits atomically with its writes."""
    result = await session.execute(
        update(JobRun)
        .where(_owned(run_id, attempt))
        .values(status="succeeded", finished_at=func.now(), last_error=None)
        .returning(JobRun.id)
    )
    if result.scalar_one_or_none() is None:
        raise RunLost(str(run_id))


async def mark_retrying(
    factory: async_sessionmaker[AsyncSession],
    household_id: uuid.UUID,
    run_id: uuid.UUID,
    attempt: int,
    error: str,
    delay: timedelta,
) -> None:
    async with scoped_session(factory, household_id, None) as s:
        await s.execute(
            update(JobRun)
            .where(_owned(run_id, attempt))
            .values(
                status="retrying",
                last_error=error[:ERROR_MAX],
                next_attempt_at=func.now() + delay,
            )
        )


async def mark_failed(
    factory: async_sessionmaker[AsyncSession],
    household_id: uuid.UUID,
    run_id: uuid.UUID,
    attempt: int,
    error: str,
) -> None:
    """Terminal failure of a claimed attempt."""
    async with scoped_session(factory, household_id, None) as s:
        await s.execute(
            update(JobRun)
            .where(_owned(run_id, attempt))
            .values(
                status="failed",
                finished_at=func.now(),
                next_attempt_at=None,
                last_error=error[:ERROR_MAX],
            )
        )


async def fail_unclaimed(
    factory: async_sessionmaker[AsyncSession],
    household_id: uuid.UUID,
    run_id: uuid.UUID,
    job_name: str,
    error: str,
) -> None:
    """Terminal failure before any claim (a malformed delivery, a household that stayed busy).
    Matches ``queued``/``retrying`` only, so a run another delivery owns is never touched."""
    async with scoped_session(factory, household_id, None) as s:
        await s.execute(
            update(JobRun)
            .where(_where(run_id, PENDING), JobRun.job_name == job_name)
            .values(
                status="failed",
                finished_at=func.now(),
                next_attempt_at=None,
                last_error=error[:ERROR_MAX],
            )
        )
