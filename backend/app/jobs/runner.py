"""Run one delivery of a job: validate, lock the household, claim, handle, record (ADR 0016).

arq delivers at least once. These rules turn that into "one successful side effect":

1. The household id, run id and payload in the Redis message are checked (strictly) before
   anything runs. A malformed message is never handed to a handler and never retried.
2. The per-household advisory lock is taken without waiting. If it's busy the delivery is
   deferred (no attempt used); after ``max_lock_deferrals`` the run fails as ``HouseholdBusy``.
3. Under the lock, ``claim`` moves the row to ``running`` and returns the payload (and the
   requester, ``JobContext.requested_by``) stored in ``job_run``. **That** payload is what the
   handler gets: it was validated and authorized at enqueue, while the Redis copy is
   unauthenticated. A Redis copy that differs is logged and ignored. A run that already
   succeeded or failed is skipped, so a duplicate delivery does nothing. So is a run another
   execution started less than ``spec.orphan_after`` ago, even with the lock in hand: a dropped
   lock connection frees the lock while that execution still runs. The sweeper re-pushes it
   once it is old enough to be a real orphan.
4. The handler runs in a household-wide session under ``asyncio.timeout(spec.timeout)``; the
   success mark commits in that same session, atomically with whatever the handler left
   uncommitted. arq's own timeout sits ``ARQ_TIMEOUT_MARGIN`` above, as a backstop only.
5. A handler exception (a timeout included) is recorded by **class name only** (messages can
   hold secrets, hard rule 3), then retried with capped exponential backoff until
   ``max_attempts``.
6. A cancellation after the claim (worker shutdown, arq's backstop timeout) is recorded as
   ``retrying`` or ``failed`` before it propagates, so a row never stays ``running`` for a run
   nobody is executing.
7. On arq's last allowed try the runner never asks for another one: it records ``failed``, so
   a run can't be silently dropped by arq while ``job_run`` still says ``retrying``.
8. arq can still lose a run: its backstop timeout finishes the job without a retry, after the
   cancellation above recorded ``retrying``. ``sweep_stalled_runs`` (a worker cron) re-pushes
   overdue ``queued``/``retrying`` runs and ``running`` runs older than their timeout plus both
   margins. Re-pushing is safe for the same reasons a duplicate delivery is (rules 2 and 3).

Worker-only. ``app.api`` and ``app.services`` may not import this module (import-linter), so
``enqueue_household_wide`` is out of reach of request code.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from arq import Retry
from arq.worker import Function, func
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db_context import list_household_ids, scoped_session, unscoped_session
from app.jobs.lock import household_lock
from app.jobs.queue import JobQueue
from app.jobs.registry import (
    ARQ_TIMEOUT_MARGIN,
    HouseholdSessions,
    InvalidJobPayload,
    JobContext,
    JobPayload,
    JobRegistry,
    JobSpec,
    discover_jobs,
)
from app.logging import get_logger
from app.services import jobs as job_runs

log = get_logger(__name__)

Outcome = Literal["succeeded", "failed", "skipped", "rejected"]
CTX_KEY = "holdfast_jobs"
CANCELLED = "CancelledError"
# Sweeper (rule 8). A queued/retrying run this far past due, or a running run this far past its
# arq backstop, is presumed lost. Too early only costs a push arq drops (it still has the job).
SWEEP_PENDING_GRACE = timedelta(minutes=2)
SWEEP_RUNNING_MARGIN = timedelta(minutes=2)
SWEEP_BATCH = 100  # per household per sweep; the rest wait for the next minute


@dataclass(frozen=True)
class RunnerDeps:
    """Built once at worker startup and kept in arq's ``ctx[CTX_KEY]``."""

    engine: AsyncEngine
    factory: async_sessionmaker[AsyncSession]


def _parse_ids(args: Sequence[object]) -> tuple[uuid.UUID, uuid.UUID, str] | None:
    if len(args) != 3:
        return None
    household, run, payload_json = args
    if not (isinstance(household, str) and isinstance(run, str) and isinstance(payload_json, str)):
        return None
    try:
        return uuid.UUID(household), uuid.UUID(run), payload_json
    except ValueError:
        return None


