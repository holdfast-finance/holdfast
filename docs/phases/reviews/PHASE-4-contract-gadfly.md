# Gadfly: Phase 4 wiring contract, second pass

- Target: `docs/phases/PHASE-4-contract.md`, ADRs 0024 and 0026 to 0029 at `0fab87f` (PR #59).
- Checked against the merged code: `domain/{rules,transfers,recurring}.py`,
  `services/{transfer_pairing,ledger_service,statement_import,imports,household,jobs}.py`,
  `jobs/{registry,runner}.py`, `worker/main.py`, and migration 0002's account predicates.
- `PHASE-4-gadfly.md`, the first pass at the same SHA, still stands: cross-owner legs, supersession
  carry-over, the regex gate in the domain, and C7 evidence. This pass doesn't repeat it. Both sets
  of blockers must be closed.

**Verdict: REWORK-large.** The engines are sound. The wiring isn't ready for this household's real data:
- The ownership model has no producer and no way to change.
- One review path silently drops pairs.
- Deleting a paired leg corrupts its partner.
- Acceptance can pass without the owner's data ever being loaded.

## Blockers

**B1. Real data can't express this household, and ownership changes break the same-owner invariant
(D2, D7, ADR 0026 point 3, ADR 0027 point 1 and Consequences, §3.1 `rule_account`).**
- Code: `statement_import._account` creates every imported account with
  `owner_person_id = uploader` and `visibility = 'private'`. A joint account can only come from a
  household-wide import job, and none exists (`app/jobs/handlers/` is empty).
- No API changes an account's owner or visibility. `routes_ledger` has GET only.
  `household.py` tells a person-delete to "Reassign them first", but nothing can reassign.
- So the owner's 125 statements produce one owner and only private accounts:
  - The second person sees nothing: no rows, rules, suggestions or series.
  - `joint` scope, `different_owners` and cross-owner single legs have no real rows to act on.
  - The joint-account design gets tested on synthetic data only.
- The DB trigger `account_guard_ownership` already lets an owner give an account to someone else
  or make it joint. Nothing in the database enforces "pair = same owner". `_check_pair` checks it
  only when a pair is made. The first ownership change leaves cross-owner pairs that ADR 0027 says
  can't exist. ADR 0027 covers only joint to owned ("claim"). The transition real data needs is
  owned to joint or owned to spouse.

Change:
- Add slice **A1** (no migration): `PATCH /accounts/{id} {owner: "me" | "joint" | <person_id>,
  visibility}`.
- In the same unit of work, dissolve every pair that becomes cross-owner into two single legs.
- Audit `account.ownership_changed`.
- A1 comes before C4c. If you decide not to build A1, state in the contract that Phase 4 is single-owner on real data and
  cut `joint`/`own` scope (S5).

**B2. The per-person pass drops confident pairs, and those rows stay counted as spending (§2.4
`review_edges`, ADR 0029 point 3.2, ADR 0026 point 4).**
- `review_edges` turns only `suggested` and `ambiguous` into edges. The per-person pass also
  returns `result.pairs`: edges that look certain in A's view but that the household-wide step 1
  didn't pair.
- That happens when a row A can't see competed for the leg. Example: A's checking pays A's savings
  $500 "TRANSFER". B's private checking takes in $500 "TRANSFER" the same day. Step 1 sees two
  equally good edges, so it marks the leg ambiguous and stores nothing. A's view has one confident
  edge, which lands in `pairs`, and `review_edges` discards it.
- Nobody is ever shown the transfer, and both legs count. Round-amount moves (rent shares,
  allowances) hit this often.

Change:
- `review_edges` also emits each per-person `pairs` entry with a new reason `likely`. Add it to the
  `transfer_suggestion.reason` CHECK and the API enum.
- Accept follows the normal `can_pair` / `marked` path.
- C4c evidence: a hidden competitor still yields a visible suggestion for A.

**B3. Deleting or superseding a paired leg corrupts its partner and breaks series identity (§4 "Rows
a person has touched", ADR 0027 Alternatives, ADR 0028 point 1).**
- Code: `unmark_transfer` raises `TransferPairError` unless it finds **two** legs in the group.
  `transfer_group_id` has no FK.
- When a paired leg is deleted (Plaid `removed`, the queued "re-import this document" action, or
  import undo), the partner is left `is_transfer = true`. It's excluded from spend for good, and
  it can never be unmarked, by anyone.
- Supersession: a `recurring_member` keeps pointing at the superseded id. Detection loads only live
  rows, so overlap with the stored series is zero for any series that lies wholly inside Plaid's
  24-month window. The result:
  - a new id
  - the `auto` series is deleted, along with its label
  - a `confirmed` series stays as a hollow duplicate that drifts to "possibly cancelled"
- The detail view lists superseded members.

Change:
- An `AFTER DELETE` trigger, or a `LedgerWriter.delete_transaction`, clears the partner's
  `is_transfer` and `transfer_group_id`. ADR 0027's same-owner rule makes that write safe.
- `unmark_transfer` accepts a group with one surviving leg.
- `reconcile_series` maps stored member ids through `superseded_by_id` before measuring overlap.
  Member listings filter `superseded_by_id IS NULL`.
- Tests:
  - delete one leg, then unmark the other: succeeds
  - supersede every member of a confirmed series, then refresh: same id, still confirmed

**B4. Acceptance can pass without the feature working on the owner's data (Phase 4 acceptance,
§9.3, §7 C7 and W rows).**
- PLAN 13 says "recurring list matches **your real** bills". The contract swaps in a synthetic 95%
  and records real data as "unverified until the owner runs it". ADR 0019 lets Holdfast agents
  load and read the owner's statements locally, so Sounding can run this check with counts only.
- **Unlisted dependency:** the live instance is still on the pre-#47 build and waiting on an owner
  go-ahead (CHECKPOINT, "Owner decisions waiting"). Phase 4 adds migrations M1, M3 and M4 to a
  database that holds real data. Until the owner says yes, nothing Phase 4 builds is usable on that
  data.
- Criterion 4 can't fail: `apply_rule_bulk` refuses any count mismatch. A rule that matches zero
  rows passes.
- The W1–W4 evidence is MSW mocks only, so every screen can pass with the backend unwired.

Change:
- Put "upgrade the live instance (dump to the private directory first)" in the owner queue now.
- Rehearse M1–M4 up/down/up on a local restore of that dump. It stays local, per ADR 0019.
- C7 gains a local real-data run. It reports only these counts:
  - percent categorised
  - pairs made
  - suggestions open
  - series per tier
  - owner-confirmed series as a share of bills found
- Rewrite criterion 4 as two checks:
  - after apply, `preview` of the full rule set over all rows is empty
  - a rule saved before an import gives the same categories as the same rule applied after it
- Add one Playwright e2e on a seeded stack: upload a synthetic statement, set a category, make a
  rule, apply it, see the transfers tab, see the recurring tab.

**B5. The job overwrites people's choices made while it runs (ADR 0029 points 3.1 and 3.3, ADR 0028
point 1).**
- Step 3 plans `reconcile_series` from series it loaded before detection, which takes about 7 s or
  more. If a person confirms or labels an `auto` series in that window, the final write can delete
  it or overwrite it. The PATCH already returned 200, so the choice is lost. ADR 0028 says this never
  happens.
