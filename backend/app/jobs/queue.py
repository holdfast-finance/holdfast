"""arq queue plumbing shared by enqueuers (api, services) and the worker.

arq serializes jobs with pickle by default, so anyone who can write to Redis could run code in
the worker. Holdfast jobs carry three strings (household id, run id, IDs-only payload JSON), so
both sides use JSON instead: a tampered job can at worst fail validation.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Protocol

from arq.connections import ArqRedis
from redis.asyncio import Redis


def dumps(obj: Any) -> bytes:
    # arq stores failure results that may hold exceptions; ``default=str`` keeps those
    # serializable (as a class name + message arq itself produced, never a handler's message:
    # the runner catches handler exceptions before arq sees them).
    return json.dumps(obj, separators=(",", ":"), default=str).encode()


def loads(data: bytes) -> Any:
    return json.loads(data)


class JobQueue(Protocol):
    """The slice of :class:`arq.connections.ArqRedis` that enqueue uses."""

    async def enqueue_job(
        self,
        function: str,
        *args: Any,
        _job_id: str | None = None,
        _defer_until: datetime | None = None,
    ) -> Any: ...


def arq_pool(redis: Redis, *, queue_name: str | None = None) -> ArqRedis:
    """An ArqRedis sharing ``redis``'s connection pool, with the JSON serializer.

    The client must not use ``decode_responses`` (arq reads bytes); ``app.main.create_redis``
    doesn't.
    """
    kwargs: dict[str, Any] = {"job_serializer": dumps, "job_deserializer": loads}
    if queue_name is not None:
        kwargs["default_queue_name"] = queue_name
    return ArqRedis(pool_or_conn=redis.connection_pool, **kwargs)
