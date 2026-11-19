"""arq worker entry point: ``arq app.worker.main.WorkerSettings``."""

from __future__ import annotations

from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings

from app import __version__
from app.config import get_settings
from app.logging import configure_logging, get_logger

log = get_logger(__name__)


async def heartbeat(ctx: dict[str, Any]) -> None:
    """No-op cron job proving the scheduler runs. Real jobs land from Phase 2."""
    log.info("worker.heartbeat", job_try=ctx.get("job_try"))


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging()
    log.info("worker.startup", version=__version__)


async def shutdown(ctx: dict[str, Any]) -> None:
    log.info("worker.shutdown")


class WorkerSettings:
    functions: ClassVar[list[Any]] = [heartbeat]
    cron_jobs: ClassVar[list[Any]] = [cron(heartbeat, minute=None, second=0, run_at_startup=True)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url.get_secret_value())
    health_check_interval = 60