- Step 1 has the same race. The code's `_pair(auto=True)` rechecks only `is_pending` and
  `is_transfer` after locking. A rejection written after the load gets paired anyway.

Change:
- After detection, `SELECT … FOR UPDATE` the stored series and re-plan. The re-plan is pure and
  cheap.
- Deletes carry `WHERE user_state = 'auto' AND label IS NULL`. Upserts never write `user_state` or
  `label`.
- `_pair(auto=True)` rechecks `NOT EXISTS transfer_rejection` for the edge and both legs, under the
  lock.

## Should-fix

**S1. Series identity holes (ADR 0028 points 1 and 4).**
- A PATCH that sets `label` leaves `user_state = 'auto'`. The next unmatched run deletes the series,
  so "label sticks" is false.
- When a detection claims every member of a confirmed series, the series is kept with no members
  and stale columns, next to a new `auto` twin. The Bills list shows a duplicate.

Fix:
- Setting a label implies `confirmed`, unless the series is `dismissed`.
- When a detection takes most of a user-stated series' members, move `user_state` and `label` to
  that detection and delete the empty series.

**S2. The process pool as specified can't do what ADR 0029 claims (point 3.3, §7.1 C5b).**
- `ProcessPoolExecutor` can't kill a running task. A timeout leaves the single worker process busy,
  and every later detection waits behind it.
