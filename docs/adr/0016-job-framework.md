# 0016. Job framework: arq for delivery, `job_run` for truth, Postgres advisory lock per household

- Status: Accepted
- Date: 2026-11-20
- Deciders: Lattice (architect); Keel

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
- **Idempotency:** the caller passes an explicit key (`[A-Za-z0-9:._-]`, at most 200 characters,
  for example `sync:<item>:<hour>`). A duplicate returns the existing run and creates no new row.
  If that run is still `queued`, enqueue pushes it to arq again under the same `_job_id`, which
  arq dedupes. This heals a crash between the commit and the Redis push. Reusing a key with a
  different payload raises `IdempotencyConflict`.
- **Per-household lock: a Postgres session-level advisory lock**, `pg_try_advisory_lock(ns, key)`
  on a dedicated pooled connection held for the run. If the lock is busy, the run defers with
  `Retry(lock_retry_delay)` and no attempt is used. After `max_lock_deferrals` the run fails as
  `HouseholdBusy`.
- **Exactly one success:** a conditional `UPDATE` claims the run under the lock
  (`queued|retrying|running → running`, `attempts + 1`). A run already in `succeeded` or `failed`
  is skipped. A `running` row found while holding the lock is an orphan of a dead worker and is
  re-claimed. The handler gets a household-wide session, and the runner marks success in that same
  session's final transaction, so uncommitted handler writes commit atomically with the mark.
- **Bounded retries:** capped exponential backoff (`base_delay * factor^(n-1)`, up to `max_delay`)
  until `max_attempts`. arq's own `max_tries` is set to attempts + lock deferrals + 1. On that last
  try the runner always records `failed` instead of asking for another retry, so arq can never drop
  a run that `job_run` still shows as `retrying`.
- **No secrets in errors:** `last_error` stores the exception class name only. Handler exceptions
  never reach arq, so arq doesn't log or store their messages either.
- **JSON, not pickle, on the queue:** both enqueuers (`app.jobs.queue.arq_pool`) and the worker
  use a JSON job serializer. A job carries three strings (household id, run id, payload JSON), so a
  forged Redis entry can at worst fail validation. It can't execute code.

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
- Each running job holds up to two pooled connections (the lock and its session). The worker caps
  `max_jobs` at 5 to stay within SQLAlchemy's default pool.
- Handlers must still be idempotent for any progress they commit early or any external call they
  make (Plaid), because a retry re-runs them. Writes left to the final commit are exactly-once.
- Jobs serialize per household across all job types. A sync that runs longer than
  `lock_retry_delay × max_lock_deferrals` (10 minutes by default) makes queued work fail as
  `HouseholdBusy`. Tune this per job if it happens.
- Handlers run household-wide (`household_wide=True`, ADR 0008). Person-scoped jobs (agent runs
  for one person) will need a `person_id` field in the payload and a context option. Not built yet.
- Follow-ups: a sweeper that re-pushes `queued` rows older than N minutes (crash between commit
  and push when nobody retries the enqueue), retention pruning of old `job_run` rows, wiring
  `arq_pool(app.state.redis)` into the API once the first API-triggered job lands (P7), and
  Bulwark's Redis-auth item. The import-linter domain contract now also forbids `app.jobs`.
