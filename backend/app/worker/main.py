"""arq worker entry point: ``arq app.worker.main.WorkerSettings``.

Registered jobs come from ``app/jobs/handlers/`` (ADR 0016). Each is wrapped by
``app.jobs.runner.arq_function``: payload validation, the per-household lock, ``job_run`` state
and retries all happen there, never in arq's own retry logic. ``sweep_stalled_runs`` runs every
minute and re-pushes runs arq has lost.
"""

from __future__ import annotations

from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings

from app import __version__
from app.config import get_settings
from app.db import create_engine, create_session_factory
from app.jobs import queue
from app.jobs.registry import discover_jobs
from app.jobs.runner import CTX_KEY, RunnerDeps, arq_function, sweep_stalled_runs
from app.logging import configure_logging, get_logger
from app.security import crypto

log = get_logger(__name__)

# Each running job holds three pooled connections at most: the household lock, the handler's
# session and one side session from JobContext.factory (handlers open one at a time). 5 jobs x 3
# = 15 = SQLAlchemy's default pool (5 + 10 overflow), so a full worker never waits on the pool.
MAX_JOBS = 5
CONNECTIONS_PER_JOB = 3


async def heartbeat(ctx: dict[str, Any]) -> None:
    """No-op cron job proving the scheduler runs. Real jobs land from Phase 2."""
    log.info("worker.heartbeat", job_try=ctx.get("job_try"))


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging()
    if (key := get_settings().fernet_key) is not None:
        crypto.load_ring(key)  # malformed or duplicate keys fail startup
    engine = create_engine(get_settings())
    ctx[CTX_KEY] = RunnerDeps(engine=engine, factory=create_session_factory(engine))
    log.info("worker.startup", version=__version__, jobs=len(discover_jobs()))


async def shutdown(ctx: dict[str, Any]) -> None:
    deps: RunnerDeps | None = ctx.get(CTX_KEY)
    if deps is not None:
        await deps.engine.dispose()
    log.info("worker.shutdown")


class WorkerSettings:
    functions: ClassVar[list[Any]] = [heartbeat, *map(arq_function, discover_jobs())]
    cron_jobs: ClassVar[list[Any]] = [
        cron(heartbeat, minute=None, second=0, run_at_startup=True),
        cron(sweep_stalled_runs, minute=None, second=30, run_at_startup=True),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url.get_secret_value())
    # Plain functions, not staticmethod(): arq builds the Worker from this class's __dict__, so a
    # wrapper would reach it as-is. tests/test_worker.py pins what arq actually receives.
    job_serializer = queue.dumps
    job_deserializer = queue.loads
    max_jobs = MAX_JOBS
    health_check_interval = 60
