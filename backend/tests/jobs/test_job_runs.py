"""Job runs against real Postgres (holdfast_app, RLS) and Redis: idempotency, lock, retries.

Acceptance (ROADMAP F2): a job enqueued twice gives one side effect; a malformed (non-UUID)
payload is rejected before enqueue and before run.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import pytest
from arq import Retry, Worker
from redis.asyncio import Redis
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.exc import IntegrityError

from app.db_context import scoped_session
from app.jobs import queue
from app.jobs.lock import household_lock
from app.jobs.queue import arq_pool
from app.jobs.registry import (
    InvalidIdempotencyKey,
    InvalidJobPayload,
    JobContext,
    JobPayload,
    JobRegistry,
    JobSpec,
    RetryPolicy,
)
from app.jobs.runner import CTX_KEY, RunnerDeps, arq_function, execute
from app.models import AuditLog, JobRun
from app.services import jobs as job_runs
from app.services.jobs import EnqueueResult, IdempotencyConflict, enqueue
from tests.models.conftest import set_ctx

pytestmark = pytest.mark.services

EFFECT_ACTION = "test.job_side_effect"
CANARY = "access-sandbox-canary-0000"


class ItemPayload(JobPayload):
    item_id: uuid.UUID


REG = JobRegistry()
FAST = RetryPolicy(
    max_attempts=3,
    base_delay=timedelta(seconds=2),
    max_delay=timedelta(seconds=5),
    lock_retry_delay=timedelta(seconds=1),
    max_lock_deferrals=2,
)
FAILS_LEFT: dict[uuid.UUID, int] = {}
ATTEMPTS_SEEN: list[int] = []
RUNNING: dict[str, int] = {"now": 0, "max": 0}


async def _side_effect(ctx: JobContext, payload: ItemPayload) -> None:
    ctx.session.add(
        AuditLog(
            id=uuid.uuid4(),
            household_id=ctx.household_id,
            action=EFFECT_ACTION,
            entity="item",
            entity_id=str(payload.item_id),
        )
    )


@REG.job("test.effect", payload=ItemPayload, retry=FAST)
async def effect(ctx: JobContext, payload: ItemPayload) -> None:
    ATTEMPTS_SEEN.append(ctx.attempt)
    await _side_effect(ctx, payload)


@REG.job("test.flaky", payload=ItemPayload, retry=FAST)
async def flaky(ctx: JobContext, payload: ItemPayload) -> None:
    await _side_effect(ctx, payload)  # rolled back whenever the attempt fails
    if FAILS_LEFT.get(payload.item_id, 0) > 0:
        FAILS_LEFT[payload.item_id] -= 1
        raise RuntimeError(f"upstream said no: {CANARY}")


@REG.job("test.slow", payload=ItemPayload, retry=FAST)
async def slow(ctx: JobContext, payload: ItemPayload) -> None:
    RUNNING["now"] += 1
    RUNNING["max"] = max(RUNNING["max"], RUNNING["now"])
    try:
        await asyncio.sleep(0.3)
    finally:
        RUNNING["now"] -= 1


@REG.job("test.one_shot", payload=ItemPayload, retry=RetryPolicy(max_attempts=1))
async def one_shot(ctx: JobContext, payload: ItemPayload) -> None:
    await _side_effect(ctx, payload)


class RecordingQueue:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], str | None]] = []

    async def enqueue_job(self, function: str, *args: Any, _job_id: str | None = None) -> Any:
        self.calls.append((function, args, _job_id))
        return object()


async def _enqueue(
    deps: RunnerDeps, spec: JobSpec[ItemPayload], hid: uuid.UUID, key: str = "k1"
) -> tuple[EnqueueResult, tuple[Any, ...]]:
    q = RecordingQueue()
    res = await enqueue(
        deps.factory,
        q,
        spec,
        ItemPayload(household_id=hid, item_id=uuid.uuid4()),
        idempotency_key=key,
    )
    assert len(q.calls) == 1
    return res, q.calls[0][1]


async def _run(deps: RunnerDeps, hid: uuid.UUID, run_id: uuid.UUID) -> JobRun:
    async with scoped_session(deps.factory, hid, None) as s:
        return (await s.execute(select(JobRun).where(JobRun.id == run_id))).scalar_one()


async def _count(deps: RunnerDeps, hid: uuid.UUID, model: Any, *where: Any) -> int:
    async with scoped_session(deps.factory, hid, None) as s:
        stmt = select(func.count()).select_from(model).where(*where)
        return int((await s.execute(stmt)).scalar_one())


async def _effects(deps: RunnerDeps, hid: uuid.UUID) -> int:
    return await _count(deps, hid, AuditLog, AuditLog.action == EFFECT_ACTION)


# --------------------------------------------------------------------------- idempotency


async def test_enqueue_twice_runs_once_through_arq(
    deps: RunnerDeps, redis: Redis, jobs_redis_url: str, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    hid, _ = households
    qname = f"test-jobs-{uuid.uuid4().hex}"
    pool = arq_pool(redis, queue_name=qname)
    payload = ItemPayload(household_id=hid, item_id=uuid.uuid4())

    first = await enqueue(deps.factory, pool, effect, payload, idempotency_key="sync:1")
    second = await enqueue(
        deps.factory, pool, effect, payload.model_dump(), idempotency_key="sync:1"
    )
    assert first.created and not second.created
    assert first.run_id == second.run_id
    assert await redis.zcard(qname) == 1  # one arq job, not two
    assert await _count(deps, hid, JobRun) == 1

    worker_pool = arq_pool(Redis.from_url(jobs_redis_url), queue_name=qname)
    worker = Worker(
        functions=[arq_function(effect)],
        redis_pool=worker_pool,
        queue_name=qname,
        burst=True,
        poll_delay=0.01,
        handle_signals=False,
        ctx={CTX_KEY: deps},
        job_serializer=queue.dumps,
        job_deserializer=queue.loads,
    )
    try:
        await worker.main()
    finally:
        await worker_pool.aclose()
    assert worker.jobs_complete == 1
    assert await _effects(deps, hid) == 1
    run = await _run(deps, hid, first.run_id)
    assert (run.status, run.attempts, run.last_error) == ("succeeded", 1, None)

    # Once done, the same key is a no-op: no new arq job, no new side effect.
    third = await enqueue(deps.factory, pool, effect, payload, idempotency_key="sync:1")
    assert (third.run_id, third.created, third.status) == (first.run_id, False, "succeeded")
    assert await redis.zcard(qname) == 0


async def test_duplicate_delivery_is_skipped(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    hid, _ = households
    _, args = await _enqueue(deps, effect, hid)
    assert await execute(deps, effect, args, job_try=1) == "succeeded"
    assert await execute(deps, effect, args, job_try=1) == "skipped"
    assert await _effects(deps, hid) == 1


async def test_duplicate_of_a_queued_run_is_re_pushed_under_the_same_arq_id(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Heals a crash between the job_run commit and the Redis push; arq dedupes on _job_id."""
    hid, _ = households
    q = RecordingQueue()
    payload = {"household_id": str(hid), "item_id": str(uuid.uuid4())}
    a = await enqueue(deps.factory, q, effect, payload, idempotency_key="k")
    b = await enqueue(deps.factory, q, effect, payload, idempotency_key="k")
    assert a.run_id == b.run_id
    assert [c[2] for c in q.calls] == [str(a.run_id), str(a.run_id)]


