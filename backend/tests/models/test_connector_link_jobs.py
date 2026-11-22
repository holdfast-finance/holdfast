"""``job_run.requested_by`` (P4a item 7): written by enqueue, handed to the handler, chained.

Runs enqueue and the runner against real Postgres as holdfast_app (RLS applies). Pushes go to a
recording queue, and each run is executed directly, so no Redis is needed. Synthetic data only.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import create_session_factory
from app.db_context import scoped_session
from app.jobs.registry import JobContext, JobPayload, JobRegistry, Ref, RetryPolicy
from app.jobs.runner import RunnerDeps, enqueue_household_wide, execute
from app.models import Account, JobRun
from app.services.jobs import IdempotencyConflict, enqueue
from tests.models.conftest import delete_household, set_ctx

pytestmark = pytest.mark.services


class AccountPayload(JobPayload):
    account_id: Annotated[uuid.UUID, Ref(Account)]


class RecordingQueue:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def enqueue_job(
        self,
        function: str,
        *args: Any,
        _job_id: str | None = None,
        _defer_until: datetime | None = None,
    ) -> Any:
        self.calls.append((function, args))
        return object()


@dataclass
class Harness:
    deps: RunnerDeps | None = None
    queue: RecordingQueue = field(default_factory=RecordingQueue)
    seen: dict[str, uuid.UUID | None] = field(default_factory=dict)


H = Harness()
REG = JobRegistry()
ONCE = RetryPolicy(max_attempts=1)


@REG.job("test.p4a_child", payload=AccountPayload, retry=ONCE)
async def child(ctx: JobContext, payload: AccountPayload) -> None:
    H.seen["child"] = ctx.requested_by


@REG.job("test.p4a_parent", payload=AccountPayload, retry=ONCE)
async def parent(ctx: JobContext, payload: AccountPayload) -> None:
    """Chains a follow-up the way a handler should: passing its own requester on."""
    H.seen["parent"] = ctx.requested_by
    assert H.deps is not None
    await enqueue(
        H.deps.factory,
        H.queue,
        child,
        AccountPayload(household_id=ctx.household_id, account_id=payload.account_id),
        household_id=ctx.household_id,
        requested_by=ctx.requested_by,
        idempotency_key=f"child:{ctx.run_id}",
    )


@dataclass
class House:
    hid: uuid.UUID
    alex: uuid.UUID
    account: uuid.UUID  # Alex's private account


def _house(app_engine: Engine, label: str) -> House:
    with app_engine.begin() as c:
        hid = c.execute(
            text("SELECT create_household(:n)"), {"n": f"Example Requester Household {label}"}
        ).scalar_one()
        set_ctx(c, hid, wide=True)
        alex = c.execute(
            text(
                "INSERT INTO person (household_id, display_name, filing_role)"
                " VALUES (:h, :n, 'primary') RETURNING id"
            ),
            {"h": hid, "n": f"Alex {label}"},
        ).scalar_one()
        account = c.execute(
            text(
                "INSERT INTO account (household_id, owner_person_id, visibility, source, name,"
                " type) VALUES (:h, :p, 'private', 'manual', 'Example Checking', 'depository')"
                " RETURNING id"
            ),
            {"h": hid, "p": alex},
        ).scalar_one()
    return House(hid, alex, account)


@pytest.fixture
def house(app_engine: Engine, owner_engine: Engine) -> Iterator[House]:
    h = _house(app_engine, "A")
    yield h
    delete_household(owner_engine, h.hid)


@pytest.fixture
async def deps(app_url: str, migrated: str) -> AsyncIterator[RunnerDeps]:
    engine = create_async_engine(app_url)
    H.deps = RunnerDeps(engine=engine, factory=create_session_factory(engine))
    H.queue = RecordingQueue()
    H.seen = {}
    yield H.deps
    H.deps = None
    await engine.dispose()


async def _requested_by(deps: RunnerDeps, hid: uuid.UUID, run_id: uuid.UUID) -> uuid.UUID | None:
    async with scoped_session(deps.factory, hid, None) as s:
        return (
            await s.execute(select(JobRun.requested_by).where(JobRun.id == run_id))
        ).scalar_one()


async def test_requested_by_round_trips_through_a_chained_job(
    deps: RunnerDeps, house: House
) -> None:
    payload = AccountPayload(household_id=house.hid, account_id=house.account)
    res = await enqueue(
        deps.factory,
        H.queue,
        parent,
        payload,
        household_id=house.hid,
        requested_by=house.alex,
        idempotency_key="parent:1",
    )
    assert await _requested_by(deps, house.hid, res.run_id) == house.alex

    (_, parent_args) = H.queue.calls.pop()
    assert await execute(deps, parent, parent_args, job_try=1) == "succeeded"
    assert H.seen["parent"] == house.alex

    # The child was enqueued with Alex's rights (his private account passed the check) and
    # records him as its requester; running it hands him to the handler again.
    (_, child_args) = H.queue.calls.pop()
    child_run = uuid.UUID(child_args[1])
    assert await _requested_by(deps, house.hid, child_run) == house.alex
    assert await execute(deps, child, child_args, job_try=1) == "succeeded"
    assert H.seen["child"] == house.alex


async def test_system_and_scheduler_runs_have_no_requester(deps: RunnerDeps, house: House) -> None:
    res = await enqueue_household_wide(
        deps.factory,
        H.queue,
        child,
        AccountPayload(household_id=house.hid, account_id=house.account),
        household_id=house.hid,
        idempotency_key="sched:1",
    )
    assert await _requested_by(deps, house.hid, res.run_id) is None
    (_, args) = H.queue.calls.pop()
    assert await execute(deps, child, args, job_try=1) == "succeeded"
    assert H.seen["child"] is None


async def test_a_key_reused_by_another_requester_conflicts(
    deps: RunnerDeps, house: House, app_engine: Engine
) -> None:
    with app_engine.begin() as c:
        set_ctx(c, house.hid, wide=True)
        joint = c.execute(
            text(
                "INSERT INTO account (household_id, source, name, type)"
                " VALUES (:h, 'manual', 'Example Joint', 'depository') RETURNING id"
            ),
            {"h": house.hid},
        ).scalar_one()
    payload = AccountPayload(household_id=house.hid, account_id=joint)
    who = {"household_id": house.hid, "idempotency_key": "dup:1"}
    first = await enqueue(deps.factory, H.queue, child, payload, requested_by=house.alex, **who)
    again = await enqueue(deps.factory, H.queue, child, payload, requested_by=house.alex, **who)
    assert first.run_id == again.run_id
    with pytest.raises(IdempotencyConflict, match="different requester"):
        await enqueue(deps.factory, H.queue, child, payload, requested_by=None, **who)


async def test_requester_is_a_person_of_the_runs_household(
    deps: RunnerDeps, house: House, app_engine: Engine, owner_engine: Engine
) -> None:
    other = _house(app_engine, "B")
    try:
        with app_engine.begin() as c:
            set_ctx(c, house.hid, wide=True)
            joint = c.execute(
                text(
                    "INSERT INTO account (household_id, source, name, type)"
                    " VALUES (:h, 'manual', 'Example Joint', 'depository') RETURNING id"
                ),
                {"h": house.hid},
            ).scalar_one()
        with pytest.raises(IntegrityError, match="fk_job_run_requested_by_person"):
            await enqueue(
                deps.factory,
                H.queue,
                child,
                AccountPayload(household_id=house.hid, account_id=joint),
                household_id=house.hid,
                requested_by=other.alex,
                idempotency_key="cross:1",
            )
    finally:
        delete_household(owner_engine, other.hid)


async def test_deleting_the_requester_keeps_the_run(
    deps: RunnerDeps, house: House, app_engine: Engine
) -> None:
    with app_engine.begin() as c:
        set_ctx(c, house.hid, wide=True)
        sam = c.execute(
            text(
                "INSERT INTO person (household_id, display_name, filing_role)"
                " VALUES (:h, 'Sam Example', 'spouse') RETURNING id"
            ),
            {"h": house.hid},
        ).scalar_one()
        joint = c.execute(
            text(
                "INSERT INTO account (household_id, source, name, type)"
                " VALUES (:h, 'manual', 'Example Joint', 'depository') RETURNING id"
            ),
            {"h": house.hid},
        ).scalar_one()
    res = await enqueue(
        deps.factory,
        H.queue,
        child,
        AccountPayload(household_id=house.hid, account_id=joint),
        household_id=house.hid,
        requested_by=sam,
        idempotency_key="gone:1",
    )
    with app_engine.begin() as c:
        set_ctx(c, house.hid, wide=True)
        c.execute(text("DELETE FROM person WHERE id = :p"), {"p": sam})
    assert await _requested_by(deps, house.hid, res.run_id) is None
