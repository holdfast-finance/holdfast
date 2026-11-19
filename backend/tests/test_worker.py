"""arq worker settings import cleanly and schedule the heartbeat."""

from __future__ import annotations

from app.worker.main import WorkerSettings, heartbeat


def test_worker_settings_register_heartbeat_cron() -> None:
    assert heartbeat in WorkerSettings.functions
    assert [job.coroutine for job in WorkerSettings.cron_jobs] == [heartbeat]


async def test_heartbeat_is_a_noop() -> None:
    await heartbeat({"job_try": 1})  # must not raise
