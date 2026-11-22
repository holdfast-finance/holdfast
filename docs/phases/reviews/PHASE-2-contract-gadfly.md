# Gadfly: Phase 2 connector-lane contract (PR #62)

- Target: `docs/phases/PHASE-2-contract.md`, ADRs 0030, 0032, 0033, 0034 at `77aafbe`.
- Checked against: `origin/main` at `ba92ca3` (the contract says `733132e`; #60 has landed since),
  `origin/feat/P3-plaid-client-preflight` (#56, head now `cb97f73`, not `cba7f7b`),
  `origin/feat/F5c-secret-resolver-rotation` (#31, `b8fc45c`), `origin/fix/F2b-job-hardening`
  (#32, `070a92a`, 24 commits behind main), local `feat/P9a-plaid-setup-page` (`d973679`), and
  `origin/docs/phase4-wiring-contract` (#59).
- Real data: none read. Every claim below comes from code, profiles and docs in the repo.

## Verdict: REWORK-large

The interfaces (§3.1, §3.2) and the sync shape (D8) hold up. The money path that gives the owner
first value does not. The exact-key dedupe will miss most card overlaps in the owner's real
data. Adoption is decided on mask and type alone, can't be undone, and drops the transfer,
recurring and split state the ledger already holds. The gates (G1, M-live) can pass on sandbox
data and the owner saying "it works" while production is wrong. The owner's existing `.env`
overrides D5. Each of these is cheap to fix now and permanent or costly to fix after the first
production Item.

---

## Blockers

### B1. §2 D7, §3.4 step 6: exact `(account_id, posted_date, amount)` dedupe misses most real card overlaps
**Failure.** The merged card profiles capture the **first** of the two printed dates as the row
date. `navy-federal-card.yaml` lines 20 and 23: `(?P<date>\d\d/\d\d/\d\d)\s+\d\d/\d\d/\d\d\s+\S+`.
That first date is the transaction date, and the post date is skipped. Plaid's `date` on a posted
row is the **posting** date, and the transaction date is in `authorized_date`. Any purchase that
posts a day or more after it was made fails the exact key. Interest rows are dated at period end
(same profile, line 3). The owner has about 3,300 imported rows. `days_requested = 730` overlaps
essentially all of them, so one initial sync writes both copies of every miss into live data:
net worth, categories, transfer pairing and recurring detection all see duplicates. P8/I4 cleanup
comes later, and the 100-interleaving property test can't catch this because its synthetic
connector rows share the import's dates by construction.
**Change.**
1. The P2b matcher compares the import date against **both** Plaid `posted_date` and
   `authorized_date` (exact first, then ±3 days on a unique same-amount candidate). Ties go to
   review, never to a guess. This pulls the I4 core into P2b.
2. Add a **statement horizon** to adoption. Per adopted account,
   `connector_since = max(import_run.period_end) + 1`. Connector rows dated on or before it are
   not inserted. Imported history stays authoritative there, so the first sync never mutates the
   3,300 rows. Only statements imported **after** linking go through supersession.
3. Add a fixture built from **sanitized** real-layout rows (`make sanitize`) where trans date ≠
   post date. It must pass before P2b merges.

### B2. ADR 0033 §1 and §5: the match is a guess the owner can't check and can't undo
**Failure.**
- The candidate filter is "same `type`, same `mask` when both have one". The import account key
  is `profile:last4:block` (`statement_import.external_account_key`), so a reissued card or a
  profile/layout change already splits one real account into **two** imported accounts. The
  `mask` filter then hides the right one, or offers one of two twins. Adoption is one-to-one, so
  the other twin stays live next to the connector account and is counted twice.
- The owner picks with nothing but a name and a mask. Nothing shows whether the histories agree.
- A wrong pick is permanent. The trigger forbids `source` going back, §5 puts un-adopt out of
  scope, and disconnecting leaves the Plaid rows inside the wrong account. The only recovery is a
  new Item, which costs a slot.
- A sandbox Item can adopt a real imported account (see B5).

**Change.**
1. **Preview before confirm.** `POST /connections/{id}/accounts` takes `dry_run: true`. It
   fetches the first page or pages held in memory (the Item exists, so nothing is spent) and
   returns, per proposed mapping, counts only: overlap window, matched, connector-only and
   import-only. The sheet shows "47 of 52 rows match" or "0 of 61 match: wrong account?".
2. Candidates are **ranked, not filtered**: mask match first, then the same institution, then
   the same type. All of the household's writable import accounts are listed. Add an `ignore`
   choice besides adopt or new.
3. Allow **many-to-one** in one shape: extra twins can be marked `closed_at` / `is_hidden` with
   the reason "covered by connection", in the same unit of work.
4. **Un-adopt in P4, not out of scope.** It deletes the connector-sourced rows in the account
   (FK `SET NULL` makes the superseded import rows live again), restores `source = 'import'`,
   clears `bank_connection_id` and `external_account_id`, and returns the connection to
   `pending_setup`. The trigger allows exactly this reverse transition when no connector rows
   remain. It is cheap now and impossible to retrofit after a bad first link.

### B3. §3.3 `supersede`: superseding drops state the ledger already holds (with Phase 4 #59)
**Failure.** The verb carries over only the category and notes. On main and in #59:
- **Transfers.** `pair_transfers` filters `superseded_by_id IS NULL`, so a superseded import leg
  keeps `is_transfer` and its `transfer_group_id`. Its partner (often in an account that isn't
  linked) stays a transfer against a dead row, while the live Plaid winner is a plain outflow
  and is counted as spending.
- **Splits.** The loser's splits stay on the dead row. Metrics use `COALESCE(splits, parent)` on
  live rows, so the owner's splits disappear from reports.
- **Rejections.** ADR 0027 keys `transfer_rejection` by row id (it explicitly rejects keying by
  amount and date), so a pair the owner rejected is re-paired on the winner.
- **Recurring.** ADR 0028 matches stored series to detections by member overlap, and
  `recurring_member` is keyed by `transaction_id`. Once members are superseded, overlap is zero:
  a `dismissed` series comes back as `auto`, and a `confirmed` one is orphaned.
- **Other fields.** `is_tax_relevant`, `tax_tag` and `receipt_document_id` are lost.

**Change.** `supersede` moves the transfer membership (the winner joins the group and the loser
leaves it, in one locked unit), splits (when the amounts are equal; otherwise the amounts differ,
so it doesn't supersede and sends the pair to review), tax fields, the receipt and the notes.
Phase 4 adds one clause to #59, owned by C4c and C5b: rejections and recurring members follow
`superseded_by_id` (rewritten in the same verb, or resolved through the live winner in reads).
The P2a acceptance gains a test per carried field. Land the clause in #59 before C4c and C5b
build, or P2a and C4c each implement half of it.

### B4. §2 D5 and D6: the owner's existing `.env` overrides the new defaults
**Failure.** `.env.example` on main (line 62) ships
`PLAID_PRODUCTS=transactions,auth,balance,investments,liabilities,statements`, and `make keys`
copied it into the owner's `.env`. Compose passes it through (`PLAID_PRODUCTS: ${PLAID_PRODUCTS:-}`).
P4 changing the `config.py` default changes nothing for this owner. #56's `link_token_create`
sends that list as **required** `products` and has no `optional_products` parameter. The result
in production:
- Link hides institutions that lack any listed product (investments, liabilities or statements on
  a credit union card login).
- `auth` is consented, which brings back full account and routing numbers, against D5 and the
  data-minimization rule.

The "test pins `days_requested = 730`" pins the default only. `PLAID_TX_DAYS_REQUESTED` in `.env`
can lower it, and the lower value is permanent for the Item. Sandbox supports every product, so
G1 passes.
**Change.**
- P4 builds `products` and `optional_products` in code from a required `login_kind`
  (`bank_card | retirement_hsa | brokerage`) in the link-token request. It ignores
  `PLAID_PRODUCTS` for Link (the setup probe only) and refuses `auth` and `statements` there.
- `days_requested` is a constant, not a setting. Remove `PLAID_TX_DAYS_REQUESTED`.
- P4 owns `integrations/plaid/client.py` (add it to §6.3) to add `optional_products`.
- The setup check fails closed when the env list contains `auth` or `statements`.
- Owner action: delete the `PLAID_PRODUCTS` line.

### B5. §6.1 G1 and §12 Q6: the sandbox gate runs on the owner's real-data instance
**Failure.** G1 has the owner run `user_good` and `user_transactions_dynamic` "on their instance",
and checks "a statement month that overlaps a sandbox link shows once". That means adopting a
**real** imported account into a **sandbox** Item:
- ADR 0033's trigger makes the switch permanent.
- Fake rows land beside real history.
- If they ever match exactly, real rows are superseded by fake ones.

Also, `PLAID_ENV=production` goes into the shared dev `.env`, the same file agents' `make up` and
e2e stacks read from the main checkout. Q6 ("which machine") is listed as a question, not a
precondition.
**Change.**
- G1's owner smoke runs in a separate compose project (`-p holdfast-sandbox`, its own volume and
  `.env`) or a throwaway household. Never the real one.
- The candidate query requires `bank_connection.env = 'production'`, so a sandbox connection
  never adopts.
- Q6 becomes a precondition for G1. M-live runs only on the dedicated instance, and its `.env` is
  the only place `PLAID_ENV=production` exists.

### B6. §6.1 M-live and G1: acceptance passes without a correct real sync
**Failure.** G1 is recorded fixtures plus sandbox. Every failure in B1, B2 and B4 is invisible
there. M-live evidence is "Owner says it works". Nothing measures duplicates, balances or
idempotence on production data.
**Change.** M-live evidence, recorded as counts only (ADR 0019), per linked account:
1. The latest `sync_run` succeeded with `history_complete`.
2. A second "Sync now" makes zero writes.
3. A fuzzy duplicate audit over the overlap window (same amount, ±3 days, both live) finds zero
   pairs.
4. The connector's current balance equals the last statement's closing balance plus the sum of
   connector rows after `connector_since`, within $0.01. That is hard rule 6 applied to the link.
5. The slot ledger shows exactly one `linked` row and matches the Plaid dashboard count.

Sounding runs the queries on the owner's instance and writes only the numbers. Any non-zero
duplicate count, or a balance outside $0.01, fails the gate.

### B7. §11 and §6.1: the "safe first link" likely doesn't exist, and the slice order depends on Q2
**Failure.** §11 says to link an institution **without** imported statements first. The owner's
imported history covers both of their institutions (9 accounts). The likely non-statement
logins are the 401(k), HSA and brokerage. D5 links those with `products = ["investments"]`, so
`/transactions/sync` and its balance block (P5) return nothing, and they show nothing until P6.
So either M-live needs P6, or the first link is a statement-backed bank, and adoption (B1–B3) is
on the critical path with no "practice" link.
**Change.** Ask Q2 **before** fixing the slice order, and pick one of two paths. If an
investment login goes first, move P6 holdings and balances (not liabilities) ahead of M-live and
defer P2b supersession, adoption and the `statement_key` migration to an M-live-2. If a bank
goes first, B1–B3 are hard gates for P4. Don't plan both.

### B8. ADR 0034 and the backup story: the irreversible assets have no durability requirement
**Failure.**
- Every slot-relevant fact lives only in Postgres: `plaid_link_slot` and the access tokens in
  `bank_connection.access_token_enc`, sealed under `FERNET_KEY`. Update-mode relink needs the
  access token.
- Restoring a backup older than a link loses that Item's token, and the Item is orphaned for
  good. Every recovery is a new Item and a new slot, and the ledger under-counts by the same
  number.
- A fresh install with the same client starts at 0.
- `make keys --force` makes a new `FERNET_KEY`, and the old tokens can no longer be opened.
- In `self_host`, every household reads the **same** env keys through `get_secret`, but the
  ledger counts per household under RLS, so two households can each count to 10 on one client.

**Change.**
- An owner precondition for M-live: a tested backup of the DB **and** `FERNET_KEY`, kept off the
  machine, taken again right after each production link.
- A `slot_floor` the owner types from the Plaid dashboard during setup.
  `count = max(ledger, floor)`.
- The guard counts by `client_fp` across households through a `SECURITY DEFINER` function that
  returns a count only. Or production linking is refused outside the first household while the
  keys come from env.
- The setup check warns when `linked` rows exist whose `bank_connection` is missing or cannot be
  opened.

---

## Should-fix

**S1. §6.1: gold-plating before first value.**
- K1 (in-app keys) is a G1 dependency, yet §12 Q4 says M-live can use the `.env` keys. Move K1
  and the P9b key-entry form after M-live.
- P9b also bundles the connection list polish. M-live needs only the Link button, the slot
  counter with the phrase, the mapping sheet and "Sync now".
- Smallest safe path for a bank first: L1–L3 → P1 → P2a → P2b → P4a → P4 → P5 → P9b-min → G1.
  That is 10 slices, not 13. With an investment login first (B7), P2b and the `statement_key`
  half of P4a leave the path too.

**S2. §4 and §3.3: serialization will stall.** There is one migration token for 0010 (#31), P4a
and Phase 4's M1–M4. "May land between them" sets no priority. C3b holds both the token and
`ledger_service.py` and `models/transactions.py`, so P2a and P4a both queue behind C3b's review.
`statement_import.py` is edited by P2b, P4a and C3e (and C4d edits `imports.py`). One Ledger
agent owns P2a, C3b, C4c and C5b. **Change:** Keel fixes the order now: #31 → P4a → M1… and
P2a before C3b (or the reverse), and names it in both contracts. P2b's one `statement_import`
call lands in P4a's PR, to cut one edit.

**S3. §0 and I2b: CSV/OFX import will key accounts the old way.** I2b is in progress and not in
P4a's scope. If it resolves accounts by `external_account_id` + `source = 'import'`, it breaks
after P4a or makes twins of adopted accounts. **Change:** I2b uses `statement_key` through one
shared resolver extracted in P4a, and its brief says so.

**S4. ADR 0034 §4: the slot state machine trusts the browser and locks out the owner.**
- `/exit` marks `abandoned` (not counted) on the client's word.
- A crashed tab sends no `onExit`, and the partial unique index then blocks any new link for up
  to 4 hours.
- A lost `onSuccess` becomes `unknown` and a spent slot, although the Item is recoverable.
- The exchange has no retry rule. The `public_token` lives 30 minutes.

**Change.** Resolve every reservation through `/link/token/get` (its `link_sessions` results
carry `item_add_results`; **VERIFY** on Trial). Use it to recover a lost `public_token` and
exchange it, to confirm "no Item" before `abandoned`, and to let a stuck `reserved` row be
resolved at once. Make the exchange idempotent on `reservation_id` so the browser can retry.
Commit the sealed access token and `linked` before calling `/accounts/get`. Before issuing a
new-Item token, list existing connections and require "this is not one of these", because a
duplicate login is the most likely wasted slot. `TRIAL_CONNECTION_LIMIT` is an unverified code
name, and it surfaces in Link's `onExit`, not at the exchange: **VERIFY**.

**S5. ADR 0032: the conflict and change rules have holes.**
- "One source per key" is per **name**, so `plaid_client_id` from env and `plaid_secret_production`
  from a row combine silently. Treat the trio as one unit: all from env or all from rows.
- §3's client-change confirmation runs only on `PUT`. Editing `.env` bypasses it. Compare
  `client_fp` at use time, in the sync job, and mark `client_changed` there.
- On `conflict`, every sync fails. The job must set a household-level `keys_conflict` that the
  Connections page shows as a banner, rather than N connection errors.
- Deleting env keys takes effect only after `docker compose up -d --force-recreate api worker`.
  Until then `PUT` answers `409 managed_in_env`. Put that in the copy and the owner steps.

**S6. ADR 0033 §1 and §3.4 step 1: accounts that appear later and Item replacement.**
- The rule for a new account on an already-mapped Item is unclear ("created only when the
  connection has a confirmed mapping"). Route every unseen `external_account_id` to
  `pending_mapping`, never auto-create.
- When an Item has to be replaced by a new one (not update mode), the new Plaid rows duplicate
  the old Plaid rows. `supersede` requires an `import` loser, which contradicts P8's "relink
  duplicate cleanup through `supersede`". Define plaid-over-plaid supersession, or forbid
  adopting an account that still holds live rows from another connection.

**S7. P2b step 5: rules depend on merge order.** "When C3e has merged, rules run inline first"
means whoever lands second must add it, and no one is assigned. Assign it to the later of P2b and
C3e, by name, with a test.

**S8. P5 and D9: recorded sandbox fixtures need keys no agent has.** Recording happens in wave 4.
Q5 asks about keys only for G1. **Change:** move Q5 into P5's dependencies. Either the owner
runs a provided `scripts/record_sandbox.py` against sandbox and commits the sanitized output, or
sandbox keys go into a CI secret before P5 starts.

**S9. ADR 0030 SimpleFIN: low value for this owner.**
- The owner has two bank institutions plus about three investment logins. That is roughly 5 of
  10 slots, so SimpleFIN saves slots the owner doesn't need to save.
- What it delivers is posted-only, daily, through two third parties (with the MX incident),
  without categories (no PFC, so everything is uncategorized), without holdings, and with
  history that depends on the institution (unverified beyond 90 days).
- The backfill stops at the first **empty** 90-day window, so a quiet savings account loses
  older history. Use the oldest-transaction date or 8 windows, not emptiness.
- One `bank_connection` per access URL spans up to 25 banks, so one bank's `con.auth` marks every
  bank `needs_relink`. Key the status per `conn_id`.

**Change:** keep S1 after P6, P7 and M1. Add to Q3: "only if your slot plan exceeds 10 logins".
M1 manual accounts cover low-activity accounts for free.

**S10. §6.1 estimate: "about 3 windows" doesn't hold.**
- 13 slices, most with two SHA-bound reviewers, is at least 24 review runs before re-reviews.
- #56's head has moved twice since its APPROVE (`88dda90` → `cba7f7b` → `cb97f73`), and the
  contract already names the wrong SHA.
- #32 is 24 commits behind main and conflicts with #31 on `backend/app/worker/main.py`.
- #31 is 1,671 lines on protected paths with no review.

**Change:** re-estimate at 6–8 windows, and stop merging main into open PR branches until the
gate runs (rebase once, review once).

**S11. §12: owner actions not listed.**
1. Back up the DB and `FERNET_KEY` before and after each production link (B8).
2. Delete `PLAID_PRODUCTS` and `PLAID_TX_DAYS_REQUESTED` from `.env`, then recreate the
   containers (B4, S5).
3. Check in Plaid's dashboard that the chosen institution supports Transactions, and whether it
   is OAuth. OAuth institutions may need the application and company profile completed and
   per-bank registration before production Link works (**VERIFY**). Sandbox `user_good` is
   non-OAuth, so G1 can't show this.
4. Read the current production Item count from the dashboard (B8 floor).
5. Enrol a passkey or TOTP on the **dedicated** instance, since step-up needs it.
6. Decide the owner and visibility for each mapped account at the mapping step.
7. Provide sandbox recording keys before P5 (S8).
