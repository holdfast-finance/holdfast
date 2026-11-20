"""arq worker settings import cleanly and schedule the heartbeat."""

from __future__ import annotations

import pickle
from typing import Any

import pytest
from arq.worker import get_kwargs

from app.config import get_settings
from app.db import create_engine
from app.jobs import queue
from app.worker.main import CONNECTIONS_PER_JOB, MAX_JOBS, WorkerSettings, heartbeat


def test_worker_settings_register_heartbeat_cron() -> None:
    assert heartbeat in WorkerSettings.functions
    assert [job.coroutine for job in WorkerSettings.cron_jobs] == [heartbeat]


async def test_heartbeat_is_a_noop() -> None:
    await heartbeat({"job_try": 1})  # must not raise


def test_worker_deserializes_json_never_pickle() -> None:
    """Pin what arq actually builds the Worker from, not just the class attributes."""
    kwargs = get_kwargs(WorkerSettings)
    assert kwargs["job_serializer"] is queue.dumps
    assert kwargs["job_deserializer"] is queue.loads
    job: dict[str, Any] = {"t": 1, "f": "test.item", "a": ["h", "r", "{}"], "k": {}, "et": 1}
    with pytest.raises(ValueError):
        kwargs["job_deserializer"](pickle.dumps(job))


def test_a_full_worker_fits_the_connection_pool() -> None:
    pool: Any = create_engine(get_settings()).pool
    assert pool.size() + pool._max_overflow >= MAX_JOBS * CONNECTIONS_PER_JOB
    assert WorkerSettings.max_jobs == MAX_JOBS
