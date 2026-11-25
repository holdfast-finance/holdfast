"""Job runs against real Postgres (holdfast_app, RLS) and Redis: idempotency, lock, retries.

Acceptance (ROADMAP F2): a job enqueued twice gives one side effect; a malformed (non-UUID)
payload is rejected before enqueue and before run. F2b (Bulwark's #24 follow-ups): enqueue
authorizes against the caller, timeouts and cancellations never leave a run ``running``, stale
attempts can't mark a re-claimed run, and the handler runs on the stored payload. F2b (Bulwark's
#32 block): a job that writes a row needs the requester's write rights on it, side sessions stay
in the run's household, and runs arq lost are redelivered without ever running twice.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any

import pytest
from arq import Retry, Worker
from redis.asyncio import Redis
from sqlalchemy import Engine, event, func, select, text, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db_context import is_household_wide, scoped_session, unscoped_session
from app.jobs import queue
from app.jobs.lock import LOCK_NAMESPACE, household_lock, lock_key
from app.jobs.queue import arq_pool
from app.jobs.registry import (
    HouseholdSessions,
    InvalidIdempotencyKey,
    InvalidJobPayload,
    JobContext,
    JobPayload,
    JobRegistry,
    JobSpec,
    Ref,
    RetryPolicy,
)
from app.jobs.runner import (
    CANCELLED,
    CTX_KEY,
    RunnerDeps,
    arq_function,
    enqueue_household_wide,
    execute,
    sweep_all,
    sweep_household,
)
from app.models import Account, AppUser, AuditLog, JobRun, Person
from app.services import jobs as job_runs
from app.services.errors import ApiError
from app.services.jobs import (
    EnqueueResult,
    IdempotencyConflict,
    JobReferenceNotVisible,
    RunLost,
    enqueue,
)
from tests.models.conftest import set_ctx

pytestmark = pytest.mark.services

EFFECT_ACTION = "test.job_side_effect"
CANARY = "access-sandbox-canary-0000"


class ItemPayload(JobPayload):
    item_id: Annotated[uuid.UUID, Ref(Account)]


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
    """Records every push. Unlike arq it never dedupes, so tests see the worst case."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], str | None]] = []
        self.defers: list[datetime | None] = []

    async def enqueue_job(
        self,
        function: str,
        *args: Any,
        _job_id: str | None = None,
        _defer_until: datetime | None = None,
    ) -> Any:
        self.calls.append((function, args, _job_id))
        self.defers.append(_defer_until)
        return object()


async def _account(
    deps: RunnerDeps,
    hid: uuid.UUID,
    *,
    owner: uuid.UUID | None = None,
    visibility: str = "shared",
) -> uuid.UUID:
    """A synthetic account (joint unless ``owner``), inserted household-wide like a sync job."""
    account_id = uuid.uuid4()
    async with scoped_session(deps.factory, hid, None, household_wide=True) as s:
        s.add(
            Account(
                id=account_id,
                household_id=hid,
                owner_person_id=owner,
                source="manual",
                name="Example Checking",
                mask="0000",
                type="depository",
                visibility=visibility,
            )
        )
    return account_id


async def _person(deps: RunnerDeps, hid: uuid.UUID, name: str) -> uuid.UUID:
    person_id = uuid.uuid4()
    async with scoped_session(deps.factory, hid, None) as s:
        s.add(Person(id=person_id, household_id=hid, display_name=name, filing_role="primary"))
    return person_id


async def _enqueue(
    deps: RunnerDeps,
    spec: JobSpec[ItemPayload],
    hid: uuid.UUID,
    key: str = "k1",
    *,
    item: uuid.UUID | None = None,
) -> tuple[EnqueueResult, tuple[Any, ...]]:
    q = RecordingQueue()
    res = await enqueue(
        deps.factory,
        q,
        spec,
        ItemPayload(household_id=hid, item_id=item or await _account(deps, hid)),
        household_id=hid,
        requested_by=None,
        idempotency_key=key,
    )
    assert len(q.calls) == 1
    return res, q.calls[0][1]


def _as(hid: uuid.UUID, person: uuid.UUID | None = None) -> dict[str, Any]:
    """The authenticated caller: household plus requesting person (None: a system caller)."""
    return {"household_id": hid, "requested_by": person}


