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

---

## Round 2 (f651389)

I checked Revision 2 of the contract and its section 11 answers against the merged code:
- `statement_import._account`
- the `IMPORT_RUN_WRITE` predicate in migration 0005
- the 0002 account predicates
- `services/jobs.enqueue`
- `LedgerWriter._lock_group` and `unmark_transfer`
- `household.py`

**Verdict: REWORK-small.** Most items are closed. The remaining four blockers are local edits:
- A1's real-data workflow
- one acceptance criterion that can't pass
- the go-ahead claim for live migrations
- one stuck half-accepted move

### Earlier blockers, checked against the code

| Item | Status | Why |
|---|---|---|
| Pass 1 B1, cross-owner moves | **Partly closed** | Links and auto-marking close the hinted case. A half-accepted move between two private accounts still sticks (R2-B4) |
| Pass 1 B2, supersession carry-over | Closed on paper | `supersede_transaction` and the re-point trigger are specified and tested. Phase 4 has no caller. A series split remains (R2-S2) |
| Pass 1 B3, regex gate | Closed | `compile_pattern` refuses every input until C3d. The `ast` test covers `rules.py`, which still imports `re` today, so C3c must delete that import |
| Pass 1 B4, C7 evidence | **Partly closed** | Criterion 2(d) contradicts 7.4 (R2-B2) |
| Pass 2 B1, ownership | **Partly closed** | A1 now exists, but it locks the bookkeeper out of imports (R2-B1) |
| Pass 2 B2, dropped pairs | Closed | `pairs` and `links` in a person's view become `likely` edges, and C4b names the test |
| Pass 2 B3, orphaned leg | Closed | The delete trigger, plus `unmark_transfer` with one surviving leg. Residual in R2-S1 |
| Pass 2 B4, real data | **Partly closed** | 7.5 is good. "Nothing waits on the owner" is unproven (R2-B3) |
| Pass 2 B5, races | Closed | Lock then re-check, and `FOR UPDATE` plus a re-plan. State that the rejection check is a separate statement run after `_lock_group` returns. Only a new statement gets a fresh READ COMMITTED snapshot |

### Round 2 blockers

**R2-B1. Giving an account to the other person locks the uploader out of it (5.0, 6 item 0).**
- In this household one person uploads every statement.
- After `PATCH owner: <spouse>`, the uploader can't import that account's statements again. Two
  policies refuse it:
  - `LedgerWriter.insert_transaction` needs `ACCOUNT_DETAIL_WRITABLE` (owner or joint).
  - `IMPORT_RUN_WRITE` (0005) also needs a writable account.
  - With `private`, the earlier `_account` lookup already raises `AccountNotVisibleError`.
- Runs already in `needs_review` on that account get stuck for both people:
  - The new owner can't accept them, because `d.person_id` is the uploader's.
  - The uploader can't accept them, because the account isn't theirs to write.
- If the new owner has no login, `private` hides the account from every person. Only a
  household-wide session can reach it, and the API has none.

Change:
- A1 refuses `owner: <person>` when that person has no login.
- The sheet says plainly: "You won't be able to import statements for this account."
- The sheet recommends **Joint** to a household's sole bookkeeper.
- Record the long-term fix as a follow-up: a household-owner import path through the existing
  `import_statement(household_wide=True)`.
- A1 test: give the account away, then upload as the giver. The upload is refused with a clear
  error, not a 500.

**R2-B2. Criterion 2(d) can't pass as the simulation is written (0 criterion 2, 7.4).**
- The generator plants unhinted transfers, which become suggestions.
- The simulation (7.4 steps 1–5) never accepts or rejects a suggestion.
- Their outflows therefore count as spend, and the "spend total equals truth exactly" check fails
  every time. The alternative is that Sounding quietly redefines the truth.

Change: add simulation step 6. Each person accepts every review card whose truth is a transfer and
rejects the rest. Measure (d) after step 6. Also report it after the refresh in step 5, without
clicks.

**R2-B3. "Nothing waits on the owner" isn't backed by the record (7.5, 9).**
- `CHECKPOINT-2026-11-21.md` says the live instance is pre-#47 and "Do not do it without" an owner
  go-ahead. Nothing on `main` records an upgrade or a dump.
- CLAUDE.md puts real data in the owner queue. Phase 4 then adds three migrations to that database.

Change: write the evidence into 7.5:
- the date of the upgrade
- `alembic current` on the instance
- the dump's existence (path class only)
- the owner's go-ahead, and whether it is a standing one for Phase 4 migrations

