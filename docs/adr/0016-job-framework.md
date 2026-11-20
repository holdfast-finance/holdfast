# 0016. Job framework: arq for delivery, `job_run` for truth, Postgres advisory lock per household

- Status: Accepted (amended by F2b, 2026-11-20: enqueue authority, timeouts, stale attempts)
- Date: 2026-11-20
- Deciders: Lattice (architect); Keel; Bulwark (security review of #24)

## Context
Phase 2 brings scheduled Plaid polling (poll-first, no webhooks), and later phases add imports,
matview refreshes and agent reviews. All of them are background work against one household's data.
arq and Redis were already in the stack (ADR 0004) with a stub worker. Roadmap F2 asks for a typed
registry, idempotency keys, a per-household lock, bounded retries and a `job_run` table under RLS,
with two acceptance checks: a job enqueued twice gives one side effect, and a non-UUID payload is
rejected. Bulwark's roundtable item E1.3 asked for IDs-only arq payloads, enforced by a test.

arq gives at-least-once delivery. Its own dedupe (`_job_id`) only lasts while the job or its
result sits in Redis, and arq retries only on `Retry` or cancellation. On its own, arq can't
guarantee one side effect.

## Decision
**`job_run` is the source of truth; arq only delivers.** One row per logical job, with
`UNIQUE (household_id, job_name, idempotency_key)`, `household_id`, ENABLE + FORCE RLS and the
standard `household_isolation` policy. The worker writes it as `holdfast_app` inside the job's
household context.

- **Registry** (`app/jobs/registry.py`): `@job("area.action", payload=Model, retry=RetryPolicy(...))`
  returns a typed `JobSpec[P]`. Handler modules live in `app/jobs/handlers/` and are discovered
  like routers (ADR 0011), so adding a job never edits a shared file.
- **IDs-only payloads:** `JobPayload` subclasses may declare only `uuid.UUID` fields, and
  `household_id` is always present. A class that breaks this fails at import. Validation is
  strict (extra keys forbidden). It runs before enqueue, where model instances also go through
  JSON so `model_construct` can't bypass it, and again before the run. A `CHECK` on
  `job_run.payload` enforces the same shape in the database. Error messages name fields, never values.
  Every id field other than `household_id` names its table: `Annotated[uuid.UUID, Ref(Model)]`,
  where `Model` is a household-scoped table (has `id` and `household_id`). A field with no `Ref`,
  or two, fails at import, so a new job can't skip the enqueue check below.
- **Enqueue authority (confused deputy, F2b).** Handlers run household-wide, so enqueue is the one
  place the requester's own visibility applies. `enqueue(..., household_id=, requested_by=)` takes
  the *authenticated* household and person as separate arguments, never the payload's word for
  them. It requires `payload.household_id == household_id`, then looks every `Ref` id up in the
  requester's person-scoped RLS session (`scoped_session(factory, household_id, requested_by)`).
  Any miss raises `JobReferenceNotVisible` (render as not found) before a row is written or a job
  is pushed. The check also runs for a duplicate key, so a key can't probe another person's run.
  `requested_by=None` is for system callers (schedulers) and gets RLS's person-less view (shared
  and joint rows), never a household-wide one. **Every API route that enqueues must pass the
  session's household and person.** Visibility is the bar, not write permission: a job that
  writes on a person's behalf to rows they can only read must check that itself.
- **Idempotency:** the caller passes an explicit key (`[A-Za-z0-9:._-]`, at most 200 characters,
  for example `sync:<item>:<hour>`). A duplicate returns the existing run and creates no new row.
  If that run is still `queued`, enqueue pushes it to arq again under the same `_job_id`, which
  arq dedupes. This heals a crash between the commit and the Redis push. Reusing a key with a
  different payload raises `IdempotencyConflict`.
- **Per-household lock: a Postgres session-level advisory lock**, `pg_try_advisory_lock(ns, key)`
  on a dedicated pooled connection held for the run. `key` is the first 4 bytes of
  `blake2b(household_id.bytes)`, not the UUID's leading bytes (a UUIDv7 leads with its timestamp). If the lock is busy, the run defers with
  `Retry(lock_retry_delay)` and no attempt is used. After `max_lock_deferrals` the run fails as
  `HouseholdBusy`.
- **Exactly one success:** a conditional `UPDATE` claims the run under the lock
  (`queued|retrying|running → running`, `attempts + 1`, and `job_name` must match the delivering
  function). The claim returns `job_run.payload`, and **that** is what the handler runs on: it was
  validated and authorized at enqueue, while the Redis copy is unauthenticated. A Redis copy that
  differs is logged (`job.delivery_mismatch`) and ignored. Every later mark (`succeeded`,
  `retrying`, `failed`) matches `status = 'running' AND attempts = <its attempt>`, so a stale
  execution can't overwrite the outcome of a newer re-claim. A run already in `succeeded` or `failed`
  is skipped. A `running` row found while holding the lock is an orphan of a dead worker and is
  re-claimed. The handler gets a household-wide session, and the runner marks success in that same
  session's final transaction, so uncommitted handler writes commit atomically with the mark.
