# 0016. Job framework: arq for delivery, `job_run` for truth, Postgres advisory lock per household

- Status: Accepted (amended by F2b, 2026-11-20: enqueue authority, timeouts, stale attempts;
  2026-11-21: write references, redelivery sweeper, household-bound side sessions; F2c: orphan
  age guard on claims, enqueue lock timeout and order, tighter boundaries)
- Date: 2026-11-20
- Deciders: Lattice (architect); Keel; Bulwark (security reviews of #24, #32 and 82cff55)

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
  **Every API route that enqueues must pass the session's household and person.**
- **Write references need write rights (Bulwark, #32).** Visibility alone let Sam enqueue a job on
  Alex's *shared* account, which the job then writes household-wide although the account's
  `person_update` policy refuses Sam that write: a confused deputy at the person boundary.
  `Ref(Model)` now means the job writes the row (the default), and enqueue looks it up with
  `SELECT ... FOR KEY SHARE`. Postgres applies a table's UPDATE policies (USING) to every row a
  locking SELECT touches and drops the rows that fail, so the check finds exactly the rows the
  requester could update directly, with the policy defined once, in the migration. Tested on
  Postgres 16 (`test_the_row_lock_applies_the_update_policy`: plain SELECT sees the row, the
  locked one doesn't, a direct UPDATE changes nothing). `FOR KEY SHARE` is the weakest row lock
  (blocks only deletes and key changes) and lasts until enqueue commits. Locking needs the UPDATE
  privilege, so a table the app can't update needs `access="read"`. Enqueue runs
  `SET LOCAL lock_timeout = '2s'` first and checks references in id order (then table name), so
  two enqueues naming the same rows take their locks in the same order and can't deadlock. A row
  another transaction holds `FOR UPDATE` (or a concurrent enqueue of the same key) past 2 s
  answers `ApiError("busy")`, a 409, with nothing recorded or queued.
  `Ref(Model, access="read")` opts out for a job that never writes that row or anything under it;
  visibility is then the bar.
- **System callers.** `requested_by=None` gets RLS's person-less view, never a household-wide one:
  it may *write*-reference joint rows (and tables with no per-person rule), and *read*-reference
  shared, summary-only and joint rows; private rows are refused either way. A worker scheduler
  that must name a person's own rows uses `app.jobs.runner.enqueue_household_wide`, which checks
  household-wide (still inside `household_id`). Request code can't reach it: import-linter lets
  only `app.jobs` and `app.worker` import `app.jobs.runner`, and
  `tests/jobs/test_job_boundaries.py` fails if anything but the runner calls `_enqueue` or
  anything outside `app/jobs` and `app/worker` names `enqueue_household_wide`.
  `tests/test_household_wide_guard.py` fails on any `household_wide=` call keyword under
  `backend/app/` other than literal `False` outside an allowlist that states each module's reason.
- **Idempotency:** the caller passes an explicit key (`[A-Za-z0-9:._-]`, at most 200 characters,
  for example `sync:<item>:<hour>`). A duplicate returns the existing run and creates no new row.
  If that run is unfinished (`queued`, `retrying`, `running`), enqueue pushes it to arq again under
  the same `_job_id` (`retrying` with `_defer_until=next_attempt_at`), which arq drops while it
  still holds the job. This heals a crash between the commit and the Redis push, and a run arq
  finished without a retry. Reusing a key with a different payload raises `IdempotencyConflict`.
- **Redelivery sweeper.** arq's backstop timeout finishes a job without retrying it, after the
  runner's cancel handler recorded `retrying`, so nothing would ever deliver that run again. A
  worker cron (`sweep_stalled_runs`, every minute) re-pushes, per household, `queued` runs older
  than 2 minutes, `retrying` runs 2 minutes past `next_attempt_at`, and `running` runs older than
  their timeout + `ARQ_TIMEOUT_MARGIN` + 2 minutes (at most 100 per household per sweep). It never
  claims or marks a run (except failing a pending run whose stored payload no longer validates).
  Pushing twice can't run twice: a delivery claims only under the household lock with a
  conditional UPDATE, so extra deliveries defer on the lock or skip a finished run
  (`test_redelivered_runs_never_run_twice` pushes one lost run three times with no arq dedupe and
  runs them concurrently: one handler run, one side effect). `household` has RLS, so the sweeper
  lists tenants with `list_household_ids()` (migration 0010, SECURITY DEFINER, ids only), which
  includes households with no login.
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
  is skipped. A `running` row is re-claimed only under the lock **and** once its `started_at` is
  older than `JobSpec.orphan_after` (the job's timeout + `ARQ_TIMEOUT_MARGIN`, the same base the
  sweeper adds its margin to); the close-out of an orphan on its last attempt has the same guard.
  The lock alone isn't proof: a dropped lock connection frees the lock while the handler, on its
  own connection, keeps running. By `orphan_after` the runner's timeout and arq's backstop have
  both cancelled it. A younger `running` row is skipped and the sweeper re-pushes it later
  (`test_a_re_push_after_the_lock_connection_drops_cannot_start_a_second_run` terminates the lock
  connection mid-run: the redelivery gets the lock and is refused). The handler gets a household-wide session, and the runner marks success in that same
  session's final transaction, so uncommitted handler writes commit atomically with the mark.
  `JobContext.factory()` opens side sessions in the run's household only (`HouseholdSessions`
  takes no household argument), so a handler can't open one elsewhere by mistake. Handler modules
  may not import `app.db` or `app.db_context` (import-linter), and a scan rejects `_factory`,
  `get_bind`, `.bind` and `set_config` (in code or SQL text) in `app/jobs/handlers/`.
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
- A run arq loses is revived within about a minute of becoming overdue. The sweep scans each
  household's `job_run` rows without a status index (adding one needs a migration); fine at
  self-host scale, revisit with retention pruning.
- Open (not built): a dropped lock connection still lets a *different* run of the same household
  start while the first is running (the same run can't start twice; the orphan age guard covers
  that), and a handler that ignores cancellation past `orphan_after` could still overlap its own
  re-claim. A Redis writer can still deny service (flood deliveries, delete jobs), though never
  run a forged payload. `job_run` doesn't record `requested_by` (P4a, needs a migration), so a
  run's requester isn't auditable and the sweeper re-pushes without knowing who asked.
- Follow-ups: retention pruning of old `job_run` rows, wiring `arq_pool(app.state.redis)` into the
  API once the first API-triggered job lands (P7), and Bulwark's Redis-auth item. The import-linter
  domain contract also forbids `app.jobs`.