@dataclass(frozen=True)
class _Run:
    deps: RunnerDeps
    spec: JobSpec[Any]
    household_id: uuid.UUID
    run_id: uuid.UUID
    attempt: int
    requested_by: uuid.UUID | None
    last_try: bool
    fields: dict[str, str]

    @property
    def final(self) -> bool:
        return self.attempt >= self.spec.retry.max_attempts or self.last_try

    async def fail(self, error: str) -> None:
        await job_runs.mark_failed(
            self.deps.factory, self.household_id, self.run_id, self.attempt, error
        )

    async def retry(self, error: str, delay: timedelta) -> None:
        await job_runs.mark_retrying(
            self.deps.factory, self.household_id, self.run_id, self.attempt, error, delay
        )

    async def record_cancelled(self) -> None:
        """Cancellation isn't the handler's verdict: retry at once if attempts remain."""
        log.warning("job.cancelled", attempt=self.attempt, final=self.final, **self.fields)
        if self.final:
            await self.fail(CANCELLED)
        else:
            await self.retry(CANCELLED, timedelta(0))


async def execute(
    deps: RunnerDeps, spec: JobSpec[Any], args: Sequence[object], *, job_try: int
) -> Outcome:
    """Run one arq delivery of ``spec``. Raises :class:`arq.Retry` to defer or back off."""
    parsed = _parse_ids(args)
    if parsed is None:
        # Not something enqueue() produces: no trusted household to record it under.
        log.warning("job.rejected", job=spec.name, reason="malformed_arguments")
        return "rejected"
    household_id, run_id, payload_json = parsed
    fields = {"job": spec.name, "run_id": str(run_id)}

    try:
        delivered = spec.parse_json(payload_json)
        if delivered.household_id != household_id:
            raise InvalidJobPayload(f"{spec.name}: household mismatch")
    except InvalidJobPayload:
        log.warning("job.rejected", reason="invalid_payload", **fields)
        await job_runs.fail_unclaimed(
            deps.factory, household_id, run_id, spec.name, "InvalidJobPayload"
        )
        return "rejected"

    last_try = job_try >= spec.retry.queue_max_tries
    async with household_lock(deps.engine, household_id) as locked:
        if not locked:
            if last_try:
                log.warning("job.failed", error="HouseholdBusy", **fields)
                await job_runs.fail_unclaimed(
                    deps.factory, household_id, run_id, spec.name, "HouseholdBusy"
                )
                return "failed"
            log.info("job.deferred", reason="household_busy", **fields)
            raise Retry(defer=spec.retry.lock_retry_delay)

        claimed = await job_runs.claim(deps.factory, household_id, run_id, spec)
        if claimed is None:
            log.info("job.skipped", reason="not_claimable", **fields)
            return "skipped"

        run = _Run(
            deps,
            spec,
            household_id,
            run_id,
            claimed.attempt,
            claimed.requested_by,
            last_try,
            fields,
        )
        try:
            return await _handle(run, claimed.payload, delivered)
        except asyncio.CancelledError:
            # Shielded: a second cancel mustn't stop the row leaving ``running``.
            await asyncio.shield(run.record_cancelled())
            raise


async def _handle(run: _Run, stored: dict[str, Any], delivered: JobPayload) -> Outcome:
    spec, fields = run.spec, run.fields
    try:
        payload = spec.validate(stored)
    except InvalidJobPayload:
        # Stored under an older schema (a deploy changed the payload class): never retryable.
        log.warning("job.rejected", reason="invalid_stored_payload", **fields)
        await run.fail("InvalidJobPayload")
        return "rejected"
    if payload != delivered:
        log.warning("job.delivery_mismatch", action="ran_stored_payload", **fields)

    try:
        async with asyncio.timeout(spec.timeout.total_seconds()):
            async with scoped_session(
                run.deps.factory, run.household_id, None, household_wide=True
            ) as session:
                ctx = JobContext(
                    run_id=run.run_id,
                    household_id=run.household_id,
                    attempt=run.attempt,
                    requested_by=run.requested_by,
                    session=session,
                    factory=HouseholdSessions(run.deps.factory, run.household_id),
                )
                await spec.handler(ctx, payload)
                await job_runs.mark_succeeded(session, run.run_id, run.attempt)
    except Exception as exc:  # TimeoutError from asyncio.timeout lands here too
        error = type(exc).__name__
        if run.final:
            log.warning("job.failed", error=error, attempt=run.attempt, **fields)
            await run.fail(error)
            return "failed"
        delay = spec.retry.delay(run.attempt)
        log.info(
            "job.retrying",
            error=error,
            attempt=run.attempt,
            delay_s=delay.total_seconds(),
            **fields,
        )
        await run.retry(error, delay)
        raise Retry(defer=delay) from None

    log.info("job.succeeded", attempt=run.attempt, **fields)
    return "succeeded"