- **Bounded retries:** capped exponential backoff (`base_delay * factor^(n-1)`, up to `max_delay`)
  until `max_attempts`. arq's own `max_tries` is set to attempts + lock deferrals + 1. On that last
  try the runner always records `failed` instead of asking for another retry, so arq can never drop
  a run that `job_run` still shows as `retrying`.
- **Timeouts and cancellation never leave a row `running`.** arq enforces its timeout by
  cancelling the task, which `except Exception` never sees; the row stayed `running` and enqueue
  with the same key returned it without re-pushing. Now the runner wraps the handler and its
  success commit in `asyncio.timeout(spec.timeout)`: expiry rolls the handler back and is recorded
  as `TimeoutError` (retried or failed like any error). arq's function timeout is
  `spec.timeout + 60 s` (`ARQ_TIMEOUT_MARGIN`), a backstop for the bookkeeping only. Any
  `CancelledError` after the claim (worker shutdown, the backstop) is recorded, shielded, as
  `retrying` (`CancelledError`, due now) or `failed` on the last attempt, then re-raised.
  `keep_result=0`: arq stores no results, so none holds arq's error text and none blocks a later
  re-push under the same `_job_id`.
- **No secrets in errors:** `last_error` stores the exception class name only. Handler exceptions
  never reach arq, so arq doesn't log or store their messages either.
- **JSON, not pickle, on the queue:** both enqueuers (`app.jobs.queue.arq_pool`) and the worker
  use a JSON job serializer. A job carries three strings (household id, run id, payload JSON), so a
  forged Redis entry can at worst fail validation. It can't execute code. arq builds its Worker
  from `WorkerSettings.__dict__`, so the settings hold the plain functions (a `staticmethod`
  wrapper would reach arq as-is), and a test pins `get_kwargs(WorkerSettings)` to the JSON loader.

## Alternatives considered
- Redis lock (`SET NX PX` + renewal): needs a TTL and heartbeat. If the TTL expires mid-run, two
  runs overlap, and a crash leaves a stale lock until expiry. Postgres already holds the state we
  must keep consistent, and it releases the lock when a connection dies.
- `pg_advisory_xact_lock` inside the handler's transaction: ties the lock to one transaction, so
  handlers that commit progress (sync cursors) would drop it mid-run.
- Blocking lock waits: each waiter ties up an arq slot and a DB connection. Deferring is cheaper
  and bounded.
- Deriving the idempotency key from the payload hash: "sync item X" could then run only once ever.
  An explicit key states the dedupe window (per hour, per statement and so on).
- arq's `_job_id` alone: its dedupe ends when the result expires, and nothing durable records
  attempts or failures.
- Celery, Dramatiq or a Postgres-only queue (procrastinate): new dependencies, and arq is already
  deployed. No new service is added (hard rule 9).

## Consequences
- Each running job holds up to three pooled connections: the lock, the handler's session and one
  side session from `JobContext.factory` (handlers open those one at a time). The worker keeps
  `max_jobs` at 5: 5 × 3 = 15, exactly SQLAlchemy's default pool (5 + 10 overflow). A test fails
  if `MAX_JOBS × CONNECTIONS_PER_JOB` outgrows the pool.
- Handlers must still be idempotent for any progress they commit early or any external call they
  make (Plaid), because a retry re-runs them. Writes left to the final commit are exactly-once.
- Jobs serialize per household across all job types. A sync that runs longer than
  `lock_retry_delay × max_lock_deferrals` (10 minutes by default) makes queued work fail as
  `HouseholdBusy`. Tune this per job if it happens.
- Handlers run household-wide (`household_wide=True`, ADR 0008). Person-scoped jobs (agent runs
  for one person) will need a `person_id` field in the payload and a context option. Not built yet.
- If arq's backstop timeout ever fires, arq drops the job, and the row sits `retrying` with a
  past `next_attempt_at`. Unlike `running`, that state is never live work, so a sweeper can safely
  re-push it.
- Follow-ups: a sweeper that re-pushes `queued` rows older than N minutes (crash between commit
  and push when nobody retries the enqueue) and `retrying` rows whose `next_attempt_at` is well
  past, retention pruning of old `job_run` rows, wiring
  `arq_pool(app.state.redis)` into the API once the first API-triggered job lands (P7), and
  Bulwark's Redis-auth item. The import-linter domain contract now also forbids `app.jobs`.