async def test_same_key_with_a_different_payload_conflicts(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    hid, _ = households
    await _enqueue(deps, effect, hid, key="k")
    with pytest.raises(IdempotencyConflict):
        await _enqueue(deps, effect, hid, key="k")
    assert await _count(deps, hid, JobRun) == 1


async def test_keys_are_scoped_per_household_and_job(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = households
    ra, _ = await _enqueue(deps, effect, a, key="k")
    rb, _ = await _enqueue(deps, effect, b, key="k")
    rc, _ = await _enqueue(deps, flaky, a, key="k")
    assert ra.created and rb.created and rc.created
    assert len({ra.run_id, rb.run_id, rc.run_id}) == 3


# --------------------------------------------------------------------------- malformed payloads


@pytest.mark.parametrize(
    "bad",
    [
        {"household_id": "not-a-uuid", "item_id": "also-not"},
        {"item_id": "00000000-0000-4000-8000-000000000002"},
        {"household_id": "HID", "item_id": 7},
        {"household_id": "HID", "item_id": "ID", "access_token": CANARY},
    ],
)
async def test_malformed_payload_is_rejected_before_enqueue(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID], bad: dict[str, Any]
) -> None:
    hid, _ = households
    payload = {
        k: (str(hid) if v == "HID" else str(uuid.uuid4()) if v == "ID" else v)
        for k, v in bad.items()
    }
    q = RecordingQueue()
    with pytest.raises(InvalidJobPayload):
        await enqueue(deps.factory, q, effect, payload, idempotency_key="k")
    assert q.calls == []
    assert await _count(deps, hid, JobRun) == 0


async def test_bad_idempotency_key_is_rejected_before_enqueue(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    hid, _ = households
    q = RecordingQueue()
    with pytest.raises(InvalidIdempotencyKey):
        await enqueue(
            deps.factory,
            q,
            effect,
            ItemPayload(household_id=hid, item_id=uuid.uuid4()),
            idempotency_key=f"https://example.invalid/?t={CANARY}",
        )
    assert q.calls == []
    assert await _count(deps, hid, JobRun) == 0


@pytest.mark.parametrize(
    "tamper",
    [
        lambda h, r, p: (h, r, p.replace(h, "not-a-uuid")),
        lambda h, r, p: (h, r, p[:-1] + ',"token":"' + CANARY + '"}'),
        lambda h, r, p: (h, r, p.replace(h, str(uuid.uuid4()))),  # household mismatch
        lambda h, r, p: (h, r, '{"household_id": 1}'),
        lambda h, r, p: (h, r, "not json"),
    ],
)
async def test_malformed_payload_is_rejected_before_run(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID], tamper: Any
) -> None:
    hid, _ = households
    ATTEMPTS_SEEN.clear()
    res, args = await _enqueue(deps, effect, hid)
    assert await execute(deps, effect, tamper(*args), job_try=1) == "rejected"
    assert ATTEMPTS_SEEN == []
    run = await _run(deps, hid, res.run_id)
    assert (run.status, run.attempts, run.last_error) == ("failed", 0, "InvalidJobPayload")
    assert await _effects(deps, hid) == 0


@pytest.mark.parametrize(
    "args",
    [(), ("a", "b"), (1, 2, 3), ("not-a-uuid", "not-a-uuid", "{}"), ("a", "b", "c", "d")],
)
async def test_malformed_arguments_are_rejected(deps: RunnerDeps, args: tuple[Any, ...]) -> None:
    assert await execute(deps, effect, args, job_try=1) == "rejected"


def test_job_run_rejects_non_id_payloads_in_the_database(
    app_engine: Engine, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Defense in depth: even a direct INSERT as the app can't store a non-ID payload."""
    hid, _ = households
    bad_payloads = [
        {"household_id": str(hid), "token": CANARY},
        {"household_id": str(hid), "nested": {"id": str(uuid.uuid4())}},
        {"household_id": str(hid), "n": 1},
        {"household_id": str(hid).upper()},
        {"household_id": str(uuid.uuid4())},  # not this row's household
        {},
    ]
    for payload in bad_payloads:
        with app_engine.begin() as c:
            set_ctx(c, hid)
            with pytest.raises(IntegrityError, match="ck_job_run_payload_ids_only"):
                c.execute(
                    JobRun.__table__.insert().values(
                        household_id=hid,
                        job_name="test.effect",
                        idempotency_key="k",
                        payload=payload,
                        max_attempts=1,
                    )
                )
    for column, value, check in [
        ("idempotency_key", "has space", "ck_job_run_idempotency_key_format"),
        ("job_name", "heartbeat", "ck_job_run_job_name_format"),
    ]:
        values = {
            "household_id": hid,
            "job_name": "test.effect",
            "idempotency_key": "k",
            "payload": {"household_id": str(hid)},
            "max_attempts": 1,
            column: value,
        }
        with app_engine.begin() as c:
            set_ctx(c, hid)
            with pytest.raises(IntegrityError, match=check):
                c.execute(JobRun.__table__.insert().values(**values))


# --------------------------------------------------------------------------- retries


async def test_failure_backs_off_then_succeeds(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    hid, _ = households
    res, args = await _enqueue(deps, flaky, hid)
    item = uuid.UUID(args[2].split('"item_id":"')[1][:36])
    FAILS_LEFT[item] = 2

    with pytest.raises(Retry) as first:
        await execute(deps, flaky, args, job_try=1)
    assert first.value.defer_score == 2_000  # base_delay
    run = await _run(deps, hid, res.run_id)
    assert (run.status, run.attempts, run.last_error) == ("retrying", 1, "RuntimeError")
    assert run.next_attempt_at is not None
    assert await _effects(deps, hid) == 0  # the failed attempt's write rolled back

    with pytest.raises(Retry) as second:
        await execute(deps, flaky, args, job_try=2)
    assert second.value.defer_score == 4_000  # doubled

    assert await execute(deps, flaky, args, job_try=3) == "succeeded"
    run = await _run(deps, hid, res.run_id)
    assert (run.status, run.attempts, run.last_error) == ("succeeded", 3, None)
    assert await _effects(deps, hid) == 1


async def test_retries_are_bounded_and_errors_never_store_messages(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    hid, _ = households
    res, args = await _enqueue(deps, flaky, hid)
    item = uuid.UUID(args[2].split('"item_id":"')[1][:36])
    FAILS_LEFT[item] = 99

    for job_try in (1, 2):
        with pytest.raises(Retry):
            await execute(deps, flaky, args, job_try=job_try)
    assert await execute(deps, flaky, args, job_try=3) == "failed"
    run = await _run(deps, hid, res.run_id)
    assert (run.status, run.attempts, run.last_error) == ("failed", 3, "RuntimeError")
    assert await execute(deps, flaky, args, job_try=4) == "skipped"
    assert await _effects(deps, hid) == 0
    async with scoped_session(deps.factory, hid, None) as s:
        dumped = (
            await s.execute(
                text("SELECT row_to_json(j)::text FROM job_run j WHERE id = :i"), {"i": res.run_id}
            )
        ).scalar_one()
    assert CANARY not in dumped


async def test_last_queue_try_records_failure_instead_of_retrying(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """arq would silently drop a Retry past its max_tries; the runner closes the run first."""
    hid, _ = households
    res, args = await _enqueue(deps, flaky, hid)
    item = uuid.UUID(args[2].split('"item_id":"')[1][:36])
    FAILS_LEFT[item] = 1
    assert await execute(deps, flaky, args, job_try=FAST.queue_max_tries) == "failed"
    assert (await _run(deps, hid, res.run_id)).status == "failed"


async def test_orphaned_running_run_is_reclaimed_under_the_lock(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    hid, _ = households
    res, args = await _enqueue(deps, effect, hid)
    assert await job_runs.claim(deps.factory, hid, res.run_id) == 1  # a worker that then died
    ATTEMPTS_SEEN.clear()
    assert await execute(deps, effect, args, job_try=2) == "succeeded"
    assert ATTEMPTS_SEEN == [2]
    assert await _effects(deps, hid) == 1


async def test_orphan_with_no_attempts_left_is_closed_out(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    hid, _ = households
    res, args = await _enqueue(deps, one_shot, hid)
    assert await job_runs.claim(deps.factory, hid, res.run_id) == 1
    assert await execute(deps, one_shot, args, job_try=2) == "skipped"
    run = await _run(deps, hid, res.run_id)
    assert (run.status, run.last_error) == ("failed", "WorkerLost")
    assert await _effects(deps, hid) == 0


async def test_success_mark_rolls_back_the_handler_if_the_run_was_taken_over(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """If the row stops being ``running`` mid-handler, the handler's writes never commit."""
    hid, _ = households

    @REG.job(f"test.taken_over_{uuid.uuid4().hex[:8]}", payload=ItemPayload, retry=FAST)
    async def taken_over(ctx: JobContext, payload: ItemPayload) -> None:
        await _side_effect(ctx, payload)
        async with scoped_session(ctx.factory, ctx.household_id, None) as other:
            await other.execute(
                update(JobRun).where(JobRun.id == ctx.run_id).values(status="succeeded")
            )

    _, args = await _enqueue(deps, taken_over, hid)
    with pytest.raises(Retry):
        await execute(deps, taken_over, args, job_try=1)
    assert await _effects(deps, hid) == 0
    assert await execute(deps, taken_over, args, job_try=2) == "skipped"


# --------------------------------------------------------------------------- household lock


async def test_busy_household_defers_without_using_an_attempt(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = households
    res_a, args_a = await _enqueue(deps, effect, a)
    _, args_b = await _enqueue(deps, effect, b)
    async with household_lock(deps.engine, a) as held:
        assert held
        with pytest.raises(Retry) as deferred:
            await execute(deps, effect, args_a, job_try=1)
        assert deferred.value.defer_score == 1_000  # lock_retry_delay
        run = await _run(deps, a, res_a.run_id)
        assert (run.status, run.attempts) == ("queued", 0)
        # Another household is not blocked.
        assert await execute(deps, effect, args_b, job_try=1) == "succeeded"
    assert await execute(deps, effect, args_a, job_try=2) == "succeeded"
    assert await _effects(deps, a) == 1


async def test_lock_wait_is_bounded(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    hid, _ = households
    res, args = await _enqueue(deps, effect, hid)
    async with household_lock(deps.engine, hid):
        assert await execute(deps, effect, args, job_try=FAST.queue_max_tries) == "failed"
    run = await _run(deps, hid, res.run_id)
    assert (run.status, run.attempts, run.last_error) == ("failed", 0, "HouseholdBusy")


async def test_one_job_at_a_time_per_household(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = households
    _, a1 = await _enqueue(deps, slow, a, key="a1")
    _, a2 = await _enqueue(deps, slow, a, key="a2")
    RUNNING.update(now=0, max=0)
    results = await asyncio.gather(
        execute(deps, slow, a1, job_try=1),
        execute(deps, slow, a2, job_try=1),
        return_exceptions=True,
    )
    assert sorted(type(r).__name__ for r in results) == ["Retry", "str"]
    assert RUNNING["max"] == 1

    _, b1 = await _enqueue(deps, slow, b, key="b1")
    _, a3 = await _enqueue(deps, slow, a, key="a3")
    RUNNING.update(now=0, max=0)
    both = await asyncio.gather(
        execute(deps, slow, a3, job_try=1), execute(deps, slow, b1, job_try=1)
    )
    assert both == ["succeeded", "succeeded"]
    assert RUNNING["max"] == 2  # different households run in parallel


async def test_lock_is_released_after_a_failure(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    hid, _ = households
    _, args = await _enqueue(deps, flaky, hid)
    FAILS_LEFT[uuid.UUID(args[2].split('"item_id":"')[1][:36])] = 1
    with pytest.raises(Retry):
        await execute(deps, flaky, args, job_try=1)
    async with household_lock(deps.engine, hid) as held:
        assert held


# --------------------------------------------------------------------------- RLS


async def test_job_runs_are_invisible_to_other_households(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = households
    res, args = await _enqueue(deps, effect, a)
    assert await _count(deps, b, JobRun) == 0
    assert await _count(deps, a, JobRun) == 1
    # A delivery naming household B for A's run touches nothing.
    forged = (str(b), args[1], args[2].replace(str(a), str(b)))
    assert await execute(deps, effect, forged, job_try=1) == "skipped"
    assert (await _run(deps, a, res.run_id)).status == "queued"
    assert await _effects(deps, b) == 0
