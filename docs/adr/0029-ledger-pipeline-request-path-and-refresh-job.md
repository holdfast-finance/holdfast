# 0029. Rules run where rows are written; transfers and recurring run in one refresh job

- Status: Accepted
- Date: 2026-11-21 (revised the same day after the Gadfly and Bulwark reviews of PR #59)
- Deciders: Lattice (architect); Keel

## Context
PLAN 8.2 orders categorisation as user rules, transfer detection, Plaid category, ML, then
review. Three pure engines exist (`rules`, `transfers`, `recurring`), and nothing runs them.

Constraints:
- **Rules:**
  - A retroactive rule must be previewed, and its count confirmed, before it's applied (PLAN 8.2).
  - It runs in the author's person-scoped session (PR #53), with "preview count = applied count"
    (roadmap C3).
- **Recurring:** detection is CPU-bound, about 7 s per 50,000 rows (PR #52), and must stay off the
  event loop. A timed-out detection must actually stop.
- **Transfers:** matching is household-wide, with a `job_run_id` in each automatic pair's audit
  (PR #50).
- **Ingest:** statement import runs in the uploader's request today. Plaid sync (Phase 2a) will be
  a job.
- **Jobs:** ADR 0016 jobs are serialised per household and carry IDs-only payloads.
- **Load:** a first load can be 125 statements uploaded one after another. One refresh per file
  would mean 20–50 minutes of serial worker time.

## Decision
1. **Rules run inline wherever a row is inserted.**
   - The ingest path (statement import now, sync later) loads the household's enabled, `active`
     rules once per import.
   - It runs the matcher in a worker thread (`anyio.to_thread.run_sync`), so the event loop never
     waits on pattern matching.
   - It sets `category_id`, `category_source = 'rule'` and `category_rule_id` on each
     `NewTransaction` before `LedgerWriter.insert_transaction`.
   - Categories appear together with the rows, and there's no "which rows are new" bookkeeping.
   - A rule saved without "apply to past" touches only rows ingested after it.
2. **Retroactive preview and apply run in the request path**, in the author's person-scoped
   session, with the pure matcher in a worker thread (`anyio.to_thread.run_sync`).
   - Preview returns a `preview_token`: SHA-256 over the rule's canonical definition and the sorted
     ids of the rows it would change.
   - Apply recomputes in the same kind of session, compares the token, and writes with one bulk
     `UPDATE` through `LedgerWriter.apply_rule_bulk`. That `UPDATE` re-checks precedence in its
     `WHERE`, and it fails the unit of work unless it updated exactly the expected rows.
   - Any mismatch is `409 preview_stale`, and nothing is written.
   - Budget: preview and apply in under 2 s at 50,000 visible rows. This is a benchmark, recorded
     but not a required CI gate. The required tests assert bounded work: a fixed query count, and
     one `UPDATE` per apply.
3. **One job, `ledger.refresh`, does the household-wide work, in this order:**
   1. **Link cleanup and automatic pairs**, in one household-wide session, committed together:
      - Step 0 resolves links whose leg was deleted, unmarked or rejected (ADR 0027 point 2).
      - Step 1 pairs same-owner edges and links cross-owner edges over all live history, with
        rejections honoured, re-checked under the leg lock, and `job_run_id` in the audit.
      - A rerun pairs nothing new.
   2. **Transfer suggestions per person.** For each person with a login, a
      `scoped_session(household, person)`:
      - runs the matcher over what that person sees
      - adds `other_side_marked` edges for half-accepted links
      - replaces that person's suggestions (delete and insert in one transaction)

      It never writes a pair or a link.
   3. **Recurring detection.**
      - It loads live, posted, non-transfer rows that have a merchant key, household-wide.
      - `detect_rows` runs in a `ProcessPoolExecutor(max_workers=1,
        mp_context=get_context("forkserver"))` that the worker creates at startup and closes at
        shutdown, preloading only `app.domain.recurring_series`.
      - The child gets only ids, account ids, dates, amounts and merchant keys, and its exceptions
        are logged by class name.
      - **On the 120 s timeout,** the worker terminates the child, shuts the pool down without
        waiting, and creates a new pool before failing the attempt (the retry policy applies). A
        stuck detection can't block the next one.
      - `reconcile_series` (ADR 0028) is then re-run against series locked `FOR UPDATE`, and written
        in the runner's final transaction with the success mark.

   Spec:
   - payload `{household_id}` only
   - `RetryPolicy(max_attempts=3)` with a 30 s base
   - an explicit `timeout` of 300 s (arq's own timeout comes from the spec)

   Every step is idempotent, so a retry after a partial run is safe. At most three database
   connections per job: the lock, the runner's session, one step session.
4. **Refreshes are coalesced per household.**
   - These all enqueue the key `refresh:auto:<UTC time floored to 2 minutes, %Y%m%dT%H%MZ>`:
     - a statement upload that inserted rows
     - an account ownership change
     - any transfer edit
     - (later) each sync run
   - The run is deferred until that bucket ends, plus 5 s. Every enqueue happens after its rows
     commit and before its bucket ends, so the run sees all of them and no tail check is needed.
   - A first load of 125 statements becomes one run per 2 minutes of uploading.
   - "Check again" uses `refresh:manual:<UTC minute, %Y%m%dT%H%MZ>` with no defer.
   - Both key forms match `IDEMPOTENCY_KEY_PATTERN`. An ISO time with `+00:00` wouldn't.
5. **The API can enqueue.** `arq_pool(app.state.redis)` is wired into the request context (the
   ADR 0016 follow-up). `enqueue` gains an optional `defer_until`. If Redis is down after a write
   commits, the write still succeeds, the failure is logged by class name, and the person can press
   "Check again".
6. **The request path never pairs or links automatically.**
   - `pair_transfers`, `apply_transfer_pair` and `apply_transfer_link` stay unreachable from
     `app/api/`. The guard test allows them only in `app/jobs/handlers/ledger_refresh.py`.
   - Accepting a suggestion calls `mark_transfer_pair` or `mark_transfer_link` as the person.

## Alternatives considered
- **Rules as a post-ingest job:** rows would appear uncategorised for a while, and the job would
  need to know which rows are new (sync rows have no run link). Inline is cheaper and exact.
- **A full rule pass on every refresh:** a rule saved "for new rows only" would be applied to
  history without a preview.
- **Three jobs (rules, transfers, recurring):** the per-household lock serialises them anyway,
  recurring must run after pairing (it ignores transfers), and three runs per import triple the
  `job_run` rows.
- **One refresh per file, with a tail check for rows that arrived during a run:** correct, but 125
  full-history runs on a first load.
- **`asyncio.to_thread` for recurring:** the GIL keeps a 7 s pure-Python loop competing with the
  worker's event loop (lock connection, other jobs), and a thread can't be killed on timeout.
- **A default (`fork`) process pool:** Python 3.12 forks on Linux, and this worker has an asyncio
  loop and open asyncpg sockets. `forkserver` starts children from a clean process.
- **One new process per run:** also killable, with public APIs only. It was rejected in favour of
  Bulwark's pool shape, which keeps the child warm between runs.
- **Recomputing suggestions on each page load:** no storage, but each view costs a full matcher run,
  and the review badge needs a count on every page.

## Consequences
- Statement import gains a dependency on the rules service. Ingest still writes only through
  `LedgerWriter`.
- The worker owns a process pool, and worker shutdown must close it. One detection runs at a time
  per worker, which is the intent.
- Phase 2a's sync job enqueues the same `refresh:auto` key after each committed sync run.
- Suggestions can be up to one refresh old. The review list joins live rows and hides a group once
  every leg the reader can write is a transfer, superseded or rejected.
- **A deviation from PLAN 8.2:** a rule's category doesn't stop transfer pairing. PLAN's "first hit
  wins" ordering would let a broad rule hide a card payment as spending. Here `is_transfer` and the
  category are independent, and a rule that targets a transfer-kind category only changes the
  category.
