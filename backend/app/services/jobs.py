"""Job runs: enqueue (idempotent) and the ``job_run`` state transitions the worker drives.

ADR 0016. ``job_run`` is the source of truth; arq is the delivery mechanism. Every write here runs
as holdfast_app in the run's household context, so RLS (FORCE) applies to the worker too.

State machine (each transition is a conditional UPDATE, so a stale or duplicate delivery is a
no-op)::

    queued ──claim──> running ──succeed──> succeeded
    retrying ─claim─^    │ └──fail (attempts left)──> retrying
    running (orphan) ─^  └──fail (no attempts left)─> failed

A ``running`` row may be re-claimed only by a runner holding the household lock: while the lock
is held nobody else can be running that household's jobs, so the row is an orphan of a dead
worker.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db_context import scoped_session
from app.jobs.queue import JobQueue
from app.jobs.registry import JobPayload, JobSpec, check_idempotency_key
from app.logging import get_logger
from app.models import JobRun

log = get_logger(__name__)

ERROR_MAX = 200
CLAIMABLE = ("queued", "retrying", "running")
PENDING = ("queued", "retrying")


class IdempotencyConflict(ValueError):
    """The idempotency key was already used for the same job with a different payload."""


@dataclass(frozen=True)
class EnqueueResult:
    run_id: uuid.UUID
    created: bool  # False: an existing run with this key was returned (no new side effect)
    status: str


async def enqueue[P: JobPayload](
    factory: async_sessionmaker[AsyncSession],
    queue: JobQueue,
    spec: JobSpec[P],
    payload: P | Mapping[str, Any],
    *,
    idempotency_key: str,
) -> EnqueueResult:
    """Validate, record and queue one run. Same ``(household, job, key)`` twice = one run.

    Raises ``InvalidJobPayload`` / ``InvalidIdempotencyKey`` before touching Postgres or Redis.
    A duplicate whose run is still ``queued`` is pushed to arq again under the same arq job id
    (arq drops it if the job is already there), which heals a crash between commit and push.
    """
    validated = spec.validate(payload)
    check_idempotency_key(idempotency_key)
    household_id = validated.household_id
    body = validated.model_dump(mode="json")

    async with scoped_session(factory, household_id, None) as s:
        stmt = (
            insert(JobRun)
            .values(
                household_id=household_id,
                job_name=spec.name,
                idempotency_key=idempotency_key,
                payload=body,
                max_attempts=spec.retry.max_attempts,
            )
            .on_conflict_do_nothing(index_elements=["household_id", "job_name", "idempotency_key"])
            .returning(JobRun.id)
        )
        new_id = (await s.execute(stmt)).scalar_one_or_none()
        if new_id is not None:
            run_id, status, created = new_id, "queued", True
        else:
            row = (
                await s.execute(
                    select(JobRun.id, JobRun.status, JobRun.payload).where(
                        JobRun.household_id == household_id,
                        JobRun.job_name == spec.name,
                        JobRun.idempotency_key == idempotency_key,
                    )
                )
            ).one()
            if row.payload != body:
                raise IdempotencyConflict(f"{spec.name}: key reused with a different payload")
            run_id, status, created = row.id, row.status, False

    if status == "queued":
        await queue.enqueue_job(
            spec.name,
            str(household_id),
            str(run_id),
            validated.model_dump_json(),
            _job_id=str(run_id),
        )
    log.info("job.enqueued", job=spec.name, run_id=str(run_id), created=created, status=status)
    return EnqueueResult(run_id=run_id, created=created, status=status)


def _where(run_id: uuid.UUID, statuses: tuple[str, ...]) -> Any:
    return (JobRun.id == run_id) & JobRun.status.in_(statuses)


async def claim(
    factory: async_sessionmaker[AsyncSession], household_id: uuid.UUID, run_id: uuid.UUID
) -> int | None:
    """Mark the run ``running`` and count the attempt. Call only while holding the household
    lock. Returns the attempt number, or None when the run isn't claimable (done, failed,
    unknown, or an orphan with no attempts left, which is marked failed here)."""
    async with scoped_session(factory, household_id, None) as s:
        attempt = (
            await s.execute(
                update(JobRun)
                .where(_where(run_id, CLAIMABLE), JobRun.attempts < JobRun.max_attempts)
                .values(
                    status="running",
                    attempts=JobRun.attempts + 1,
                    started_at=func.now(),
                    next_attempt_at=None,
                )
                .returning(JobRun.attempts)
            )
        ).scalar_one_or_none()
        if attempt is None:
            # A worker died on the last attempt: close the run out.
            await s.execute(
                update(JobRun)
                .where(_where(run_id, ("running",)))
                .values(
                    status="failed",
                    finished_at=func.now(),
                    last_error=func.coalesce(JobRun.last_error, "WorkerLost"),
                )
            )
        return attempt


class RunLost(RuntimeError):
    """The run's row was no longer ``running`` when the handler finished (rolls its work back)."""


async def mark_succeeded(session: AsyncSession, run_id: uuid.UUID) -> None:
    """Mark success inside the handler's session, so it commits atomically with its writes."""
    result = await session.execute(
        update(JobRun)
        .where(_where(run_id, ("running",)))
        .values(status="succeeded", finished_at=func.now(), last_error=None)
        .returning(JobRun.id)
    )
    if result.scalar_one_or_none() is None:
        raise RunLost(str(run_id))


async def mark_retrying(
    factory: async_sessionmaker[AsyncSession],
    household_id: uuid.UUID,
    run_id: uuid.UUID,
    error: str,
    delay: timedelta,
) -> None:
    async with scoped_session(factory, household_id, None) as s:
        await s.execute(
            update(JobRun)
            .where(_where(run_id, ("running",)))
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
    error: str,
    *,
    from_statuses: tuple[str, ...] = ("running",),
) -> None:
    """Terminal failure. ``from_statuses`` guards against overwriting a run another delivery
    owns: pre-claim failures pass ``PENDING`` so a ``running`` row is never touched."""
    async with scoped_session(factory, household_id, None) as s:
        await s.execute(
            update(JobRun)
            .where(_where(run_id, from_statuses))
            .values(
                status="failed",
                finished_at=func.now(),
                next_attempt_at=None,
                last_error=error[:ERROR_MAX],
            )
        )
