"""Run one delivery of a job: validate, lock the household, claim, handle, record (ADR 0016).

arq delivers at least once. These rules turn that into "one successful side effect":

1. The household id, run id and payload in the Redis message are checked (strictly) before
   anything runs. A malformed message is never handed to a handler and never retried.
2. The per-household advisory lock is taken without waiting. If it's busy the delivery is
   deferred (no attempt used); after ``max_lock_deferrals`` the run fails as ``HouseholdBusy``.
3. Under the lock, ``claim`` moves the row to ``running`` and returns the payload stored in
   ``job_run``. **That** payload is what the handler gets: it was validated and authorized at
   enqueue, while the Redis copy is unauthenticated. A Redis copy that differs is logged and
   ignored. A run that already succeeded or failed is skipped, so a duplicate delivery does
   nothing.
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
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from arq import Retry
from arq.worker import Function, func
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db_context import scoped_session
from app.jobs.lock import household_lock
from app.jobs.registry import InvalidJobPayload, JobContext, JobPayload, JobSpec
from app.logging import get_logger
from app.services import jobs as job_runs

log = get_logger(__name__)

Outcome = Literal["succeeded", "failed", "skipped", "rejected"]
CTX_KEY = "holdfast_jobs"
# arq's per-function timeout = spec.timeout + this. It covers the bookkeeping around the handler
# (pool checkout waits up to 30 s, lock, claim, marks); the runner's own timeout fires first.
ARQ_TIMEOUT_MARGIN = timedelta(seconds=60)
CANCELLED = "CancelledError"


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

        claimed = await job_runs.claim(deps.factory, household_id, run_id, spec.name)
        if claimed is None:
            log.info("job.skipped", reason="not_claimable", **fields)
            return "skipped"

        run = _Run(deps, spec, household_id, run_id, claimed.attempt, last_try, fields)
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
                    session=session,
                    factory=run.deps.factory,
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
        timeout=(spec.timeout + ARQ_TIMEOUT_MARGIN).total_seconds(),
        keep_result=0,
    )