Without a standing go-ahead, each of M1, M3 and M4 is an owner-queue item.

**R2-B4. A half-accepted move between two private accounts never reaches the second person (4.1
step 2, 3.3 `transfer_suggestion` read policy).**
- `other_side_marked` is a suggestion, and its read policy needs both legs detail-visible.
- Take a move from B's private checking to A's shared card. B sees both legs and accepts, so B's
  leg is marked and a `person` link is inserted. A can't see B's checking, so A never gets the
  offer. A's card inflow stays counted for good.
- Criterion 2(b) still passes, because B was offered the card.

Change:
- For `reason = 'other_side_marked'` only, the policy needs only the reader's own leg to be visible.
- The card shows the reader's leg and the line "Another household member marked the other side".
- This is the leak ADR 0027 already accepts for job links.
- C4c test: B accepts and A gets the offer.

### Round 2 should-fixes

**R2-S1. The delete trigger undoes people's pairs; links keep them (3.3 `transaction_dissolve_orphan`
against 4.1 step 0(a)).**
- The trigger always clears the surviving leg. Step 0(a) clears the survivor only for `job` links.
- So when a person's manual pair loses a leg (re-import, Plaid `removed`), the survivor goes back
  to spending, while a person's link survives the same event. That contradicts "never changes a
  mark a person made".

Fix: groups don't record their origin, so pick one rule and state it in ADR 0027. Either:
- the survivor of any deleted leg becomes a single leg and stays marked, or
- record pair origin the way links do.

**R2-S2. Plaid supersession will still split confirmed series (D13, ADR 0028).**
- `merchant_key` prefers `merchant_name`. Statement rows have none, and Plaid rows do. So
  re-pointed members land in a different detector group from the older statement-only members of
  the same bill.
- The bill then forms two series, and the lapsed-merge rule needs equal keys, so it doesn't fire.
- The stored id, and with it `confirmed` and the label, follows the larger overlap. That's usually
  the long statement history, which drifts to "possibly cancelled". The live bill comes back as a
  new `auto` series.
- The "supersede every member" test passes. A partial supersession over Plaid's 24-month window
  would fail.

Fix: when a stored series' members split across detections, the id and the state follow the
detection that holds the **newest** member. Add a test: supersede the newest 12 of 30 members under
a new key.

**R2-S3. The rejected undo for retroactive apply rests on a false premise (10, 11.1 S5).**
- The answer says every overwritten value can be re-derived. On statement-only data the overwritten
  value is almost always "uncategorised" (`category_source IS NULL`), and nothing can restore it:
  - `release` works only on `user` rows.
  - Setting a row to null makes it a `user` clear.
- One bad "Save and apply to 2,000" can only be repaired by hand or by a counter-rule.

Fix, with no new table: add `POST /rules/{id}/clear`. It previews first, then sets category,
source and `category_rule_id` to NULL `WHERE category_rule_id = :id AND category_source = 'rule'`,
through `LedgerWriter`, with an audit row. `category_rule_id` already records which rule set each
row. Plaid-sourced priors are lost, but they're 0% of real data and re-derivable from the PFC.

**R2-S4. Wave 0 runs C3b and A1 in parallel, but they own the same files (7.1, 7.6).**
- Both own `services/ledger_service.py` and `tests/ledger/test_ledger_service.py`.
- Both are Ledger-owned and Bulwark-gated.

Fix: order them (A1, then C3b) or split the file ownership.

**R2-S5. Small contradictions.**
- 5.0 says "Any member may change a joint account's visibility". The 422 refuses `visibility` on a
  joint account.
- The joint-claim role check exists only in the service. `account_guard_ownership` still lets any
  member re-own a joint account. State this as a residual, or check `app_user.role` in M3.
- The healing re-push in `services.jobs.enqueue` (a duplicate while `queued`) must carry
  `_defer_until` as well. Without it, a re-push after a Redis blip runs mid-bucket and misses that
  bucket's later uploads.

### Rejections

| Rejection | Holds? | Why |
|---|---|---|
| Merging M3 and M4 | **Holds** | The cost is one more token handoff. Keeping each RLS review whole is worth that. M4 depends only on M3's re-point function |
| Recurring before transfers | **Weak, but acceptable** | The only harm named is a hollow confirmed series once pairing lands. One line in ADR 0028 removes it: "a series whose live members are all `is_transfer` is deleted whatever its state". Then recurring could follow a job-skeleton slice (the C4d framework without steps 0–2), and M4 would no longer wait behind M3, the largest Bulwark gate. Keep the order if transfers stay on schedule. Otherwise swap, with that line |
| Undo record for retroactive apply | **Doesn't hold as argued** | See R2-S3. The table can stay out, but a "clear this rule's categories" action is needed |
| Per-account opt-out from another person's rule | **Holds** | In a two-adult household, setting the row (user wins) or writing a higher-priority rule is enough. The residual is a priority war, which isn't a Phase 4 problem |