def _item(args: tuple[Any, ...]) -> uuid.UUID:
    return uuid.UUID(args[2].split('"item_id":"')[1][:36])


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
    payload = ItemPayload(household_id=hid, item_id=await _account(deps, hid))
    who = {"household_id": hid, "requested_by": None}

    first = await enqueue(deps.factory, pool, effect, payload, **who, idempotency_key="sync:1")
    second = await enqueue(
        deps.factory, pool, effect, payload.model_dump(), **who, idempotency_key="sync:1"
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
    third = await enqueue(deps.factory, pool, effect, payload, **who, idempotency_key="sync:1")
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
    payload = {"household_id": str(hid), "item_id": str(await _account(deps, hid))}
    a = await enqueue(deps.factory, q, effect, payload, **_as(hid), idempotency_key="k")
    b = await enqueue(deps.factory, q, effect, payload, **_as(hid), idempotency_key="k")
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
        await enqueue(deps.factory, q, effect, payload, **_as(hid), idempotency_key="k")
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
            **_as(hid),
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
    item = _item(args)
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
    item = _item(args)
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
    item = _item(args)
    FAILS_LEFT[item] = 1
    assert await execute(deps, flaky, args, job_try=FAST.queue_max_tries) == "failed"
    assert (await _run(deps, hid, res.run_id)).status == "failed"


async def test_orphaned_running_run_is_reclaimed_under_the_lock(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    hid, _ = households
    res, args = await _enqueue(deps, effect, hid)
    claimed = await job_runs.claim(deps.factory, hid, res.run_id, effect)
    assert claimed is not None and claimed.attempt == 1  # a worker that then died
    ATTEMPTS_SEEN.clear()
    # Not before it is older than the job's timeout + arq's margin: it might still be live.
    assert await execute(deps, effect, args, job_try=2) == "skipped"
    assert ATTEMPTS_SEEN == []
    await _age(deps, hid, res.run_id, started_at=effect.orphan_after + timedelta(seconds=1))
    assert await execute(deps, effect, args, job_try=3) == "succeeded"
    assert ATTEMPTS_SEEN == [2]
    assert await _effects(deps, hid) == 1


async def test_orphan_with_no_attempts_left_is_closed_out(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    hid, _ = households
    res, args = await _enqueue(deps, one_shot, hid)
    assert await job_runs.claim(deps.factory, hid, res.run_id, one_shot) is not None
    # A live last attempt is never closed out by a second delivery.
    assert await execute(deps, one_shot, args, job_try=2) == "skipped"
    assert (await _run(deps, hid, res.run_id)).status == "running"
    await _age(deps, hid, res.run_id, started_at=one_shot.orphan_after + timedelta(seconds=1))
    assert await execute(deps, one_shot, args, job_try=3) == "skipped"
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
        async with ctx.factory() as other:
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
    FAILS_LEFT[_item(args)] = 1
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


# --------------------------------------------------------------------------- F2b: enqueue authority


async def test_enqueue_for_another_household_is_rejected(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Confused deputy: the payload's household_id is never trusted over the caller's."""
    a, b = households
    b_item = await _account(deps, b)
    q = RecordingQueue()
    forged = ItemPayload(household_id=b, item_id=b_item)
    with pytest.raises(JobReferenceNotVisible) as info:
        await enqueue(deps.factory, q, effect, forged, **_as(a), idempotency_key="k")
    assert str(b) not in str(info.value)
    # The caller's own household, but naming another household's row: RLS hides it.
    smuggled = ItemPayload(household_id=a, item_id=b_item)
    with pytest.raises(JobReferenceNotVisible) as info:
        await enqueue(deps.factory, q, effect, smuggled, **_as(a), idempotency_key="k")
    assert str(b_item) not in str(info.value) and "item_id" in str(info.value)
    assert q.calls == []
    assert await _count(deps, a, JobRun) == 0
    assert await _count(deps, b, JobRun) == 0


async def test_person_cannot_enqueue_on_rows_they_cannot_see(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    hid, _ = households
    alex = await _person(deps, hid, "Alex Example")
    sam = await _person(deps, hid, "Sam Example")
    private = await _account(deps, hid, owner=alex, visibility="private")
    shared = await _account(deps, hid, owner=alex, visibility="shared")
    q = RecordingQueue()

    def job(item: uuid.UUID) -> ItemPayload:
        return ItemPayload(household_id=hid, item_id=item)

    for requester in (sam, None):  # another person, and a person-less system caller
        with pytest.raises(JobReferenceNotVisible):
            await enqueue(
                deps.factory, q, effect, job(private), **_as(hid, requester), idempotency_key="p"
            )
    assert q.calls == [] and await _count(deps, hid, JobRun) == 0

    mine = await enqueue(
        deps.factory, q, effect, job(private), **_as(hid, alex), idempotency_key="p"
    )
    assert mine.created
    # Sam can't use the key to reach Alex's run either: the check runs before the dedupe.
    with pytest.raises(JobReferenceNotVisible):
        await enqueue(deps.factory, q, effect, job(private), **_as(hid, sam), idempotency_key="p")
    # Alex's shared account is visible to Sam, but this job writes it: see the next tests.
    with pytest.raises(JobReferenceNotVisible):
        await enqueue(deps.factory, q, effect, job(shared), **_as(hid, sam), idempotency_key="s")
    assert len(q.calls) == 1


async def test_payloads_without_references_need_no_lookup(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    hid, other = households
    reg = JobRegistry()

    @reg.job("test.household_only", payload=JobPayload)
    async def household_only(ctx: JobContext, payload: JobPayload) -> None:
        return None

    q = RecordingQueue()
    res = await enqueue(
        deps.factory, q, household_only, {"household_id": str(hid)}, **_as(hid), idempotency_key="h"
    )
    assert res.created
    with pytest.raises(JobReferenceNotVisible):
        await enqueue(
            deps.factory,
            q,
            household_only,
            {"household_id": str(hid)},
            **_as(other),
            idempotency_key="h",
        )


# ------------------------------------------------------------------------- F2b: never stuck running

GATES: dict[uuid.UUID, asyncio.Event] = {}
QUICK = timedelta(milliseconds=200)


async def _hang(ctx: JobContext, payload: ItemPayload) -> None:
    await _side_effect(ctx, payload)  # uncommitted: must roll back
    gate = GATES.get(payload.item_id)
    if gate is not None:
        gate.set()
    await asyncio.sleep(30)


@REG.job("test.hangs", payload=ItemPayload, retry=FAST, timeout=QUICK)
async def hangs(ctx: JobContext, payload: ItemPayload) -> None:
    await _hang(ctx, payload)


@REG.job("test.hangs_once", payload=ItemPayload, retry=RetryPolicy(max_attempts=1), timeout=QUICK)
async def hangs_once(ctx: JobContext, payload: ItemPayload) -> None:
    await _hang(ctx, payload)


@REG.job("test.blocks", payload=ItemPayload, retry=FAST)
async def blocks(ctx: JobContext, payload: ItemPayload) -> None:
    await _hang(ctx, payload)


@REG.job("test.blocks_once", payload=ItemPayload, retry=RetryPolicy(max_attempts=1))
async def blocks_once(ctx: JobContext, payload: ItemPayload) -> None:
    await _hang(ctx, payload)


async def test_timeout_is_recorded_and_retried_not_left_running(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    hid, _ = households
    res, args = await _enqueue(deps, hangs, hid)
    with pytest.raises(Retry) as retry:
        await asyncio.wait_for(execute(deps, hangs, args, job_try=1), timeout=5)
    assert retry.value.defer_score == 2_000
    run = await _run(deps, hid, res.run_id)
    assert (run.status, run.attempts, run.last_error) == ("retrying", 1, "TimeoutError")
    assert await _effects(deps, hid) == 0
    async with household_lock(deps.engine, hid) as held:
        assert held  # released


async def test_timeout_on_the_last_attempt_fails_the_run(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    hid, _ = households
    res, args = await _enqueue(deps, hangs_once, hid)
    assert await asyncio.wait_for(execute(deps, hangs_once, args, job_try=1), timeout=5) == "failed"
    run = await _run(deps, hid, res.run_id)
    assert (run.status, run.attempts, run.last_error) == ("failed", 1, "TimeoutError")
    # The same key now returns a terminal run instead of a phantom ``running`` one.
    q = RecordingQueue()
    payload = ItemPayload(household_id=hid, item_id=_item(args))
    again = await enqueue(deps.factory, q, hangs_once, payload, **_as(hid), idempotency_key="k1")
    assert (again.run_id, again.created, again.status) == (res.run_id, False, "failed")
    assert q.calls == []


@pytest.mark.parametrize(
    ("spec", "status"), [(blocks, "retrying"), (blocks_once, "failed")], ids=["retry", "final"]
)
async def test_cancellation_records_state_and_propagates(
    deps: RunnerDeps,
    households: tuple[uuid.UUID, uuid.UUID],
    spec: JobSpec[ItemPayload],
    status: str,
) -> None:
    """Worker shutdown or arq's own timeout cancel the task: the row must leave ``running``."""
    hid, _ = households
    res, args = await _enqueue(deps, spec, hid)
    GATES[_item(args)] = started = asyncio.Event()
    task = asyncio.create_task(execute(deps, spec, args, job_try=1))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    run = await _run(deps, hid, res.run_id)
    assert (run.status, run.attempts, run.last_error) == (status, 1, "CancelledError")
    assert await _effects(deps, hid) == 0
    async with household_lock(deps.engine, hid) as held:
        assert held


def test_arq_timeout_is_a_backstop_above_the_runner_timeout() -> None:
    fn = arq_function(hangs)
    assert fn.timeout_s == (QUICK + timedelta(seconds=60)).total_seconds()
    assert fn.keep_result_s == 0


# --------------------------------------------------------------------------- F2b: stale attempts


async def test_a_stale_attempt_cannot_mark_a_re_claimed_run(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    hid, _ = households
    res, _ = await _enqueue(deps, effect, hid)
    first = await job_runs.claim(deps.factory, hid, res.run_id, effect)
    await _age(deps, hid, res.run_id, started_at=effect.orphan_after + timedelta(seconds=1))
    second = await job_runs.claim(deps.factory, hid, res.run_id, effect)  # orphan re-claim
    assert first is not None and second is not None
    assert (first.attempt, second.attempt) == (1, 2)

    with pytest.raises(RunLost):
        async with scoped_session(deps.factory, hid, None) as s:
            await job_runs.mark_succeeded(s, res.run_id, first.attempt)
    stale = timedelta(seconds=1)
    await job_runs.mark_retrying(deps.factory, hid, res.run_id, first.attempt, "Stale", stale)
    await job_runs.mark_failed(deps.factory, hid, res.run_id, first.attempt, "Stale")
    run = await _run(deps, hid, res.run_id)
    assert (run.status, run.attempts, run.last_error) == ("running", 2, None)

    async with scoped_session(deps.factory, hid, None) as s:
        await job_runs.mark_succeeded(s, res.run_id, second.attempt)
    assert (await _run(deps, hid, res.run_id)).status == "succeeded"


# ------------------------------------------------------------------------- F2b: stored payload wins


async def test_a_swapped_redis_payload_is_ignored_for_the_stored_one(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Someone with Redis write access swaps in another (valid) id of the same household."""
    hid, _ = households
    res, args = await _enqueue(deps, effect, hid)
    authorized, swapped = _item(args), await _account(deps, hid)
    tampered = (args[0], args[1], args[2].replace(str(authorized), str(swapped)))
    assert tampered != args
    assert await execute(deps, effect, tampered, job_try=1) == "succeeded"
    async with scoped_session(deps.factory, hid, None) as s:
        touched = (
            (await s.execute(select(AuditLog.entity_id).where(AuditLog.action == EFFECT_ACTION)))
            .scalars()
            .all()
        )
    assert touched == [str(authorized)]
    assert (await _run(deps, hid, res.run_id)).status == "succeeded"


async def test_a_run_delivered_under_another_job_name_is_not_claimed(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    hid, _ = households
    res, args = await _enqueue(deps, effect, hid)
    assert await execute(deps, flaky, args, job_try=1) == "skipped"
    run = await _run(deps, hid, res.run_id)
    assert (run.status, run.attempts) == ("queued", 0)
    assert await execute(deps, effect, args, job_try=1) == "succeeded"


# ------------------------------------------------------------- F2b: write references (Bulwark #32)


class ReadPayload(JobPayload):
    item_id: Annotated[uuid.UUID, Ref(Account, access="read")]


@REG.job("test.reads", payload=ReadPayload, retry=FAST)
async def reads(ctx: JobContext, payload: ReadPayload) -> None:
    return None


async def _household_people(
    deps: RunnerDeps, hid: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID, dict[str, uuid.UUID]]:
    """Alex and Sam, plus one account per visibility case (all synthetic)."""
    alex = await _person(deps, hid, "Alex Example")
    sam = await _person(deps, hid, "Sam Example")
    accounts = {
        "alex_private": await _account(deps, hid, owner=alex, visibility="private"),
        "alex_shared": await _account(deps, hid, owner=alex, visibility="shared"),
        "alex_summary": await _account(deps, hid, owner=alex, visibility="summary_only"),
        "joint": await _account(deps, hid),
    }
    return alex, sam, accounts


async def test_the_row_lock_applies_the_update_policy(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """The mechanism itself, on this Postgres: Sam can SELECT Alex's shared account, but the
    same SELECT with FOR KEY SHARE finds nothing, exactly as his direct UPDATE changes nothing."""
    hid, _ = households
    alex, sam, acct = await _household_people(deps, hid)
    shared = acct["alex_shared"]
    plain = select(Account.id).where(Account.id == shared)
    locked = plain.with_for_update(read=True, key_share=True)
    assert "FOR KEY SHARE" in str(locked.compile(dialect=postgresql.dialect()))

    async with scoped_session(deps.factory, hid, sam) as s:
        assert (await s.execute(plain)).scalars().all() == [shared]  # read policy: visible
        assert (await s.execute(locked)).scalars().all() == []  # write policy: filtered out
        updated = await s.execute(
            update(Account).where(Account.id == shared).values(name="Example Renamed")
        )
        assert updated.rowcount == 0  # the policy the lock mirrors
    async with scoped_session(deps.factory, hid, alex) as s:
        assert (await s.execute(locked)).scalars().all() == [shared]
    joint = select(Account.id).where(Account.id == acct["joint"])
    async with scoped_session(deps.factory, hid, sam) as s:  # joint: Sam may write it
        locked_joint = joint.with_for_update(read=True, key_share=True)
        assert (await s.execute(locked_joint)).scalars().all() == [acct["joint"]]


async def test_enqueue_needs_write_rights_on_written_rows(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Confused deputy at the person boundary: the job would write Alex's shared account
    household-wide, which RLS refuses Sam directly. So enqueue refuses him too."""
    hid, _ = households
    alex, sam, acct = await _household_people(deps, hid)
    q = RecordingQueue()

    async def go(item: str, who: uuid.UUID | None, key: str) -> EnqueueResult:
        payload = ItemPayload(household_id=hid, item_id=acct[item])
        return await enqueue(deps.factory, q, effect, payload, **_as(hid, who), idempotency_key=key)

    for item in ("alex_shared", "alex_summary", "alex_private"):
        with pytest.raises(JobReferenceNotVisible) as info:
            await go(item, sam, f"sam-{item}")
        assert str(acct[item]) not in str(info.value)
    assert q.calls == [] and await _count(deps, hid, JobRun) == 0

    assert (await go("alex_shared", alex, "alex-shared")).created
    assert (await go("joint", alex, "alex-joint")).created
    assert (await go("joint", sam, "sam-joint")).created
    assert len(q.calls) == 3


async def test_a_read_only_reference_needs_visibility_only(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    hid, _ = households
    _, sam, acct = await _household_people(deps, hid)
    q = RecordingQueue()

    async def go(item: str, key: str) -> EnqueueResult:
        payload = ReadPayload(household_id=hid, item_id=acct[item])
        return await enqueue(deps.factory, q, reads, payload, **_as(hid, sam), idempotency_key=key)

    assert (await go("alex_shared", "r1")).created
    assert (await go("alex_summary", "r2")).created
    assert (await go("joint", "r3")).created
    with pytest.raises(JobReferenceNotVisible):
        await go("alex_private", "r4")  # still invisible to Sam
    assert len(q.calls) == 3


def test_ref_access_defaults_to_write_and_rejects_anything_else() -> None:
    assert ItemPayload.refs["item_id"].access == "write"
    assert ReadPayload.refs["item_id"].access == "read"
    with pytest.raises(TypeError):
        Ref(Account, access="delete")  # type: ignore[arg-type]


async def test_system_callers_get_the_person_less_view(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """``requested_by=None``: write joint rows only; read shared, summary-only and joint rows."""
    hid, _ = households
    _, _, acct = await _household_people(deps, hid)
    q = RecordingQueue()
    system = _as(hid, None)

    async def write(item: str) -> EnqueueResult:
        payload = ItemPayload(household_id=hid, item_id=acct[item])
        return await enqueue(deps.factory, q, effect, payload, **system, idempotency_key=f"w{item}")

    async def read(item: str) -> EnqueueResult:
        payload = ReadPayload(household_id=hid, item_id=acct[item])
        return await enqueue(deps.factory, q, reads, payload, **system, idempotency_key=f"r{item}")

    assert (await write("joint")).created
    for item in ("alex_shared", "alex_summary", "alex_private"):
        with pytest.raises(JobReferenceNotVisible):
            await write(item)
    for item in ("joint", "alex_shared", "alex_summary"):
        assert (await read(item)).created
    with pytest.raises(JobReferenceNotVisible):
        await read("alex_private")


async def test_worker_schedulers_may_name_any_row_of_their_household_only(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    hid, other = households
    _, _, acct = await _household_people(deps, hid)
    q = RecordingQueue()
    mine = ItemPayload(household_id=hid, item_id=acct["alex_private"])
    res = await enqueue_household_wide(
        deps.factory, q, effect, mine, household_id=hid, idempotency_key="nightly"
    )
    assert res.created and len(q.calls) == 1
    elsewhere = await _account(deps, other)
    with pytest.raises(JobReferenceNotVisible):
        await enqueue_household_wide(
            deps.factory,
            q,
            effect,
            ItemPayload(household_id=hid, item_id=elsewhere),
            household_id=hid,
            idempotency_key="cross",
        )
    with pytest.raises(JobReferenceNotVisible):
        await enqueue_household_wide(
            deps.factory, q, effect, mine, household_id=other, idempotency_key="forged"
        )
    assert len(q.calls) == 1


# ------------------------------------------------------------- F2b: the context is household-bound

SIDE: dict[str, Any] = {}


@REG.job("test.side_session", payload=ItemPayload, retry=RetryPolicy(max_attempts=1))
async def side_session(ctx: JobContext, payload: ItemPayload) -> None:
    SIDE.clear()
    async with ctx.factory() as s:
        SIDE["wide"] = await is_household_wide(s)
        SIDE["runs"] = (
            await s.execute(select(func.count()).select_from(JobRun).where(JobRun.id == ctx.run_id))
        ).scalar_one()
    other: uuid.UUID = SIDE_OTHER["household"]
    try:
        async with ctx.factory() as s:
            s.add(
                AuditLog(id=uuid.uuid4(), household_id=other, action=EFFECT_ACTION, entity="item")
            )
    except DBAPIError as exc:
        SIDE["foreign_write"] = type(exc).__name__
    else:
        SIDE["foreign_write"] = "allowed"


SIDE_OTHER: dict[str, uuid.UUID] = {}


async def test_side_sessions_are_bound_to_the_runs_household(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    hid, other = households
    SIDE_OTHER["household"] = other
    _, args = await _enqueue(deps, side_session, hid)
    assert await execute(deps, side_session, args, job_try=1) == "succeeded"
    assert SIDE["wide"] is True and SIDE["runs"] == 1
    assert SIDE["foreign_write"] == "ProgrammingError"  # RLS WITH CHECK (42501)
    assert await _count(deps, other, AuditLog, AuditLog.action == EFFECT_ACTION) == 0
    assert set(inspect.signature(HouseholdSessions.__call__).parameters) == {"self"}


# ------------------------------------------------------------- F2b: lost runs are redelivered


async def _age(deps: RunnerDeps, hid: uuid.UUID, run_id: uuid.UUID, **values: Any) -> None:
    """Backdate a run's timestamps (``column=timedelta`` ago) to fake the passage of time."""
    async with scoped_session(deps.factory, hid, None) as s:
        await s.execute(
            update(JobRun)
            .where(JobRun.id == run_id)
            .values({k: func.now() - v for k, v in values.items()})
        )


async def _strand(deps: RunnerDeps, hid: uuid.UUID, status: str, spec: JobSpec[ItemPayload]) -> Any:
    """A run in ``status`` that arq no longer holds, far enough past due for the sweeper."""
    res, args = await _enqueue(deps, spec, hid, key=f"lost-{status}")
    old = timedelta(hours=1)
    if status == "queued":  # crash between the commit and the push
        await _age(deps, hid, res.run_id, enqueued_at=old)
    else:
        claimed = await job_runs.claim(deps.factory, hid, res.run_id, spec)
        assert claimed is not None
        if status == "retrying":  # record_cancelled ran, then arq's backstop finished the job
            await job_runs.mark_retrying(
                deps.factory, hid, res.run_id, claimed.attempt, CANCELLED, timedelta(0)
            )
            await _age(deps, hid, res.run_id, next_attempt_at=old)
        else:  # the worker died hard mid-run
            await _age(deps, hid, res.run_id, started_at=old)
    return res, args


async def test_duplicate_enqueue_re_pushes_unfinished_runs(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    hid, _ = households
    q = RecordingQueue()
    for status in ("retrying", "running"):
        res, args = await _strand(deps, hid, status, effect)
        payload = ItemPayload(household_id=hid, item_id=_item(args))
        again = await enqueue(
            deps.factory, q, effect, payload, **_as(hid), idempotency_key=f"lost-{status}"
        )
        assert (again.run_id, again.created, again.status) == (res.run_id, False, status)
        assert q.calls[-1][2] == str(res.run_id)
        run = await _run(deps, hid, res.run_id)
        assert q.defers[-1] == (run.next_attempt_at if status == "retrying" else None)
    assert len(q.calls) == 2


async def test_sweeper_re_pushes_only_runs_arq_has_lost(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    hid, _ = households
    lost = {s: (await _strand(deps, hid, s, effect))[0].run_id for s in ("queued", "retrying")}
    lost["running"] = (await _strand(deps, hid, "running", effect))[0].run_id

    fresh, _ = await _enqueue(deps, effect, hid, key="fresh")  # arq still has it
    live, _ = await _enqueue(deps, effect, hid, key="live")
    assert await job_runs.claim(deps.factory, hid, live.run_id, effect) is not None
    # Running for longer than the margins but within its own timeout + margins: maybe live.
    long, _ = await _enqueue(deps, slow_timeout, hid, key="long")
    assert await job_runs.claim(deps.factory, hid, long.run_id, slow_timeout) is not None
    await _age(deps, hid, long.run_id, started_at=timedelta(minutes=30))
    later, _ = await _enqueue(deps, flaky, hid, key="later")
    claimed = await job_runs.claim(deps.factory, hid, later.run_id, flaky)
    assert claimed is not None
    await job_runs.mark_retrying(
        deps.factory, hid, later.run_id, claimed.attempt, "RuntimeError", timedelta(hours=1)
    )
    done, done_args = await _enqueue(deps, effect, hid, key="done")
    assert await execute(deps, effect, done_args, job_try=1) == "succeeded"
    await _age(deps, hid, done.run_id, enqueued_at=timedelta(hours=1))

    q = RecordingQueue()
    assert await sweep_household(deps, q, hid, REG) == 3
    assert {c[2] for c in q.calls} == {str(r) for r in lost.values()}
    by_id = dict(zip((c[2] for c in q.calls), q.defers, strict=True))
    retrying = await _run(deps, hid, lost["retrying"])
    assert by_id[str(lost["retrying"])] == retrying.next_attempt_at
    assert by_id[str(lost["queued"])] is None and by_id[str(lost["running"])] is None
    # The sweeper only pushes: no row changed state.
    assert (await _run(deps, hid, lost["running"])).attempts == 1
    assert (await _run(deps, hid, fresh.run_id)).status == "queued"


@REG.job("test.slow_timeout", payload=ItemPayload, retry=FAST, timeout=timedelta(hours=1))
async def slow_timeout(ctx: JobContext, payload: ItemPayload) -> None:
    return None


async def test_sweeper_fails_a_pending_run_whose_stored_payload_no_longer_validates(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    hid, _ = households
    res, _ = await _strand(deps, hid, "queued", effect)
    q = RecordingQueue()
    assert await sweep_household(deps, q, hid, REG_RENAMED) == 0
    run = await _run(deps, hid, res.run_id)
    assert (run.status, run.last_error) == ("failed", "InvalidJobPayload")
    assert q.calls == []


class RenamedPayload(JobPayload):  # a deploy renamed the field
    account_id: Annotated[uuid.UUID, Ref(Account)]


REG_RENAMED = JobRegistry()


@REG_RENAMED.job("test.effect", payload=RenamedPayload)
async def effect_renamed(ctx: JobContext, payload: RenamedPayload) -> None:
    return None


EFFECTS_SLOW: dict[str, int] = {"now": 0, "max": 0}


@REG.job("test.slow_effect", payload=ItemPayload, retry=FAST)
async def slow_effect(ctx: JobContext, payload: ItemPayload) -> None:
    EFFECTS_SLOW["now"] += 1
    EFFECTS_SLOW["max"] = max(EFFECTS_SLOW["max"], EFFECTS_SLOW["now"])
    try:
        await asyncio.sleep(0.2)
        await _side_effect(ctx, payload)
    finally:
        EFFECTS_SLOW["now"] -= 1


@pytest.mark.parametrize("status", ["queued", "retrying", "running"])
async def test_redelivered_runs_never_run_twice(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID], status: str
) -> None:
    """Every redelivery path at once, with no arq dedupe (RecordingQueue): a duplicate
    enqueue and two sweeps push the same lost run three times. Run every delivery
    concurrently, then the deferred ones again: one handler run, one side effect."""
    hid, _ = households
    res, args = await _strand(deps, hid, status, slow_effect)
    q = RecordingQueue()
    payload = ItemPayload(household_id=hid, item_id=_item(args))
    await enqueue(
        deps.factory, q, slow_effect, payload, **_as(hid), idempotency_key=f"lost-{status}"
    )
    assert await sweep_household(deps, q, hid, REG) == 1
    assert await sweep_household(deps, q, hid, REG) == 1
    assert len(q.calls) == 3

    EFFECTS_SLOW.update(now=0, max=0)
    deliveries = [c[1] for c in q.calls]
    first = await asyncio.gather(
        *(execute(deps, slow_effect, d, job_try=1) for d in deliveries), return_exceptions=True
    )
    assert [r for r in first if isinstance(r, BaseException) and not isinstance(r, Retry)] == []
    assert [r for r in first if isinstance(r, str)].count("succeeded") == 1
    deferred = [d for d, r in zip(deliveries, first, strict=True) if isinstance(r, Retry)]
    again = [await execute(deps, slow_effect, d, job_try=2) for d in deferred]
    assert set(again) <= {"skipped"}

    assert EFFECTS_SLOW["max"] == 1
    assert await _effects(deps, hid) == 1
    run = await _run(deps, hid, res.run_id)
    assert (run.status, run.attempts) == ("succeeded", 1 if status == "queued" else 2)
    assert await sweep_household(deps, q, hid, REG) == 0  # finished: nothing left to push


async def test_a_run_arqs_backstop_dropped_is_revived_once_through_arq(
    deps: RunnerDeps, redis: Redis, jobs_redis_url: str, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """End to end on Redis: arq finished the job (backstop timeout) after the runner recorded
    ``retrying``. A duplicate enqueue and the sweeper both push; arq keeps one; it runs once."""
    hid, _ = households
    qname = f"test-jobs-{uuid.uuid4().hex}"
    pool = arq_pool(redis, queue_name=qname)
    payload = ItemPayload(household_id=hid, item_id=await _account(deps, hid))
    first = await enqueue(deps.factory, pool, effect, payload, **_as(hid), idempotency_key="bk")
    claimed = await job_runs.claim(deps.factory, hid, first.run_id, effect)
    assert claimed is not None
    await job_runs.mark_retrying(
        deps.factory, hid, first.run_id, claimed.attempt, CANCELLED, timedelta(0)
    )
    await _age(deps, hid, first.run_id, next_attempt_at=timedelta(hours=1))
    # What arq's finish_job does once the backstop fires: the job is gone from Redis.
    await redis.zrem(qname, str(first.run_id))
    await redis.delete(f"arq:job:{first.run_id}", f"arq:in-progress:{first.run_id}")
    assert await redis.zcard(qname) == 0

    again = await enqueue(deps.factory, pool, effect, payload, **_as(hid), idempotency_key="bk")
    assert (again.run_id, again.status) == (first.run_id, "retrying")
    assert await sweep_household(deps, pool, hid, REG) == 1
    assert await redis.zcard(qname) == 1  # arq dropped the second push

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
    assert (run.status, run.attempts) == ("succeeded", 2)


async def test_sweep_all_covers_every_household_even_without_a_login(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    with_login, without = households
    person = await _person(deps, with_login, "Alex Example")
    async with unscoped_session(deps.factory) as s:
        s.add(
            AppUser(
                household_id=with_login,
                person_id=person,
                email=f"sweep-{uuid.uuid4().hex[:8]}@example.com",
                role="owner",
            )
        )
    a, _ = await _strand(deps, with_login, "queued", effect)
    b, _ = await _strand(deps, without, "queued", effect)
    q = RecordingQueue()
    await sweep_all(deps, q, REG)
    pushed = {c[2] for c in q.calls}
    assert {str(a.run_id), str(b.run_id)} <= pushed  # list_household_ids(), not app_user


# ------------------------------------------------------- F2c: a lost lock can't start a second run

GATED: dict[str, Any] = {}


@REG.job("test.gated", payload=ItemPayload, retry=FAST)
async def gated(ctx: JobContext, payload: ItemPayload) -> None:
    GATED["starts"] += 1
    GATED["started"].set()
    await GATED["release"].wait()
    await _side_effect(ctx, payload)


_LOCK_HOLDER = text(
    "SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND granted"
    " AND classid = CAST(:ns AS int)::oid AND objid = CAST(:key AS int)::oid"
)


async def _lock_holders(deps: RunnerDeps, hid: uuid.UUID) -> list[int]:
    params = {"ns": LOCK_NAMESPACE, "key": lock_key(hid)}
    async with deps.engine.connect() as c:
        return list((await c.execute(_LOCK_HOLDER, params)).scalars().all())


async def test_a_re_push_after_the_lock_connection_drops_cannot_start_a_second_run(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Postgres frees the advisory lock when its connection dies, while the handler (on its own
    connection) keeps running. A redelivery then gets the lock, but the row is ``running`` and
    younger than ``orphan_after``, so the claim refuses it: one execution, one side effect."""
    hid, _ = households
    GATED.update(starts=0, started=asyncio.Event(), release=asyncio.Event())
    res, args = await _enqueue(deps, gated, hid)
    first = asyncio.create_task(execute(deps, gated, args, job_try=1))
    try:
        await asyncio.wait_for(GATED["started"].wait(), timeout=5)
        (pid,) = await _lock_holders(deps, hid)
        async with deps.engine.connect() as c:
            assert (await c.execute(text("SELECT pg_terminate_backend(:p)"), {"p": pid})).scalar()
        for _ in range(100):  # the backend exits asynchronously
            if not await _lock_holders(deps, hid):
                break
            await asyncio.sleep(0.02)
        assert await _lock_holders(deps, hid) == []

        assert await execute(deps, gated, args, job_try=2) == "skipped"  # lock free, row live
        assert GATED["starts"] == 1
        run = await _run(deps, hid, res.run_id)
        assert (run.status, run.attempts) == ("running", 1)
    finally:
        GATED["release"].set()
        # The first execution still records its outcome; only releasing its dead lock fails.
        await asyncio.gather(first, return_exceptions=True)
    run = await _run(deps, hid, res.run_id)
    assert (run.status, run.attempts) == ("succeeded", 1)
    assert await _effects(deps, hid) == 1 and GATED["starts"] == 1


# ------------------------------------------------- F2c: enqueue lock timeout and lock order


async def test_a_locked_reference_answers_busy_after_the_lock_timeout(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    hid, _ = households
    item = await _account(deps, hid)
    q = RecordingQueue()
    loop = asyncio.get_running_loop()
    async with scoped_session(deps.factory, hid, None, household_wide=True) as holder:
        # Another transaction (a sync, say) holds the row FOR UPDATE.
        await holder.execute(select(Account.id).where(Account.id == item).with_for_update())
        started = loop.time()
        # wait_for: without the lock timeout, enqueue would wait on the holder forever. Then this
        # fails with TimeoutError instead of hanging the suite.
        with pytest.raises(ApiError) as info:
            await asyncio.wait_for(
                enqueue(
                    deps.factory,
                    q,
                    effect,
                    ItemPayload(household_id=hid, item_id=item),
                    **_as(hid),
                    idempotency_key="busy",
                ),
                timeout=10,
            )
        waited = loop.time() - started
        # A read-only reference takes no lock, so it isn't held up.
        read = await enqueue(
            deps.factory,
            q,
            reads,
            ReadPayload(household_id=hid, item_id=item),
            **_as(hid),
            idempotency_key="read",
        )
    assert (info.value.code, info.value.status) == ("busy", 409)
    assert str(item) not in info.value.message
    assert job_runs.ENQUEUE_LOCK_TIMEOUT == "2s" and 1.5 <= waited < 10
    assert read.created and len(q.calls) == 1
    assert await _count(deps, hid, JobRun, JobRun.job_name == effect.name) == 0


class PairPayload(JobPayload):
    source_id: Annotated[uuid.UUID, Ref(Account)]
    target_id: Annotated[uuid.UUID, Ref(Account)]


@REG.job("test.pair", payload=PairPayload, retry=FAST)
async def pair(ctx: JobContext, payload: PairPayload) -> None:
    return None


async def test_references_are_locked_in_id_order_under_a_local_lock_timeout(
    deps: RunnerDeps, households: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Two enqueues naming the same rows lock them in the same order, so they can't deadlock."""
    hid, _ = households
    low, high = sorted([await _account(deps, hid), await _account(deps, hid)])
    seen: list[tuple[str, list[uuid.UUID]]] = []

    def record(_c: Any, _cur: Any, statement: str, params: Any, *_a: Any) -> None:
        values = params.values() if isinstance(params, dict) else ()
        seen.append((statement, [v for v in values if v in (low, high)]))

    sync_engine = deps.engine.sync_engine
    event.listen(sync_engine, "before_cursor_execute", record)
    try:
        for key, (source, target) in {"hl": (high, low), "lh": (low, high)}.items():
            seen.clear()
            payload = PairPayload(household_id=hid, source_id=source, target_id=target)
            q = RecordingQueue()
            await enqueue(deps.factory, q, pair, payload, **_as(hid), idempotency_key=key)
            statements = [s for s, _ in seen]
            locks = [ids for s, ids in seen if "FOR KEY SHARE" in s]
            assert locks == [[low], [high]], key
            set_timeout = next(
                i for i, s in enumerate(statements) if "SET LOCAL lock_timeout = '2s'" in s
            )
            assert set_timeout < next(i for i, s in enumerate(statements) if "FOR KEY SHARE" in s)
    finally:
        event.remove(sync_engine, "before_cursor_execute", record)
    # SET LOCAL ends with the transaction: pooled connections go back with the default.
    async with deps.engine.connect() as c:
        assert (await c.execute(text("SHOW lock_timeout"))).scalar_one() == "0"
