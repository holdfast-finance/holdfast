"""Run one delivery of a job: validate, lock the household, claim, handle, record (ADR 0016).

arq delivers at least once. These rules turn that into "one successful side effect":

1. The household id, run id and payload are re-validated (strictly) before anything runs. A bad
   payload is never handed to a handler and never retried.
2. The per-household advisory lock is taken without waiting. If it's busy the delivery is
   deferred (no attempt used); after ``max_lock_deferrals`` the run fails as ``HouseholdBusy``.
3. Under the lock, ``claim`` moves the row to ``running``. A run that already succeeded or
   failed is skipped, so a duplicate delivery does nothing.
4. The handler runs in a household-wide session; the success mark commits in that same
   session, atomically with whatever the handler left uncommitted.
5. A handler exception is recorded by **class name only** (messages can hold secrets, hard rule
   3), then retried with capped exponential backoff until ``max_attempts``.
6. On arq's last allowed try the runner never asks for another one: it records ``failed``, so
   a run can't be silently dropped by arq while ``job_run`` still says ``retrying``.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from arq import Retry
from arq.worker import Function, func
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db_context import scoped_session
from app.jobs.lock import household_lock
from app.jobs.registry import InvalidJobPayload, JobContext, JobSpec
from app.logging import get_logger
from app.services import jobs as job_runs

log = get_logger(__name__)

Outcome = Literal["succeeded", "failed", "skipped", "rejected"]
CTX_KEY = "holdfast_jobs"


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
        payload = spec.parse_json(payload_json)
        if payload.household_id != household_id:
            raise InvalidJobPayload(f"{spec.name}: household mismatch")
    except InvalidJobPayload:
        log.warning("job.rejected", reason="invalid_payload", **fields)
        await job_runs.mark_failed(
            deps.factory,
            household_id,
            run_id,
            "InvalidJobPayload",
            from_statuses=job_runs.PENDING,
        )
        return "rejected"

    last_try = job_try >= spec.retry.queue_max_tries
    async with household_lock(deps.engine, household_id) as locked:
        if not locked:
            if last_try:
                log.warning("job.failed", error="HouseholdBusy", **fields)
                await job_runs.mark_failed(
                    deps.factory,
                    household_id,
                    run_id,
                    "HouseholdBusy",
                    from_statuses=job_runs.PENDING,
                )
                return "failed"
            log.info("job.deferred", reason="household_busy", **fields)
            raise Retry(defer=spec.retry.lock_retry_delay)

        attempt = await job_runs.claim(deps.factory, household_id, run_id)
        if attempt is None:
            log.info("job.skipped", reason="not_claimable", **fields)
            return "skipped"

        try:
            async with scoped_session(
                deps.factory, household_id, None, household_wide=True
            ) as session:
                ctx = JobContext(
                    run_id=run_id,
                    household_id=household_id,
                    attempt=attempt,
                    session=session,
                    factory=deps.factory,
                )
                await spec.handler(ctx, payload)
                await job_runs.mark_succeeded(session, run_id)
        except Exception as exc:
            error = type(exc).__name__
            if attempt >= spec.retry.max_attempts or last_try:
                log.warning("job.failed", error=error, attempt=attempt, **fields)
                await job_runs.mark_failed(deps.factory, household_id, run_id, error)
                return "failed"
            delay = spec.retry.delay(attempt)
            log.info(
                "job.retrying",
                error=error,
                attempt=attempt,
                delay_s=delay.total_seconds(),
                **fields,
            )
            await job_runs.mark_retrying(deps.factory, household_id, run_id, error, delay)
            raise Retry(defer=delay) from None

    log.info("job.succeeded", attempt=attempt, **fields)
    return "succeeded"


def arq_function(spec: JobSpec[Any]) -> Function:
    """Wrap ``spec`` as an arq function named ``spec.name``."""

    async def run(ctx: dict[str, Any], *args: object) -> str:
        deps: RunnerDeps = ctx[CTX_KEY]
        return await execute(deps, spec, args, job_try=int(ctx.get("job_try", 1)))

    run.__qualname__ = f"job[{spec.name}]"
    return func(
        run,
        name=spec.name,
        max_tries=spec.retry.queue_max_tries,
        timeout=spec.timeout.total_seconds(),
    )
