"""Typed job registry: name -> handler + IDs-only payload schema + retry policy (ADR 0016).

Declare a job next to the code it drives, in a module under ``app/jobs/handlers/`` (discovered
automatically, like routers; no hot file to edit)::

    class SyncItem(JobPayload):
        bank_connection_id: uuid.UUID

    @job("plaid.sync_item", payload=SyncItem, retry=RetryPolicy(max_attempts=5))
    async def sync_item(ctx: JobContext, payload: SyncItem) -> None:
        ...  # ctx.session is household-wide and commits together with the success mark

    # elsewhere (a service):
    await jobs.enqueue(factory, pool, sync_item, SyncItem(household_id=h, bank_connection_id=c),
                       idempotency_key=f"sync:{c}:{hour}")

Payloads are **IDs only**: every field is a ``uuid.UUID`` and ``household_id`` is always present.
A payload class with any other field type fails at import. Data the job needs (tokens, amounts,
dates) is loaded from the database by ID inside the job, so nothing sensitive sits in Redis, in
arq's logs or in ``job_run``. Validation is strict and runs twice: before enqueue and before run.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import pkgutil
import re
import uuid
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, ClassVar

import pydantic_core
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models._common import (
    IDEMPOTENCY_KEY_MAX,
    IDEMPOTENCY_KEY_PATTERN,
    JOB_NAME_MAX,
    JOB_NAME_PATTERN,
)

HANDLERS_PACKAGE = "app.jobs.handlers"


class InvalidJobPayload(ValueError):
    """The payload doesn't match the job's schema. Raised before enqueue and before run.

    The message names the job and the offending field locations only, never the values.
    """


class InvalidIdempotencyKey(ValueError):
    pass


class JobPayload(BaseModel):
    """Base for job payloads: frozen, strict, no extra keys, UUID fields only."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    household_id: uuid.UUID

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        bad = [
            name for name, field in cls.model_fields.items() if field.annotation is not uuid.UUID
        ]
        if bad:
            raise TypeError(
                f"{cls.__name__}: job payloads are IDs only (uuid.UUID fields); "
                f"not allowed: {', '.join(sorted(bad))}"
            )


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retries with capped exponential backoff.

    ``max_attempts`` counts handler runs (a worker crash mid-run counts too). Waiting for the
    household lock does not use an attempt: it defers by ``lock_retry_delay`` up to
    ``max_lock_deferrals`` times, then the run fails as ``HouseholdBusy``.
    """

    max_attempts: int = 5
    base_delay: timedelta = timedelta(seconds=30)
    factor: int = 2
    max_delay: timedelta = timedelta(minutes=30)
    lock_retry_delay: timedelta = timedelta(seconds=5)
    max_lock_deferrals: int = 120

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 100:
            raise ValueError("max_attempts must be between 1 and 100 (job_run CHECK)")
        if self.factor < 1 or self.max_lock_deferrals < 0:
            raise ValueError("factor must be >= 1 and max_lock_deferrals >= 0")
        zero = timedelta(0)
        if self.base_delay < zero or self.max_delay < self.base_delay:
            raise ValueError("need 0 <= base_delay <= max_delay")
        if self.lock_retry_delay <= zero:
            raise ValueError("lock_retry_delay must be positive")

    def delay(self, attempt: int) -> timedelta:
        """Backoff before the run after ``attempt`` (1-based) failed."""
        if attempt < 1:
            raise ValueError("attempt is 1-based")
        # Cap the exponent before multiplying: factor ** attempt can't overflow timedelta.
        exponent = min(attempt - 1, 32)
        scaled: timedelta = self.base_delay * (self.factor**exponent)
        return min(self.max_delay, scaled)

    @property
    def queue_max_tries(self) -> int:
        """arq's own cap: every handler attempt, every lock deferral, plus one final delivery
        that records the outcome. The runner never asks arq for a retry on this last try."""
        return self.max_attempts + self.max_lock_deferrals + 1


DEFAULT_RETRY = RetryPolicy()
DEFAULT_TIMEOUT = timedelta(minutes=5)


@dataclass(frozen=True)
class JobContext:
    """What a handler gets besides its payload.

    ``session`` is scoped to the household with ``household_wide=True`` (jobs write every
    account, ADR 0008). The runner marks the run succeeded in this session's last transaction,
    so writes the handler leaves uncommitted land atomically with the success mark: a crash
    before that commit rolls both back and the retry starts clean. Commit earlier (the context
    survives commits) only for progress that must persist across retries, and keep such steps
    idempotent. ``factory`` opens independent sessions (for example an audit row that must
    survive a rollback).
    """

    run_id: uuid.UUID
    household_id: uuid.UUID
    attempt: int
    session: AsyncSession
    factory: async_sessionmaker[AsyncSession]


type Handler[P: JobPayload] = Callable[[JobContext, P], Awaitable[None]]


@dataclass(frozen=True)
class JobSpec[P: JobPayload]:
    name: str
    handler: Handler[P]
    payload: type[P]
    retry: RetryPolicy
    timeout: timedelta

    def validate(self, payload: P | Mapping[str, Any]) -> P:
        """Strictly re-validate ``payload`` (a model or a plain mapping) as this job's schema.

        Model instances go through JSON too, so ``model_construct`` can't smuggle in bad values
        and a payload built for another job can't pass by sharing field names.
        """
        try:
            if isinstance(payload, BaseModel):
                raw = payload.model_dump(mode="python", warnings=False)
            else:
                raw = dict(payload)
            data = pydantic_core.to_json(raw)
        except (TypeError, ValueError, pydantic_core.PydanticSerializationError) as exc:
            raise InvalidJobPayload(f"{self.name}: payload is not JSON-serializable") from exc
        return self.parse_json(data)

    def parse_json(self, data: str | bytes) -> P:
        """Parse the queued JSON form strictly. The worker calls this before running."""
        try:
            return self.payload.model_validate_json(data, strict=True)
        except ValidationError as exc:
            locs = sorted(
                {".".join(str(p) for p in err["loc"]) or "<root>" for err in exc.errors()}
            )
            raise InvalidJobPayload(f"{self.name}: invalid payload at {', '.join(locs)}") from None


def check_job_name(name: str) -> str:
    if len(name) > JOB_NAME_MAX or not re.fullmatch(JOB_NAME_PATTERN, name):
        raise ValueError(f"job name must look like 'area.action' (lowercase): {name!r}")
    return name


def check_idempotency_key(key: str) -> str:
    if len(key) > IDEMPOTENCY_KEY_MAX or not re.fullmatch(IDEMPOTENCY_KEY_PATTERN, key):
        raise InvalidIdempotencyKey(
            f"idempotency keys are 1-{IDEMPOTENCY_KEY_MAX} chars of [A-Za-z0-9:._-]"
        )
    return key


class JobRegistry:
    """Job name -> :class:`JobSpec`. The worker exposes every registered job to arq."""

    def __init__(self) -> None:
        self._specs: dict[str, JobSpec[Any]] = {}

    def job[P: JobPayload](
        self,
        name: str,
        *,
        payload: type[P],
        retry: RetryPolicy = DEFAULT_RETRY,
        timeout: timedelta = DEFAULT_TIMEOUT,
    ) -> Callable[[Handler[P]], JobSpec[P]]:
        """Decorator: register ``handler`` under ``name``. Returns the :class:`JobSpec`."""
        check_job_name(name)
        if not (isinstance(payload, type) and issubclass(payload, JobPayload)):
            raise TypeError(f"{name}: payload must subclass JobPayload")
        if name in self._specs:
            raise ValueError(f"job {name!r} is already registered")

        def register(handler: Handler[P]) -> JobSpec[P]:
            if name in self._specs:
                raise ValueError(f"job {name!r} is already registered")
            spec = JobSpec(
                name=name, handler=handler, payload=payload, retry=retry, timeout=timeout
            )
            self._specs[name] = spec
            return spec

        return register

    def get(self, name: str) -> JobSpec[Any]:
        return self._specs[name]

    def __contains__(self, name: object) -> bool:
        return name in self._specs

    def __iter__(self) -> Iterator[JobSpec[Any]]:
        return iter(sorted(self._specs.values(), key=lambda s: s.name))

    def __len__(self) -> int:
        return len(self._specs)


registry = JobRegistry()
job = registry.job


class _Discovery:
    done: ClassVar[bool] = False


def discover_jobs() -> JobRegistry:
    """Import every ``app/jobs/handlers/*.py`` module (sorted) so its ``@job``s register.

    Only modules backed by a real ``.py`` file load, as with route discovery (ADR 0011).
    Idempotent.
    """
    if not _Discovery.done:
        package = importlib.import_module(HANDLERS_PACKAGE)
        for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda i: i.name):
            if info.ispkg or info.name.startswith("_"):
                continue
            spec = getattr(info.module_finder, "find_spec", lambda _n: None)(info.name)
            loader = getattr(spec, "loader", None)
            if isinstance(loader, importlib.machinery.SourceFileLoader):
                importlib.import_module(f"{HANDLERS_PACKAGE}.{info.name}")
        _Discovery.done = True
    return registry