### Shortest path to one working screen on the owner's real data

The shortest path is **C3c → C6a → W1 → redeploy the live instance from `main`**:
- three PRs, then one deploy
- **no migration**: `category_rule_id` arrives with C3e
- the result is manual categorisation on the Activity tab

The waves reach it in wave 2, but not first:
- C6a waits on A1 only because they share files (`schemas/ledger.py`, `services/ledger.py`). A1 is
  a Bulwark-gated write endpoint, so if it slips, W1 slips.
- **No wave updates the live instance.** Merging to `main` doesn't put anything in front of the
  owner, and 7.5 rebuilds only the restore copy. As written, the owner sees no Phase 4 screen until
  the phase is accepted.

Fix:
- Drop C6a's dependency on A1. A1 rebases, because the conflicts are additive fields.
- Add a "live redeploy" step, dump first, after W1 (no migration) and after W2 (M1, rehearsed).
  It's either covered by the go-ahead in R2-B3 or sent to the owner queue.

The first screen with real value is rules (W2, wave 3). It needs M1 on the live instance.

## Round 3 (fcb3a4e)

Checked against `f651389..fcb3a4e`, migrations 0002/0005/0010, `statement_import._account`,
`LedgerWriter.set_category`/`release_category`, `docs/planning/SPRINTS.md`, and PHASE-2 at `6df9c51`.

**Verdict: REWORK-small.** R2-B1, R2-B2 and R2-B4 are closed. R2-B3 isn't. Four more local edits.

| Round 2 item | Status | Why |
|---|---|---|
| R2-B1 | Closed | No hand-over; joint passes `ACCOUNT_DETAIL_WRITABLE` and `IMPORT_RUN_WRITE` (0002, 0005) |
| R2-B2 | Closed | Step 6 plus the planted, added-back residual |
| R2-B3 | **Open** (R3-B1) | The cited record doesn't exist |
| R2-B4 | Closed | Step 0(e) leg offers, created household-wide, own leg only |

**R3-B1. The upgrade record 7.5 cites isn't in the sprint log.**
- 7.5 says the 2026-11-21 upgrade (dump, 0008 and 0009, row counts unchanged) "is recorded in
  `docs/planning/SPRINTS.md`". On this branch and on `main`, SPRINTS.md only says "blocked: live
  instance upgrade waits on the owner". `CHECKPOINT-2026-11-21.md` says "Do not do it without one".
  The only source for the claim is cc7949d's own sentence.
- CLAUDE.md names real data as an owner-queue item, so the autonomy decision alone doesn't cover
  migrating the real-data database.

Fix: commit the real entry to SPRINTS.md in this PR: the date, `alembic current` on the instance,
that the dump exists (path class only), the row-count parity, and the owner's words with their date.
If there's no entry, the first redeploy is an owner-queue item.

**R3-B2. The shortest path still needs a migration.**
- (a) C6a's `can_edit` is `app_account_writable(account_id)` (5.1). That function is created in M1
  (3.1, C3b). C6a, listed as "C3c only", would call a function that doesn't exist yet.
  Fix: C6a computes `can_edit` inline from the account row it already joins (`owner_person_id IS
  NULL OR = principal.person_id`). C3b switches it to the helper and adds an equivalence test.
- (b) The redeploy after W1 isn't "no migration". By 7.5's own record the instance is at 0009, and
  `main` already has `0010_list_household_ids`. If they've merged by then, P4a's `connector_link`
  (the `statement_key` backfill and the unique-index swap on `account`) and M1 (C3b is in wave 1)
  come too. 7.5 step 1 rehearses only "each Phase 4 migration".
  Fix: before every redeploy, compare `alembic current` on the instance with `main`'s head, and
  rehearse every pending revision, whatever lane it came from. Remove "no migration" from wave 2,
  or pin the deploy to a SHA.

**R3-B3. Deleting a rule makes its categories impossible to clear.**
- What works: clear resets rows the rule set to NULL category, source and rule id, with an
  exact-count check. Rows people set (`user`) are left alone. A row that was uncategorised comes
  back exactly as it was.
- The hole: DELETE sets `category_rule_id` to NULL and leaves the source as `rule` (4.4). After
  that, neither clear nor any filter can find those rows. Delete sits next to Clear in the rule list
  (6 item 3), and deleting is the natural reaction to a bad rule.
