# 0029. Rules run where rows are written; transfers and recurring run in one refresh job

- Status: Accepted
- Date: 2026-11-21
- Deciders: Lattice (architect); Keel

## Context
PLAN 8.2 orders categorisation as user rules, transfer detection, Plaid category, ML, then
review. Three pure engines exist (`rules`, `transfers`, `recurring`) and nothing runs them.
Constraints:
- A retroactive rule must be previewed and its count confirmed before it is applied (PLAN 8.2),
  in the author's person-scoped session (PR #53), with "preview count = applied count" (roadmap C3).
- Recurring detection is CPU-bound: about 7 s per 50,000 rows (PR #52) and must stay off the
  event loop.
- Transfer matching is household-wide, with a `job_run_id` in each automatic pair's audit (PR #50).
- Statement import runs in the uploader's request today; Plaid sync (Phase 2a) will be a job.
- ADR 0016 jobs are serialised per household and carry IDs-only payloads.

## Decision
1. **Rules run inline wherever a row is inserted.** The ingest path (statement import now, sync
   later) loads the household's enabled rules once per import and sets `category_id`,
   `category_source = 'rule'` and `category_rule_id` on each `NewTransaction` before
   `LedgerWriter.insert_transaction`. Categories appear together with the rows, and there is no
   "which rows are new" bookkeeping. A rule saved without "apply to past" touches only rows
   ingested after it.
2. **Retroactive preview and apply run in the request path**, in the author's person-scoped
   session, with the pure matcher in a worker thread (`anyio.to_thread.run_sync`). Preview returns
   a `preview_token`: SHA-256 over the rule's canonical definition and the sorted ids of the rows
   it would change. Apply recomputes in the same kind of session, compares the token, and writes
   with one bulk `UPDATE` through `LedgerWriter.apply_rule_bulk`, which re-checks precedence in
   its `WHERE` and fails the unit of work unless it updated exactly the expected rows. Any
   mismatch is `409 preview_stale` and nothing is written. Budget: preview and apply in under 2 s
   at 50,000 visible rows on CI.
3. **One job, `ledger.refresh`, does the household-wide work, in this order:**
   1. Automatic transfer pairs over all live history (household-wide session, rejections honoured,
      same-owner only, `job_run_id` in the audit). Committed as its own step. A rerun pairs
      nothing new.
   2. Transfer suggestions per person: for each person with a login, a `scoped_session(household,
      person)` runs the matcher over what that person sees, then replaces that person's
      suggestions (delete and insert in one transaction).
   3. Recurring detection: household-wide load of live, posted, non-transfer rows; `detect_recurring`
      runs in a `ProcessPoolExecutor(max_workers=1)` owned by the worker, with a 120 s timeout
      (timeout fails the attempt; the retry policy applies); `reconcile_series` (ADR 0028) is
      written in the runner's final transaction with the success mark.
   Payload `{household_id}` only. Idempotency keys: `refresh:import:<document_id>` after a statement
   upload, `refresh:sync:<sync_run_id>` after a sync (Phase 2a), and `refresh:manual:<UTC time
   floored to 5 minutes>` for the "Check again" button. Every step is idempotent, so a retry
   after a partial run is safe.
4. **The API can enqueue.** `arq_pool(app.state.redis)` is wired into the request context (the
   ADR 0016 follow-up). If Redis is down after an import commits, the upload still succeeds; the
   failure is logged by class name and the person can press "Check again".
5. **The request path never pairs automatically.** `pair_transfers` and `apply_transfer_pair` stay
   unreachable from `app/api/` (the guard test keeps that half). Accepting a suggestion calls
   `mark_transfer_pair` or `mark_transfer_single` as the person.

## Alternatives considered
- **Rules as a post-ingest job:** rows would appear uncategorised for a while, and the job would
  need to know which rows are new (sync rows have no run link). Inline is cheaper and exact.
- **A full rule pass on every refresh:** a rule saved "for new rows only" would be applied to
  history without a preview.
- **Three jobs (rules, transfers, recurring):** the per-household lock serialises them anyway,
  recurring must run after pairing (it ignores transfers), and three runs per import triple the
  `job_run` rows.
- **`asyncio.to_thread` for recurring:** the GIL keeps a 7 s pure-Python loop competing with the
  worker's event loop (lock connection, other jobs); a process can also be killed on timeout.
- **Recomputing suggestions on each page load:** no storage, but each view costs a full matcher
  run and the review badge needs a count on every page.

## Consequences
- Statement import gains a dependency on the rules service. Ingest still writes only through
  `LedgerWriter`.
- The worker owns a process pool; worker shutdown must close it. One detection runs at a time per
  worker, which is the intent.
- Phase 2a's sync job enqueues `ledger.refresh` after each committed sync run.
- Suggestions can be up to one refresh old. The review list joins live rows and hides any
  suggestion whose leg has since become a transfer, been superseded or been rejected.
