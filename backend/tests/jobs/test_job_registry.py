"""Job registry, payload schemas and retry policy: no services needed."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Annotated, Any

import pytest

from app.jobs import queue
from app.jobs.lock import lock_key
from app.jobs.registry import (
    InvalidIdempotencyKey,
    InvalidJobPayload,
    JobContext,
    JobPayload,
    JobRegistry,
    Ref,
    RetryPolicy,
    check_idempotency_key,
    discover_jobs,
)
from app.jobs.runner import ARQ_TIMEOUT_MARGIN, arq_function
from app.models import Account, BankConnection, Institution
from app.worker.main import WorkerSettings, heartbeat


class ItemPayload(JobPayload):
    item_id: Annotated[uuid.UUID, Ref(Account)]


async def _noop(ctx: JobContext, payload: ItemPayload) -> None:
    return None


def _spec(name: str = "test.item") -> Any:
    reg = JobRegistry()
    return reg.job(name, payload=ItemPayload)(_noop)


HID = uuid.UUID("00000000-0000-4000-8000-000000000001")
IID = uuid.UUID("00000000-0000-4000-8000-000000000002")


# --------------------------------------------------------------------------- IDs-only payloads


@pytest.mark.parametrize("annotation", [str, int, "uuid.UUID | None", "list[uuid.UUID]", dict])
def test_payload_fields_must_be_uuids(annotation: Any) -> None:
    with pytest.raises(TypeError, match="IDs only"):
        type(
            "Bad",
            (JobPayload,),
            {"__annotations__": {"extra": annotation}, "__module__": __name__},
        )


def test_id_fields_must_name_their_table() -> None:
    with pytest.raises(TypeError, match="exactly one Ref"):

        class Bare(JobPayload):
            item_id: uuid.UUID

    with pytest.raises(TypeError, match="exactly one Ref"):

        class Twice(JobPayload):
            item_id: Annotated[uuid.UUID, Ref(Account), Ref(BankConnection)]

    with pytest.raises(TypeError, match="takes no Ref"):

        class Household(JobPayload):
            household_id: Annotated[uuid.UUID, Ref(Account)]


@pytest.mark.parametrize("target", [Institution, dict, "account"])
def test_refs_point_at_household_scoped_tables_only(target: Any) -> None:
    with pytest.raises(TypeError, match="household-scoped"):
        Ref(target)


def test_refs_are_collected_per_payload() -> None:
    assert dict(ItemPayload.refs) == {"item_id": Ref(Account)}
    assert dict(JobPayload.refs) == {}


def test_payload_always_carries_household_id() -> None:
    assert ItemPayload.model_fields["household_id"].annotation is uuid.UUID
    assert ItemPayload.model_fields["household_id"].is_required()


def test_registration_requires_a_job_payload() -> None:
    reg = JobRegistry()
    with pytest.raises(TypeError, match="JobPayload"):
        reg.job("test.bad", payload=dict)  # type: ignore[type-var]


# --------------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "payload",
    [
        {"household_id": HID, "item_id": IID},
        {"household_id": str(HID), "item_id": str(IID)},
        {"household_id": str(HID).upper(), "item_id": IID.hex},
        ItemPayload(household_id=HID, item_id=IID),
    ],
)
def test_valid_payloads_normalize(payload: Any) -> None:
    out = _spec().validate(payload)
    assert out == ItemPayload(household_id=HID, item_id=IID)
    assert json.loads(out.model_dump_json()) == {"household_id": str(HID), "item_id": str(IID)}


@pytest.mark.parametrize(
    "payload",
    [
        {"household_id": "not-a-uuid", "item_id": str(IID)},
        {"household_id": str(HID), "item_id": 42},
        {"household_id": str(HID), "item_id": None},
        {"household_id": str(HID)},
        {"item_id": str(IID)},
        {"household_id": str(HID), "item_id": str(IID), "token": "access-sandbox-xyz"},
        {"household_id": str(HID), "item_id": ["x"]},
        {"household_id": str(HID), "item_id": object()},
        {},
    ],
)
def test_malformed_payloads_are_rejected(payload: Any) -> None:
    with pytest.raises(InvalidJobPayload):
        _spec().validate(payload)


def test_model_construct_cannot_bypass_validation() -> None:
    smuggled = ItemPayload.model_construct(household_id="nope", item_id=IID)  # type: ignore[arg-type]
    with pytest.raises(InvalidJobPayload):
        _spec().validate(smuggled)


def test_a_payload_for_another_job_is_rejected() -> None:
    class Other(JobPayload):
        account_id: Annotated[uuid.UUID, Ref(Account)]

    with pytest.raises(InvalidJobPayload):
        _spec().validate(Other(household_id=HID, account_id=IID))


def test_rejection_message_names_fields_not_values() -> None:
    secret = "access-sandbox-canary-1234"
    with pytest.raises(InvalidJobPayload) as info:
        _spec().validate({"household_id": secret, "item_id": str(IID), "token": secret})
    assert secret not in str(info.value)
    assert "household_id" in str(info.value)


@pytest.mark.parametrize(
    "raw",
    ['{"household_id": 1}', "[]", "not json", f'{{"household_id": "{HID}"}}'],
)
def test_parse_json_is_strict(raw: str) -> None:
    with pytest.raises(InvalidJobPayload):
        _spec().parse_json(raw)


# --------------------------------------------------------------------------- names and keys


@pytest.mark.parametrize("name", ["heartbeat", "Plaid.sync", "plaid.", ".sync", "plaid sync"])
def test_job_names_need_area_dot_action(name: str) -> None:
    with pytest.raises(ValueError, match=r"area\.action"):
        JobRegistry().job(name, payload=ItemPayload)


def test_duplicate_job_names_are_refused() -> None:
    reg = JobRegistry()
    reg.job("test.item", payload=ItemPayload)(_noop)
    with pytest.raises(ValueError, match="already registered"):
        reg.job("test.item", payload=ItemPayload)


@pytest.mark.parametrize("key", ["sync:abc:2026-11-20T06", "a", "x" * 200, "A.b_c-d"])
def test_idempotency_key_accepts(key: str) -> None:
    assert check_idempotency_key(key) == key


@pytest.mark.parametrize(
    "key", ["", " lead", "has space", "a/b", "https://x", "k\n", "x" * 201, "-dash", "é"]
)
def test_idempotency_key_rejects(key: str) -> None:
    with pytest.raises(InvalidIdempotencyKey):
        check_idempotency_key(key)


# --------------------------------------------------------------------------- retry policy


def test_backoff_is_exponential_and_capped() -> None:
    policy = RetryPolicy(
        max_attempts=10, base_delay=timedelta(seconds=30), max_delay=timedelta(minutes=5)
    )
    delays = [policy.delay(n).total_seconds() for n in range(1, 8)]
    assert delays == [30, 60, 120, 240, 300, 300, 300]
    assert policy.delay(10_000) == timedelta(minutes=5)


def test_queue_max_tries_covers_attempts_and_lock_waits() -> None:
    policy = RetryPolicy(max_attempts=3, max_lock_deferrals=7)
    assert policy.queue_max_tries == 11


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 0},
        {"max_attempts": 101},
        {"factor": 0},
        {"max_lock_deferrals": -1},
        {"base_delay": timedelta(minutes=10), "max_delay": timedelta(minutes=1)},
        {"lock_retry_delay": timedelta(0)},
    ],
)
def test_retry_policy_rejects_nonsense(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)


def test_delay_is_one_based() -> None:
    with pytest.raises(ValueError):
        RetryPolicy().delay(0)


# --------------------------------------------------------------------------- worker wiring


def test_arq_function_uses_the_job_name_and_bounded_tries() -> None:
    spec = _spec("test.wired")
    fn = arq_function(spec)
    assert fn.name == "test.wired"
    assert fn.max_tries == spec.retry.queue_max_tries
    # arq's timeout is a backstop above the runner's own asyncio.timeout(spec.timeout).
    assert fn.timeout_s == (spec.timeout + ARQ_TIMEOUT_MARGIN).total_seconds()
    assert timedelta(0) < ARQ_TIMEOUT_MARGIN
    assert fn.keep_result_s == 0  # job_run is the record; arq stores nothing


# --------------------------------------------------------------------------- household lock key


def test_lock_key_hashes_the_whole_household_uuid() -> None:
    """UUIDv7 ids created in the same millisecond share their first bytes; keys must not."""
    prefix = bytes.fromhex("019a2b3c4d5e7000")
    a = uuid.UUID(bytes=prefix + bytes.fromhex("8000000000000001"))
    b = uuid.UUID(bytes=prefix + bytes.fromhex("8000000000000002"))
    assert a.bytes[:4] == b.bytes[:4]
    assert lock_key(a) != lock_key(b)
    assert lock_key(a) == lock_key(uuid.UUID(str(a)))  # stable across processes
    keys = [lock_key(uuid.uuid4()) for _ in range(200)]
    assert all(-(2**31) <= k < 2**31 for k in keys)  # fits pg_try_advisory_lock(int, int)


def test_worker_registers_discovered_jobs_and_json_serializer() -> None:
    names = {getattr(f, "name", getattr(f, "__name__", None)) for f in WorkerSettings.functions}
    assert {spec.name for spec in discover_jobs()} <= names
    assert heartbeat in WorkerSettings.functions
    assert WorkerSettings.job_serializer is queue.dumps
    assert WorkerSettings.job_deserializer is queue.loads


def test_queue_serializer_is_json_not_pickle() -> None:
    job = {"t": 1, "f": "test.item", "a": [str(HID), str(IID), "{}"], "k": {}, "et": 1}
    data = queue.dumps(job)
    assert json.loads(data) == job
    assert queue.loads(data) == job
    with pytest.raises(ValueError):
        queue.loads(b"\x80\x04K\x01.")  # a pickle stream is not accepted