- Fix: DELETE returns `409 rule_has_categories` while live rows carry the rule's id, unless the
  request passes `keep_categories=true`. The sheet offers "Clear N and delete", which runs the clear
  token and the delete in one transaction. Add a test.
- Also fix the copy: "These N rows become uncategorised." Rows the rule took from another rule, and
  released rows (`release_category` keeps their category), aren't restored.

**R3-B4. Claiming a joint account brings back R2-B1's stuck runs.**
- When the household owner claims a joint account, the other member's `pending` or `needs_review`
  runs on it can't be accepted by anyone. `IMPORT_RUN_WRITE` needs both `d.person_id =
  app_person_id()` and a writable account, and after the claim no single person has both.
- The A1 test "both people can … accept its `needs_review` runs" also fails as written if it means
  one person accepting the other's run.
- Fix: the claim returns 409 while another person has an unfinished run on the account. The test
  reads "each person accepts their own run".

**R3-B5. Gaps between the two contracts.**
- `post_pending` (PHASE-2 §3.3 only) moves the category by rank. C3b moves `category_rule_id` in
  the two supersede verbs only, and 4.5's `apply_category` clears it otherwise. So a Plaid row that
  goes from pending to posted shows source `rule` with no rule id, and Clear and "Set by rule"
  both miss it.
  Fix: C3b also carries `category_rule_id` in `post_pending`, with a test, and 4.4 points to it.
- The shared migration and file order is in PHASE-2 **§4**, not §3.3 (header, section 3).
- Wave 1's "C3b once #31 and P2a have merged" leaves out P4a, the token holder before M1 (3, 7.2).

## Round 4 (038a875)

Checked against `fcb3a4e..038a875`, `SPRINTS.md`, 0002/0005 RLS, and PHASE-2 as merged (#62).

**Verdict: REWORK-small, one item (R4-B1).** Everything else from round 3 is closed.

| Round 3 item | Status | Why |
|---|---|---|
| R3-B1 record | Closed | "Checkpoint 2026-11-21 evening" has the owner's words and date, `alembic current` after each upgrade (0009, then 0010) and count parity. Reading those words as a standing go-ahead is Keel's call. It's recorded and can be withdrawn |
| R3-B2 shortest path | Closed | `can_edit` is computed inline in C6a and moves to the helper in C3b with an equivalence test. Every redeploy rehearses every pending revision, from any lane |
| R3-B3 delete | Closed, but see R4-B1 | 409 `rule_has_categories`, clear-and-delete in one transaction, `keep_categories` passed explicitly, the copy corrected |
| R3-B4 claim | Closed | `document` has no person policy, so the claimer can see the other person's runs on a joint account and the 409 check works under RLS. The test wording is fixed |
| R3-B5 cross-contract | Closed | C3b covers `post_pending`, with a test. §4 is cited. P4a is in wave 1. PHASE-2 text follow-ups are listed in 11.7 |

**Ruling on the live-rows deviation:** it holds for *blocking*, but not for *clearing*.

**R4-B1. Un-adopt brings back a category that "Clear N and delete" removed, with no way to find it.**
- `supersede_transaction` copies the category to the winner and never clears the loser. So the
  superseded statement row keeps source `rule` and the rule's id.
- Clear-and-delete clears only live rows. Then the FK sets the superseded row's rule id to NULL.
- Later, `remove_external` or un-adopt runs `unsupersede_transaction`. Step 5 moves the category
  to `old` only when the winner's source outranks old's. The winner's source is now NULL, so `old`
  keeps its own category: it comes back live with source `rule` and a NULL rule id. That's exactly
  the orphan R3-B3 closed.

Fix (C3e):
- In clear-and-delete, the clear also resets superseded rows that carry the rule's id. That's one
  more UPDATE, with no count check, not included in N, and audited under the same `rule.cleared`.
- Test: supersede a rule-categorised row, clear-and-delete, then unsupersede. The old row comes
  back uncategorised.
- `keep_categories=true` keeps today's behaviour, knowingly.

Not blocking:
- SPRINTS Upgrade 2 names its dump as `holdfast-pre-0010-<timestamp>.dump`, but 11.8 says "by name".
  Fill in the timestamp.
- The claim's `FOR NO KEY UPDATE` doesn't conflict with the `FOR KEY SHARE` lock that the FK check
  takes when an import run is inserted. So an upload that starts after the 409 check can still be
  stranded. State it as a residual next to the service-only claim check.