- The arq timeout is `DEFAULT_TIMEOUT` = 5 min, which differs from the contract's 120 s.
- Python 3.12 forks on Linux. This worker has an asyncio loop and open asyncpg sockets.
- `backend/app/worker.py` doesn't exist. The file is `app/worker/main.py`.

Fix:
- One `multiprocessing` `forkserver` process per run. On timeout, call `terminate()` and then
  `join()`.
- The child imports only `app.domain.recurring`.
- Set the spec timeout explicitly.
- Correct the path.

**S3. Refresh fan-out and staleness (§4 triggers, ADR 0029 point 3).**
- A first load of 125 statements queues 125 full-history runs, one after another under the
  household lock. Each runs step 1, then two person passes, then detection plus pickling, for
  about 10–25 s at 50k rows. That's 20–50 minutes of worker time, with the badge and lists
  changing throughout.
- Accept, mark and unmark trigger nothing, so suggestions and series stay stale until the next
  import.

Fix:
- Use the key `refresh:auto:<UTC 2-minute bucket>` and set arq `_defer_by` to 60 s.
- When the handler finishes, it checks whether any row was created after its load. If so, it
  enqueues the next bucket.
- Transfer edits enqueue the same key.

**S4. The migration path is longer than it needs to be (§3 order, §7 "Soonest visible").**
- There are four migrations under one token, and the Plaid lane's two interleave.
- M2 (I1d) is an import follow-up, not Phase 4 wiring.
- Recurring, the most visible feature, waits on C4b, C4c (M3), C4d and then C5b (M4).

Fix:
- Take M2 out of this plan.
- Merge M3 and M4 into one "derived tables" migration, landed right after M1. Bulwark reviews the
  derived-table RLS once.
- C5b creates `ledger.refresh` with step 3 only, and C4d adds steps 1–2. Recurring then ships
  without waiting on transfer corrections. It's safe to ship recurring first: unpaired card
  payments drop out of the series on the first run after pairing lands, through overlap
  reconciliation.

**S5. Cut account scope from Phase 4 (§3.1 `rule_account`, `rule_scope_guard`, §5.2, §6.2).**
Given B1, `rule_may_touch` already limits a rule to rows the author may write. `rule_account`, its
two triggers, three 422 cases and the UI field add Bulwark surface to the first visible path but no
real data uses them. Ship `any` only. Adding scope later is an additive migration.

**S6. The contract's types don't match the code.**
- `RecurringSeries` has no `direction`, `first_date` or currency. Its ids are `str | UUID`, but
  `StoredSeries` uses `UUID`.
- `PostedTransaction.merchant_key: str` is required, but `merchant_key()` returns `None`.
- Confidence is stored as `NUMERIC(4,3)`, but the tier test is "> 0.60" on raw `Decimal`. A raw
  0.6004 gives a different tier before and after storage. Quantize in Python and compute the tier
  from the stored value.
- Step 2 needs a matcher loader that doesn't write. Called in a person session without a user,
  `pair_transfers` raises. Name the loader and give C4c ownership of it.
- The comment in `worker/main.py` ("two connections per job") no longer holds once person sessions
  exist.
- `web/src/mocks/handlers.ts` isn't in any slice's ownership list.

**S7. Wall-clock limits in CI will be flaky.** The 2 s limit at 50k rows, the 30 s job budget and the
heartbeat test are all timed on free-tier runners. Put them in a non-required benchmark job that
records the numbers. The required tests should assert bounded work instead: no N+1 queries, and one
`UPDATE` per apply.