def arq_function(spec: JobSpec[Any]) -> Function:
    """Wrap ``spec`` as an arq function named ``spec.name``.

    ``keep_result=0``: ``job_run`` is the record, so arq stores no result. A stored result would
    hold arq's own error text and block a re-push under the same ``_job_id`` until it expired.
    """

    async def run(ctx: dict[str, Any], *args: object) -> str:
        deps: RunnerDeps = ctx[CTX_KEY]
        return await execute(deps, spec, args, job_try=int(ctx.get("job_try", 1)))

    run.__qualname__ = f"job[{spec.name}]"
    return func(
        run,
        name=spec.name,
        max_tries=spec.retry.queue_max_tries,
        timeout=spec.arq_timeout.total_seconds(),
        keep_result=0,
    )


async def enqueue_household_wide[P: JobPayload](
    factory: async_sessionmaker[AsyncSession],
    queue: JobQueue,
    spec: JobSpec[P],
    payload: P | Mapping[str, Any],
    *,
    household_id: uuid.UUID,
    idempotency_key: str,
) -> job_runs.EnqueueResult:
    """Enqueue on behalf of the worker itself (a scheduler), checking references household-wide.

    For scheduled work on rows a person owns (a nightly job over every account). The ids must
    still be rows of ``household_id``; any person's rows qualify. Request code can't reach this:
    import-linter forbids ``app.api`` and ``app.services`` from importing this module, and
    ``tests/jobs/test_job_boundaries.py`` fails if anything else calls ``_enqueue``.
    """
    return await job_runs._enqueue(
        factory,
        queue,
        spec,
        payload,
        household_id=household_id,
        requested_by=None,
        household_wide=True,
        idempotency_key=idempotency_key,
    )


async def sweep_household(
    deps: RunnerDeps, queue: JobQueue, household_id: uuid.UUID, specs: JobRegistry
) -> int:
    """Re-push one household's runs that arq has lost (rule 8). Returns how many it pushed.

    Never claims or marks a run itself (apart from failing a pending run whose stored payload
    no longer validates), so it can't race a live execution: every push goes through the
    household lock and the conditional claim like any delivery.
    """
    stalled = await job_runs.stalled_runs(
        deps.factory,
        household_id,
        pending_grace=SWEEP_PENDING_GRACE,
        running_after=ARQ_TIMEOUT_MARGIN + SWEEP_RUNNING_MARGIN,
        limit=SWEEP_BATCH,
    )
    pushed = 0
    for row in stalled:
        fields = {"job": row.job_name, "run_id": str(row.id), "status": row.status}
        if row.job_name not in specs:
            log.warning("job.sweep_skipped", reason="unknown_job", **fields)
            continue
        spec = specs.get(row.job_name)
        if row.status == "running" and row.started_at is not None:
            backstop = spec.orphan_after + SWEEP_RUNNING_MARGIN
            if row.now - row.started_at < backstop:
                continue  # may still be live work
        try:
            payload = spec.validate(row.payload)
            if payload.household_id != household_id:
                raise InvalidJobPayload(f"{spec.name}: household mismatch")
        except InvalidJobPayload:
            log.warning("job.sweep_skipped", reason="invalid_stored_payload", **fields)
            if row.status in job_runs.PENDING:
                await job_runs.fail_unclaimed(
                    deps.factory, household_id, row.id, spec.name, "InvalidJobPayload"
                )
            continue
        if await job_runs.push(
            queue,
            spec.name,
            household_id,
            row.id,
            payload.model_dump_json(),
            row.status,
            row.next_attempt_at,
        ):
            pushed += 1
            log.info("job.sweep_pushed", **fields)
    return pushed


async def sweep_all(deps: RunnerDeps, queue: JobQueue, specs: JobRegistry) -> int:
    """:func:`sweep_household` for every household. Returns how many it pushed.

    Households come from ``list_household_ids()`` (migration 0010: SECURITY DEFINER, ids only),
    so a household with no login is swept too. One household's failure (logged by class name)
    doesn't stop the others.
    """
    async with unscoped_session(deps.factory) as s:
        households = await list_household_ids(s)
    pushed = 0
    for household_id in households:
        try:
            pushed += await sweep_household(deps, queue, household_id, specs)
        except Exception as exc:
            log.warning("job.sweep_failed", error=type(exc).__name__)
    log.info("job.sweep", households=len(households), pushed=pushed)
    return pushed


async def sweep_stalled_runs(ctx: dict[str, Any]) -> int:
    """Worker cron (every minute): re-push lost runs through the worker's own arq pool."""
    deps: RunnerDeps = ctx[CTX_KEY]
    queue: JobQueue = ctx["redis"]
    return await sweep_all(deps, queue, discover_jobs())
