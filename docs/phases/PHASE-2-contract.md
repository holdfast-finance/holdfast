# Phase 2: connector-lane contract (every source into the owner's own instance)

- **Revision 3 (2026-11-21).** It answers Bulwark's BLOCK (five must-fixes) and Gadfly's round 2
  (REWORK-small, seven blockers) on PR #62 at `91f6b72`, and applies Keel's rulings.
  - Revision 2 answered Gadfly round 1 (`77aafbe`).
  - The review history is in `docs/phases/reviews/PHASE-2-contract-gadfly.md`, and §14 answers
    every item.
- **Spec:** PLAN 3, 7, 13 (Phase 2 and 3) and 20 (Section 20 wins). ROADMAP E2 (P1–P10), S1, E3
  and 10a.
- **ADRs:** 0007, 0008, 0011, 0015, 0016, 0017, 0019, 0021, 0022 and 0023, plus this lane's:
  - **0030** SimpleFIN (Proposed)
  - **0032** in-app key entry (Proposed)
  - **0033** adoption after a dry-run preview
  - **0034** per-client slot ledger
  - **0035** coverage intervals and order-preserving matching
- **Numbering:** 0031 is on main. 0024 and 0026–0029 belong to PR #59. 0025 stays with the Plaid
  lane.

This file is binding for every connector-lane story. A builder who must deviate says so in the PR
body and asks Lattice for an amendment, which lands here first.

**This file owns the single definition of the supersede verb (§3.3).** The Phase 4 contract cites
it. The changes Phase 4 must make are listed in §3.3.1.

**Goal (owner, 2026-11-21):** "hook up every connector with our data" and view it on the phone.

**Three milestones:**
- **M-link:** the owner creates the first production Item on the dedicated instance and holds it
  in `pending_setup`. The token is sealed and the slot recorded. Nothing is ingested, and Plaid
  keeps the history.
- **M-preview:** the owner runs the adoption preview read-only on that held Item, before any ledger
  write exists.
- **M-live:** the owner decides the mapping, the first sync runs, and measured counts (§6.4) show
  zero duplicates and agreeing balances.

All three are reached on the PC. The phone is the ADR 0021 lane (§11). PLAN 13 Phase 2 acceptance
is P10.

---

## 0. What exists (`origin/main` at `6fea745`, and every branch)

| Piece | State | Where |
|---|---|---|
| F2 jobs | merged | ADR 0016 |
| F2b job hardening (`enqueue(requested_by=)`) | **PR #32**: APPROVE-WITH-NOTES at `82cff55`, including the `FOR KEY SHARE` fix; about to land | `fix/F2b-job-hardening` |
| F5a `SecretToken` | merged | — |
| F5c resolver, `hf1`, rotation (`rotation.TARGETS`), `resolve_outbound`, `0010_list_household_ids` | **merged** (#31, `afdc55b`). #63 (P3b, the client's low findings) also merged | `feat/F5c-secret-resolver-rotation` |
| `hf2` (ciphertext bound to its row, ADR 0015 §6) | **not started** (F5d) | — |
| F6 runs and `superseded_by_id` | merged | ADR 0017 |
| Household-wide context guard | merged (#61) | — |
| Connector and investment schema | merged, schema only | — |
| Statement PDF import, wizard, review queue, Transactions | merged. Writes no `balance_snapshot` | — |
| PDF profiles | 6 layouts. The card layout keeps the first of two printed dates; interest takes the period end | `app/importers/profiles/` |
| CSV/OFX parsers | merged. I2b in progress | — |
| `LedgerWriter` | merged. `_require_live` refuses edits to superseded rows | C1 |
| P3 Plaid client and setup checklist | **merged** (#56). No `optional_products`. The setup check reads `PLAID_PRODUCTS` and `PLAID_TX_DAYS_REQUESTED` | ADR 0023 |
| P9a page | local branch, `d973679` | — |
| P1, P2, supersession, the matcher | **do not exist** | — |
| Env and compose | `.env.example`: `PLAID_ENV=sandbox`, `PLAID_PRODUCTS=transactions,auth,balance,investments,liabilities,statements`, `PLAID_TX_DAYS_REQUESTED=730`, all passed through by compose. Ports fixed; no second instance | — |
| Backups | `scripts/backup.sh` exits 1 (Phase 9) | — |
| Crypto Track | local branch. ADR 0020 and migration 0007 collide | `feat/E10a-crypto-track` |
| Phone access | ADR 0021 Proposed | — |

**P1 and P2 come before the first live sync.** Without them, dedupe, pending-to-posted and
`removed` would live inside each connector, and overlap months would show twice. M-link needs
neither: it ingests nothing.

---

## 1. Every source the product plans

Cost markers: **$** costs the owner money. **!** has an irreversible external effect.

| Source | State | Depends on | Owner must supply |
|---|---|---|---|
| **Plaid Transactions** | client merged. Ingest not started | §6.1 | Trial approved. Production keys in the dedicated instance's env file. The slot plan and slot count. **!** one of 10 lifetime slots per Item. Bank sign-in |
| **Plaid Balances** | P5b (sync response), P6 (daily) | P5b | — |
| **Plaid Investments** | P6 | P5b, or before M-live on path B (§6.3) | the 401(k) login, the HSA custodian. **!** one slot each |
| **Plaid Liabilities** | P6 (via `optional_products` at link time) | P5b | — |
| **SimpleFIN** | ADR 0030 Proposed | M-live, P6, P7, M1, F5d | **$** $15/year. Only if the plan exceeds 10 logins or Plaid lacks an institution |
| **Statement PDF** | merged | — | PDFs from the private directory |
| **CSV/OFX/QFX** | I2b in progress | I2b on P4a's resolver | downloads |
| **Competitor exports** | I3 | I2b | exports |
| **Credit-report PDF** | Phase 6 | — | three free reports |
| **Crypto watch-only** | local branch (X1) | X1, Cipher, Bulwark, `hf2` | public xpubs or descriptors, addresses, own node. **Never a seed or key** |
| **Crypto exchange key** | X2 | X1 | read-only keys |
| **Manual accounts** | M1 | — | balances |
| **Tax documents** | Phase 7 | — | forms |

---

## 2. Decisions at a glance

| # | Question | Decision | Record |
|---|---|---|---|
| D1 | P1 and P2 before the first sync | Yes. M-link needs neither | §0 |
| D2 | Key entry | One source per key group, both = conflict. **After M-live** (K1) | ADR 0032 |
| D3 | Adoption | Read-only preview (person-scoped) with evidence and rows for the owner. Explicit adopt, new or ignore. Requires complete history and a reconciled statement. Un-adopt restores the history | ADR 0033 |
| D4 | Slot guard | Per-client ledger. The slot phrase plus the owner's slot count on every production link. Link token sealed until resolved | ADR 0034 |
| D5 | Products | Built from `login_kind` (§5.1). Only `transactions`, `liabilities` and `investments` are allowed. Every other product is **refused** | §5.1 |
| D6 | History depth | 730 days, a constant. Any other `PLAID_TX_DAYS_REQUESTED` is refused | §5.1 |
| D7 | Overrides | **None.** The override phrase path is deleted. The only typed confirmation is the slot phrase | Keel |
| D8 | Overlap dedupe | Coverage intervals over reconciled runs, then an order-preserving two-pointer per `(account, amount)` with a ±3-day window, then held pairs for the owner | ADR 0035 |
| D9 | Supersede | One verb pair, defined here (§3.3) and cited by Phase 4 | §3.3 |
| D10 | Sync shape | One full pagination run, applied in one transaction with `tx_cursor` and `sync_run` | ADR 0017 |
| D11 | Initial history | Re-enqueue until `HISTORICAL_UPDATE_COMPLETE`. Matching is deferred until then. **VERIFY** with R1 | ADR 0035 §4 |
| D12 | Sandbox | Only in `holdfast-sandbox`. Sandbox and real data never share a household (ADR 0033 §7). `PLAID_ENV`'s `sandbox` default is unchanged | ADR 0033 |
| D13 | Real data | `holdfast-instance`: its own volumes on 0700 bind mounts outside the repo and its own env file. The only place `PLAID_ENV=production` exists | H2 |
| D14 | Envelope | Every new connector credential is sealed `hf2`, bound to its row (F5d before P4-link) | ADR 0015 §6 |
| D15 | Phone Link | Not for these milestones. Desktop only | ADR 0023 |
| D16 | Who acts | The owner role with step-up, for linking, adoption, un-adopt, claims and keys | security rule |

---

## 3. Interfaces

### 3.1 Domain value types (P1): `app/domain/connector.py`, pure
```python
@dataclass(frozen=True)
class ConnectorAccount:
    external_account_id: str
    name: str
    official_name: str | None
    mask: str | None
    type: AccountType
    subtype: str | None
    currency: str | None
    institution_name: str | None

@dataclass(frozen=True)
class ConnectorBalance:
    external_account_id: str
    as_of: date
    current: Decimal | None
    available: Decimal | None
    limit: Decimal | None

@dataclass(frozen=True)
class ConnectorTxn:
    external_account_id: str
    external_id: str
    posted_date: date
    authorized_date: date | None
    amount: Decimal                  # outflow negative
    description_raw: str
    merchant_name: str | None
    pfc_primary: str | None
    pfc_detailed: str | None
    is_pending: bool
    pending_external_id: str | None

@dataclass(frozen=True)
class SyncBatch:
    accounts: tuple[ConnectorAccount, ...]
    balances: tuple[ConnectorBalance, ...]
    added: tuple[ConnectorTxn, ...]
    modified: tuple[ConnectorTxn, ...]
    removed: tuple[tuple[str, str], ...]
    next_cursor: str | None
    history_complete: bool
```
Constructors refuse `float`, amounts with more than 2 decimal places, and a pending row that
carries `pending_external_id`.

### 3.2 Protocol and registry (P1): `integrations/{base,registry,fake}.py`
```python
class BankConnector(Protocol):
    provider: ClassVar[str]
    async def begin_link(self, ctx: LinkContext) -> LinkStart
    async def complete_link(self, ctx: LinkContext, proof: SecretToken) -> LinkedConnection
    async def begin_relink(self, ctx: LinkContext, conn: ConnectionRef) -> LinkStart
    async def fetch(self, conn: ConnectionRef, credential: SecretToken,
                    cursor: str | None) -> SyncBatch
    def health(self, state: ConnectionState, now: datetime) -> ConnectorHealth
```
- `ConnectorError(kind, code, request_id)` carries no provider message text.
- `registry.override` is for tests only.
- `FakeConnector` replays scripted batches and errors.

**Session rule (Bulwark 3).** Decisions, `close` and un-adopt run in the request's person-scoped
session, never in a job and never household-wide, so the account write policy decides. An account
the requester can't write gives `404`.

### 3.3 Ledger writer verbs (P2a): `services/ledger_service.py`
```python
async def upsert_external(self, new: NewTransaction) -> UpsertOutcome
async def remove_external(self, account_id: UUID, external_id: str) -> bool
async def post_pending(self, pending_external_id: str, posted: NewTransaction) -> UUID
async def supersede_transaction(self, old_id: UUID, new_id: UUID) -> None
async def unsupersede_transaction(self, old_id: UUID) -> None
```

**`upsert_external`**
- Matches on `(account_id, external_id)`.
- It never replaces a `user` or `rule` category.
- A changed amount clears splits and reports it.

**`post_pending`**
- Inserts the posted row and deletes the pending row in one unit.
- Carries the category (by rank), the notes and the splits (when the amount is unchanged).

**`remove_external`** calls `unsupersede_transaction` first when the row is a winner, then deletes
it.

**The supersede verb: the single definition.** `supersede_transaction` and
`unsupersede_transaction` are the only writers of `superseded_by_id` (single-writer lint). Callers:
`IngestService` and statement import through the ADR 0035 matcher, `remove_external`, and
un-adopt. No API route calls them.

*Preconditions of `supersede_transaction(old, new)`.* If any fails, nothing is written.
1. Same account.
2. `old.source = 'import'`. `new.source IN ('plaid', 'simplefin')`, and its connection has
   `env = 'production'`.
3. Both are live and not pending, and neither is already in a supersession.
4. `old.amount = new.amount` exactly. Otherwise `LedgerRuleError('supersede_amount_mismatch')`.
5. If `new` is a transfer leg, it is in `old`'s group. Otherwise `SupersedeConflict`, which is
   counted, and both stay live.

*Lock.* Lock `old`, `new` and every other leg of their groups, in id order.

*Writer effects.* In this order, all while `old` is live:
1. **Category by rank** (user > rule > plaid > ml). `new` takes `category_id` and
   `category_source` when `old`'s source outranks its own. C3b adds `category_rule_id` to this
   move.
2. **Splits.**
   - `old` has splits and `new` has none: the split rows move to `new`.
   - Both have splits: each keeps its own. Counted as `splits_kept_on_old`.
3. **Transfer state.** No rejection row is written.
   - `old` paired in `G` and `new` in no transfer: `old` leaves, **then** `new` joins `G`.
   - `old` single-marked: `old` is cleared, then `new` is marked.
   - `new` already in `G`: nothing changes.
4. **Tax.** `new.is_tax_relevant = new OR old`. `tax_tag` keeps `new`'s value when it has one;
   otherwise it takes `old`'s.
5. **Receipt.** `receipt_document_id` keeps `new`'s value when it has one; otherwise it takes
   `old`'s.
6. **Notes.** `new`'s notes when non-empty, otherwise `old`'s.
7. **Mark.** `old.superseded_by_id = new`. `old` keeps its fingerprint and run id.

*Trigger effects* (Phase 4 M3/M4, `transaction_supersede_repoint`, household-wide):
- **NULL → value:** move `transfer_rejection`, `transfer_link` and `recurring_member` references
  from `old` to `new` (`INSERT … ON CONFLICT DO NOTHING`, then `DELETE`), and delete
  `transfer_suggestion` and `transfer_leg_offer` rows (Phase 4 tables) that reference `old`.
- **value → NULL:** move the same references from `OLD.superseded_by_id` back onto the row's id,
  and delete `transfer_suggestion` and `transfer_leg_offer` rows that reference either id.

*`unsupersede_transaction(old)`.* `old` must be superseded by a live `W`. Lock `old`, `W` and `W`'s
group legs, in id order. Then:
1. Set `old.superseded_by_id = NULL`. The trigger moves the references back.
2. Splits move from `W` to `old` when `old` has none.
3. Transfer state: `W` leaves, then `old` joins `W`'s group, or takes `W`'s single mark.
4. Tax, receipt and notes are copied to `old` where `old`'s value is empty.
5. The category moves to `old` when `W`'s source outranks `old`'s.

After that, `W` has no transfer state, so Phase 4's `transaction_dissolve_orphan` fires only for a
winner-only leg. Its `42501` makes un-adopt return `409 unadopt_blocked`.

*Audit.* None per row. The counts go on `sync_run.rows_superseded`.

#### 3.3.1 What the Phase 4 contract must change
Keel hands this list to the PR #59 run. It is also in
`~/.local/state/holdfast-keel/supersede-clause.md`.
1. **§4.4 "Supersession".** Cite this section. Delete:
   - "the new row must be … not a transfer, no splits, source not `user`"
   - user-category-only
   - the unequal-amount dissolve branch
   - `LedgerRuleError("split row changed amount")`
   - the `ledger.superseded` per-row audit
2. **§4.5.** Remove `supersede_transaction` from C4c's writer work. P2a owns both verbs. The
   single-writer lint entry cites P2a.
3. **§3.3, `transaction_supersede_repoint`.** Add the value → NULL branch. The trigger fires on any
   change of `superseded_by_id`. The downgrade drops both branches.
4. **§3.4, the M4 re-point.** `recurring_member` moves in both branches. The downgrade restores
   M3's body, both branches.
5. **D15 row:** "Superseding (PHASE-2 §3.3) moves category by rank, splits, transfer state, tax,
   receipt and notes in `LedgerWriter`. The trigger re-points rejections, links and recurring
   members in both directions."
6. **The "Supersede a row" table row:** the writer is P2a's `supersede_transaction`, called by the
   ADR 0035 matcher (P2m) through P2b.
7. **§7.3 acceptance.**
   - C4c adds "supersede then unsupersede: rejections and links back on the old row".
   - C5b adds "unsupersede: members back on the old row; series id and state unchanged".
   - **C3b** adds the `category_rule_id` move to both verbs, with a test.
8. **`transaction_dissolve_orphan`.** Add one sentence: `unsupersede_transaction` clears a
   winner's transfer state before `remove_external` deletes it.
9. **Shared orders (§4 here)** replace #59's order paragraph.
10. **C3e** adds inline rules to `IngestService`, with a test.
11. **The review-response rows for Gadfly B2 and B3 and Bulwark 10.** Cite this section for the
    writer half.

### 3.4 IngestService (P2b): `services/ingest.py`
`apply(connection_id, batch, sync_run_id) -> IngestCounts`, in the job's household-wide session,
one transaction:
1. **Accounts.** Resolve through `connector_account_link`. `mapped` is ingested, `ignored` is
   skipped, an unseen account becomes `pending` and is skipped. A NULL currency is skipped.
2. **Balances.** Upsert per `(account_id, as_of_date)`. Never summed.
3. **Coverage.** Posted rows inside the account's coverage (ADR 0035 §1) are dropped from
   `added` and `modified` and counted as `rows_skipped_covered`.
4. **Removals.** `removed` goes to `remove_external`. Then `modified` and `added` go to
   `upsert_external`, or to `post_pending` for a posted row that replaces a live pending row.
5. **Categories.** Plaid rows get the ADR 0022 mapping. C3e adds inline rules.
6. **Matching.** Unless deferred (ADR 0035 §4), run `match()` per touched group, then
   `supersede_transaction` per non-held pair. Held pairs are counted as `rows_held`.

P2b also adds the same matcher call to statement import after each block insert.

**Properties:** ADR 0035 §5, plus replay-twice = zero writes.

---

## 4. Schema and shared orders

**Fixed migration-token order (shared with Phase 4):**
1. `0010_list_household_ids` (#31)
2. **`<n>_connector_link` (P4a)**
3. M1 `<n>_rules` (C3b)
4. M2 (I1d)
5. M3 (C4c)
6. M4 (C5b)

A builder may write a migration before holding the token, but numbers it and rebases only once
they hold it. **Skip rule:** if the next holder has no open PR with both verdicts one window after
the token frees, the following slice may take it once. Keel notes the skip in SPRINTS.

**Shared file orders:**
- `ledger_service.py`: P2a → C3b → C4c → C5b. P2a depends only on P1's types, so it can merge early.
- `statement_import.py`: P4a (resolver) → P2b (matcher call) → C3e (rules).
- `docker-compose.instance.yml`: H2 → V1.
- `Makefile`, `docker-compose.yml`, `scripts/dev.ps1`: L1 → H2.

**P4a `<n>_connector_link`** depends on L1 only:
1. `plaid_link_slot`, as ADR 0034 §1, including `link_token_enc` and `plaid_slot_count()`.
2. `connector_account_link`, as ADR 0033 §1.
3. `bank_connection`: add `client_fp`. `status` gains `pending_setup`.
4. `account`:
   - add `statement_key` (backfilled, the unique index swapped)
   - add `adopted_at timestamptz NULL`
   - add the adoption trigger (ADR 0033 §6)
5. `account_coverage(household_id, account_id, start_date, end_date, source_import_run_id)`:
   forced RLS: `household_isolation`, plus the restrictive person policies of the account detail
   tables. Read requires that the reader can see the account's details. Insert, update and delete
   require that the reader can write the account, or a household-wide session. So a person can
   neither read another person's coverage nor add coverage that hides their connector rows
   (ADR 0035 §1).
6. `sync_run`: add `rows_superseded`, `rows_skipped_covered`, `rows_held` and `history_complete`.
7. `job_run.requested_by uuid NULL` (FK to `person`, `ON DELETE SET NULL`), written by `enqueue`
   and exposed as `JobContext.requested_by` (P4 edits `app/jobs/registry.py` and `runner.py` for
   this).
8. In the same PR: `app/services/statement_accounts.py` (the one resolver) and the
   `statement_import._account()` switch.

---

## 5. API, Plaid parameters, audit

**Session rule (Bulwark 3).** Decisions, `close` and un-adopt run in the request's person-scoped
session, never in a job and never household-wide, so the account write policy decides. An account
the requester can't write gives `404`.

### 5.1 Link parameters (P4-link)
`login_kind` is required.

| `login_kind` | `products` | `optional_products` | `days_requested` |
|---|---|---|---|
| `bank_card` | `transactions` | `liabilities` | 730 |
| `retirement_hsa` | `investments` | — | — |
| `brokerage` | `investments` | — | — |

Link products are an **allowlist**: `transactions`, `liabilities` and `investments`. Any other
product in `PLAID_PRODUCTS`, including `auth`, `identity`, `statements`, `assets`,
`income_verification`, `transfer` and `payment_initiation`, is refused for Link with
`409 product_not_allowed`, and no phrase overrides that. `auth` would consent full account and
routing numbers, and `identity` the holder's personal details (data minimization). The setup check
reports `products: warn` for any product outside the allowlist.

Further rules:
- The table's products are intersected with `PLAID_PRODUCTS`, which says what the plan has.
- `PLAID_TX_DAYS_REQUESTED` other than 730 is refused (`409 days_not_allowed`).
- P4-link changes `.env.example` and `config.py` to `PLAID_PRODUCTS=transactions,liabilities,investments`,
  so a fresh install isn't refused.
- `balance` is never sent (ADR 0023). **VERIFY** whether Plaid accepts it in `products` at all.
- P4-link adds `optional_products` to `PlaidClient.link_token_create`. **VERIFY** it on Trial. If
  it is refused, `bank_card` sends `transactions` only, and P6 adds liabilities by re-consent
  without a new Item.
- No `webhook` is sent. The setup check warns when `PLAID_WEBHOOK_URL` is set.

### 5.2 Endpoints
| Method and path | Who | Body and response | Slice |
|---|---|---|---|
| GET `/plaid/setup` · POST `/plaid/setup/check` | owner | ADR 0023 checklist. `item_slots` from `plaid_slot_count`. Adds `products: warn`, `tokens_unreadable` and `ledger_orphans` | merged, P4-link |
| POST `/connections/plaid/link-plan` | owner | `{login_kind, mode, connection_id?}` → `{products, optional_products, days_requested, ledger_count, count_source, existing_connections, phrase}` | P4-link |
| POST `/connections/plaid/link-token` | owner + step-up | `{login_kind, mode, connection_id?, owner_count, not_a_duplicate, confirm_phrase}` → `{link_token, expiration, reservation_id}`. `409 confirm_required\|no_slots_left\|product_not_allowed\|days_not_allowed\|sandbox_on_real_data\|sandbox_connection_present` | P4-link |
| POST `/connections/plaid/exchange` | owner (the reservation's creator) | `{public_token, reservation_id}` → `{connection}` (`pending_setup`; `connector_account_link` rows `pending`). Idempotent | P4-link |
| POST `/connections/plaid/reservations/{id}/resolve` · `/abandon` | owner (abandon: step-up + phrase) | → slot state | P4-link |
| POST `/connections/{id}/preview` · GET | owner | → `202 {job_run_id}` · → ADR 0033 §2 evidence and owner-only rows | P4-preview |
| POST `/connections/{id}/accounts` | owner + step-up, person-scoped | `{decisions: [...], close?: [...], confirm_held?}` → `active`, initial sync enqueued | P4-map |
| GET · POST `/connections/{id}/accounts/{ext}/unadopt` | owner + step-up, person-scoped | GET → `{rows_removed, user_edited_rows_lost}`. POST `{confirm_lost}` → 204 | P4-map |
| GET `/connections` | member, RLS | connections and accounts. When every account of a connection is hidden from the reader, it shows no names or masks | P4-link |
| POST `/connections/{id}/sync` | owner | → `202` | P5b |
| `/settings/secrets` | owner | ADR 0032. Status only | K1 |
| POST `/connections/simplefin/claim` | owner + step-up | ADR 0030 | S1 |
| GET `/sync-health` | member | — | P8 |

These never appear in a response, a log, an audit row, a job payload or a Redis key: tokens, access
URLs, the plaintext client id, and Plaid response bodies. The one exception is the owner-only
preview rows, which are sealed `hf2` in Redis (ADR 0033 §2). A canary test covers each.

### 5.3 Audit
Each call writes one `audit_log` row holding the action and ids only. It never holds tokens, the
client id, URLs, or the provider's message text. Actions:
- **Plaid:**
  - `plaid.link_token` (reservation_id, env, mode, slot n)
  - `plaid.exchange` (reservation_id, connection_id, outcome)
  - `plaid.link_token_get` (reservation_id, resolved state)
  - `plaid.reservation_abandon`
  - `plaid.accounts_get`
  - `plaid.sync` (sync_run_id, pages, outcome, Plaid `request_id`)
  - `plaid.balance_get`
  - `plaid.item_remove`
- **Connections:**
  - `connection.preview`
  - `connection.decide` (one per adopt, new, ignore or close, with account ids)
  - `connection.unadopt`
  - `connection.client_changed`
- **SimpleFIN:** `simplefin.connect`, `simplefin.fetch`.
- **Settings:** `settings.secret_set`, `settings.secret_clear`.

Scheduled runs are written by the worker with `user_id` NULL. A canary test checks that no audit
row holds a token or URL.

---

## 6. Slices (one PR each)

Bulwark gates auth, tokens, Plaid, `app/security/`, compose, `.claude/` and migrations. Ledger
gates money paths, Prism gates `web/`, and Sounding gates acceptance. **Process:** rebase once,
review once. No more merging `main` into open PRs.

### 6.1 Path A (default) to M-link, M-preview and M-live

| # | ID | Scope | Owner | Reviewers | Token | Depends | Acceptance evidence |
|---|---|---|---|---|---|---|---|
| 1 | **L1** | **Done:** #31 merged as `afdc55b`, ADR 0015 paragraph fixed. Land #31. Rebase once. Fix ADR 0015's stale `list_household_ids` paragraph | Harbor | **Bulwark** (full), Ledger | yes (0010) | — | SHA-bound verdicts. ADR 0015 tests. Up/down/up |
| 2 | **P1** | §3.1, §3.2 | Tide | Ledger, Bulwark | no | — | Contract suite. No `float` |
| 3 | **F5d** | `hf2` envelope (ADR 0015 §6). `seal(value, ctx)` and `open(blob, ctx)` take `ctx = household_id\|table\|column\|<row key or setting name>`, authenticated as associated data under a key derived from the primary ring key. `get_secret` passes `(household_id, 'household_setting', 'value_enc', name)` and fails closed on a mismatch. The rotator opens and re-seals every row with its own context, refuses a mismatch, and upgrades `hf1` rows in the two gated columns to `hf2` | Harbor | **Bulwark** (full, protected paths) | no | L1 | For each gated column, a value copied to another household, row, column or setting name fails to open. Legacy and `hf1` values still open. The rotator tests pass with `hf2` |
| 4 | **L2** | #32 has APPROVE-WITH-NOTES at `82cff55`, including the `FOR KEY SHARE` fix; landing next. Land #32. Rebase once after L1, resolving `worker/main.py` | Lattice | **Bulwark** | no | L1 | Verdict at head. Confused-deputy and timeout tests. And the fix for Bulwark's #32 BLOCK: `_check_references` locks each `Ref` row `FOR KEY SHARE`, so Postgres applies the write policy. A test shows a person can't enqueue a job on another person's shared or summary_only account. |
| 5 | **P4a** | §4 migration and resolver | Tide | **Bulwark** (RLS), Ledger | **yes, right after 0010** | L1 | RLS lint. No DELETE on `plaid_link_slot`. Two households, one client, one `reserved`. Trigger matrix including a sandbox connection refused. Backfill equality. Re-import after adoption lands on the same account. Up/down/up. A person can't read or insert coverage on another person's private or shared account. |
| 6 | **H2** | `holdfast-instance` (real: bind mounts in a 0700 directory outside the repo, its own env file, no published DB or Redis port) and `holdfast-sandbox` (throwaway: fresh synthetic keys, `PLAID_ENV` forced to sandbox, another loopback port). `make` targets for both. A `.claude/hooks` guard that blocks `docker … prune` with volumes and `docker volume rm`. The prune hook binds only Claude sessions, so it also blocks `rm` under the instance directory, and `instance-up` refuses to start unless that directory is mode 0700. The real-data instance's container socket and the session bus are on the hide list in `scripts/fc-hides.sh` (PR #64), and no Codex or Grok worker runs without it | Harbor | **Bulwark** (compose, `.claude/`) | no | L1 | `check-compose-ports.py` extended. The sandbox api refuses `PLAID_ENV=production`. **`make down -v` in the main checkout leaves `holdfast-instance` intact.** The hook blocks the prune commands |
| 7 | **P4-link** | Link plan, guard, idempotent exchange, `/link/token/get` resolve with a sealed `link_token_enc`, abandon, §5.1, `client_fp` check at use time, the coexistence rule (ADR 0033 §7) for links **and** for statement and CSV uploads (one check in `ImportService`; M1 adds it for manual accounts), `GET /connections`, the setup-check additions. Adds `plaid_link_slot.link_token_enc` to `rotation.TARGETS` (context `household_id\|plaid_link_slot\|link_token_enc\|id`, NULL skipped) and to ADR 0015 §2's column list. | Tide | **Bulwark**, Ledger | no | **F5d**, L2, P1, P4a | Guard at 7/8/9/10 and with an owner count above the ledger. An off-allowlist product gives `409`. Exchange retried after a crash makes no second connection. A second `public_token` is counted and flagged. `onExit` alone never abandons. Sandbox and real data refused both ways. Token and `link_token_enc` canaries. `link_token_enc` NULL after resolution. The rotator re-seals a live `link_token_enc`, and the residual scan counts it. A sandbox token minted on an empty household is refused at exchange once an import exists. |
| 8 | **P9b-link** | From P9a `d973679`: checklist, link-plan dialog (products, days, owner count with its named source, existing connections, slot phrase), Plaid Link button, reservation resolve, minimal connection list | Prism | Prism, Sounding, **Bulwark** | no | P4-link (mocks first) | 375 px. vitest: the phrase is required and the 11th is blocked |
| 9 | **H1** | Runbook `docs/ops/backup.md`: `pg_dump` and document-store commands, key handling (ADR 0034 §5), the owner-only real-restore option. **One verified restore of a synthetic dump with a synthetic key in `holdfast-sandbox`** | Harbor | Bulwark | no | H2 | The restored synthetic instance opens a sealed canary with its key and refuses with another key. Evidence in the PR |
| — | **M-link** | **Owner:** backup, enter the slot count, link one institution from the desktop on `holdfast-instance`. The Item is held in `pending_setup` | owner | Keel | — | 1–9, owner actions §12 | Slot ledger: one `linked` row. The owner confirms their count. Nothing ingested |
| 10 | **P2m** | `domain/supersede.py`: `coverage()`, `match()`, `is_held()` (ADR 0035), pure | Tide (Codex-suitable) | **Ledger** | no | P1 | ADR 0035 §5 named cases and properties. Brute-force maximality |
| 11 | **P5a** | Plaid `fetch()`: `/transactions/sync` pagination into `SyncBatch`, `mapping.py` signs, mutation restart (in memory), `history_complete`. Plus **R1** `scripts/record_plaid_sandbox.py` (sandbox only, strips tokens and ids) | Tide | **Ledger**, Bulwark | no | P1, P4-link | Hand-built fixtures. Sign tests (debit, credit, card, loan balance). No Plaid body logged |
| 12 | **P4-preview** | The `connections.preview` job, person-scoped candidates, Redis storage (counts plain, rows sealed `hf2`), `requested_by` in registry and runner, the 50,000-row cap | Tide | **Bulwark**, Ledger | no | P2m, P5a, P4a, L2 | A preview with no `requested_by` fails. Candidates exclude unwritable accounts. GET returns the result only to the requester. No ledger or cursor write |
| 13 | **P9b-map** | Preview view (evidence and owner-only rows), the decision sheet, un-adopt confirm | Prism | Prism, Sounding | no | P4-preview (mocks for decisions) | 375 px. Adopt disabled until history is complete and a reconciled statement exists |
| — | **M-preview** | **Owner** runs the preview on the held Item. Reads only. Sounding records counts (via V1 rules, §6.4) | owner | Keel | — | 10–13 | Counts recorded, owner satisfied or the account is set to `new` |
| 14 | **P2a** | §3.3 verbs including both supersede verbs | Ledger | Bulwark, Ledger (independent run) | no | P1 | A test per carried field and its reverse. Conflict and mismatch leave both live. Single-writer lint |
| 15 | **P2b** | §3.4, plus the matcher call in statement import | Tide | **Ledger**, Bulwark | no | P2a, P2m, P4a | Interleaving properties. **Sanitized trans≠post fixture: zero live duplicates.** Replay twice = zero writes |
| 16 | **P4-map** | Decisions, `close`, un-adopt (ADR 0033 §5), `confirm_held` | Tide | **Bulwark**, Ledger | no | P2a, P2b, P4-preview | Adopt refused without preview, history or a reconciled run. Un-adopt then re-adopt then sync restores the same live multiset and balances. Cursor reset. Snapshots deleted. Unwritable account gives `404` |
| 17 | **P5b** | `plaid.sync_item` job: apply in one transaction, cursor, `sync_run` counts, balance snapshots, deferral until history is complete, `ledger.refresh` enqueue, `client_changed` | Tide | **Ledger**, Bulwark | no | P2b, P5a, P4-map | Kill between pages, then resume: no duplicates. Cursor only on commit. `error_class` only |
| 18 | **V1** | `connector_audit` as a one-off `audit` compose service with DB-only env (the app role, household-wide, read-only transaction, no Plaid keys). Counts only, opaque indexes. It runs `SET TRANSACTION READ ONLY` and publishes no port | Tide | Sounding, **Bulwark** | no | P5b, H2 | Runs on synthetic data. The output schema has no names, masks, merchants or amounts. The container env has no `PLAID_*` |
| 19 | **RPL** | R1 recording replay in `holdfast-sandbox` (Sounding). The owner records once with sandbox keys. **No owner sandbox smoke** | Sounding | Keel | no | P5b, H2 | Replay ingests with zero duplicates. A second sync makes zero writes. `sandbox-destroy` afterwards |
| — | **M-live** | **Owner** decides the mapping and syncs. Sounding runs V1 before, after the first sync, and after a second sync at least 6 hours later | owner, Sounding | Keel | — | 14–19 | §6.4 |

**Estimate (restated):**
- **M-link: about 3–4 windows.** The serial gates are L1 → F5d → P4a → P4-link → P9b-link, and L1
  is a heavy review. P1, L2, H2 and H1 run in parallel.
- **M-preview: plus about 2 windows** (P2m, P5a, P4-preview, P9b-map, partly in parallel).
- **M-live: plus about 2–3 windows** (P2a early, then P2b → P4-map → P5b → V1 → RPL).
- **Total to M-live: about 7–9 windows**, plus the owner's steps. The added slices (F5d, the P4
  split, V1, the preview) are the reason it is above the earlier 6–8.

### 6.2 After M-live
| ID | Scope | Owner | Reviewers | Depends |
|---|---|---|---|---|
| **K1** + **P9c** | ADR 0032 key entry and its form | Harbor, Prism | **Bulwark**, Prism | F5d, M-live |
| **P6** | Investments, liabilities, daily balance | Tide | Ledger, Bulwark | P5b |
| **P7** | Relink, update mode, backoff, cron, `seed_sandbox.py` | Tide | Bulwark | P5b |
| **P8** | Sync Health, item replacement | Tide | Ledger, Bulwark | P5b |
| **P9d** | Sync Health page | Prism | Prism, Sounding | P7, P8 |
| **M1** | Manual accounts | Ledger, Prism | Bulwark, Prism | — |
| **I2b**, **I3**, **I4** | CSV/OFX, competitor exports, wider dedupe | Tide | Ledger | I2a, P2b |
| **S1** | SimpleFIN, only if the owner subscribes | Tide | **Bulwark**, Ledger | **F5d**, P6, P7, M1 |
| **X1**, **X2** | Crypto rebase and renumber, then exchanges | Tide / Cipher | **Cipher**, **Bulwark** | Keel's schedule, F5d |
| **P10** | Phase 2 threat model and acceptance | Bulwark, Sounding | — | all |

### 6.3 Path B (an investment login first)
| | Path A (default) | Path B |
|---|---|---|
| First link | a bank or card login with imported statements, after a clean preview | 401(k), HSA or brokerage |
| The owner sees | transactions and balances | holdings and balances |
| Changes before M-live | none | P6h (holdings and balances) moves ahead. P2b, P4-map adoption and coverage move to M-live-2 |
| Risk | dedupe is proven on the first real link. The preview and un-adopt are the safety net | none at first; the hard path comes on the second link |

**Default A, adopted only when the preview is clean:**
- `matched_in_overlap / (matched_in_overlap + connector_only_in_overlap) >= 0.95`
- `history_status` is complete
- `held = 0`, or the owner has reviewed and confirmed the held rows

M-link is the same on both paths. Path B is planned only if the owner picks it in Q2.

### 6.4 M-live acceptance: measured on `holdfast-instance`, counts only
Sounding runs V1 through the `audit` service, under ADR 0019. Per linked account, by opaque index:

| # | Measure | Pass |
|---|---|---|
| 1 | `rows_before`, `rows_after` | reported |
| 2 | `inserted`, `superseded`, `skipped_covered`, `held`, `supersede_conflicts`, `amount_mismatch` | conflicts = 0 |
| 3 | Window pairs: live connector row and live import row, equal amount, `\|e(c) - e(i)\| <= 3`. Reported in total. **Fails only for pairs where both rows are outside coverage** (the matcher's domain) | 0 such pairs |
| 3b | Per day outside coverage after adoption: connector count against import count, for statements imported after adoption | reported. Any day with both counts above 0 and unequal is listed for the owner |
| 4 | Latest `sync_run` succeeded with `history_complete` | yes |
| 5 | A second "Sync now" | zero rows added, modified or superseded |
| 6 | **Balance agreement, the gate.** Adopted accounts: expected = the closing balance of the run with the latest coverage `end_date` + Σ live posted rows with `posted_date >` that date, against the connector's `current`. `new` accounts: `current` at sync 2 − `current` at sync 1 = Σ live posted rows between the two sync dates. **Must hold on two consecutive syncs at least 6 hours apart** | within $0.01, either excluding or including live pending rows, and the report says which |
| 7 | Slot ledger | one `linked` row, and the owner's count agrees |

Adoption at M-live is only for accounts with a reconciled run. Otherwise `new`. Any failure means
un-adopt (ADR 0033 §5), a fix, then another preview. No new Item is needed.

### 6.5 File ownership per run
| Slice | Owns |
|---|---|
| F5d | backend/app/security/{crypto,rotation,secrets}.py, backend/tests/test_crypto.py, backend/tests/models/test_key_rotation.py, backend/tests/test_secrets.py, docs/adr/0015-*.md (status) |
| H2 | `docker-compose.instance.yml`, `docker-compose.sandbox.yml`, `Makefile`, `scripts/check-compose-ports.py`, `scripts/dev.ps1`, `.claude/hooks/guard-secrets.sh` (the prune rule), `docs/ops/instances.md`, `backend/app/config.py` (sandbox startup refusal) |
| H1 | `docs/ops/backup.md`, plus a synthetic test dump under `scripts/tests/fixtures/` |
| P1 | `backend/app/domain/connector.py`, `backend/app/integrations/{base,registry,fake}.py`, tests |
| P2m | `backend/app/domain/supersede.py`, `backend/tests/domain/test_supersede*.py`, `backend/tests/fixtures/supersede/*.sanitized.*` |
| P2a | `backend/app/services/ledger_service.py`, `backend/app/domain/ledger_rules.py`, `backend/tests/ledger/test_ledger_sync_verbs.py`, `backend/tests/ledger/test_supersede_verbs.py`, `backend/tests/test_ledger_single_writer.py` |
| P2b | `backend/app/services/ingest.py`, `backend/app/services/statement_import.py` (matcher call), `backend/tests/services/test_ingest*.py` |
| P4a | `backend/migrations/versions/<n>_connector_link.py`, `backend/app/models/{accounts,connectors,imports,jobs}.py`, `backend/app/models/{__init__,_common}.py`, `backend/app/services/statement_accounts.py`, `backend/app/services/statement_import.py` (`_account`), `backend/tests/models/test_connector_link*.py`, `backend/tests/test_migrations.py` |
| P4-link | `backend/app/security/rotation.py` (the `TARGETS` entry only), `docs/adr/0015-*.md` (§2 column list), `backend/app/integrations/plaid/{client,link}.py`, `backend/app/services/{plaid_link,plaid_slots,connections}.py`, `backend/app/services/plaid_setup.py`, `backend/app/services/imports.py` (coexistence check only), `backend/app/api/routes_connections.py`, `backend/app/schemas/connections.py`, `backend/app/config.py`, `.env.example`, tests, `web/src/api/schema.d.ts` |
| P5a | `backend/app/integrations/plaid/{sync,mapping}.py`, `scripts/record_plaid_sandbox.py`, `backend/tests/integrations/plaid/**`, `backend/tests/fixtures/plaid/**` |
| P4-preview | `backend/app/services/connection_preview.py`, `backend/app/jobs/handlers/connection_preview.py`, `backend/app/jobs/{registry,runner}.py`, `backend/app/api/routes_connections.py` (preview routes), tests |
| P4-map | `backend/app/services/{connection_decisions,unadopt}.py`, `backend/app/api/routes_connections.py` (decision routes), tests |
| P5b | `backend/app/jobs/handlers/plaid_sync.py`, `backend/app/services/sync.py`, tests |
| V1 | `backend/app/tools/connector_audit.py`, `docker-compose.instance.yml` (`audit` service), tests |
| P9b-link, P9b-map | `web/src/pages/settings/Connections*.tsx`, `web/src/features/connections/**`, `web/src/api/{plaid,connections}.ts`, `web/src/mocks/**` (connections), `web/src/app/{routes,lazyRoutes}.tsx` |
| K1 | `backend/app/security/secrets.py`, `backend/app/services/secret_settings.py`, `backend/app/api/routes_settings_secrets.py`, tests, ADR status lines, `docs/setup/plaid.md` |

`routes_connections.py` order: P4-link → P4-preview → P4-map. `statement_import.py`: P4a → P2b →
C3e.

### 6.6 Brief notes per slice (Bulwark)
- **F5d:** the API must accept a context that is not a table, for the Redis preview rows, and the
  context still includes `household_id`. For example:
  `household_id|redis|connection_preview|<connection_id>:<requested_by>`.
- **P4a and P4-link:**
  - Add `CHECK (state = 'reserved' OR link_token_enc IS NULL)`.
  - A worker cron walks households with `list_household_ids()` and moves stale `reserved` rows to
    `unknown`. `link-plan` and `link-token` do the same lazily for the caller's household.
- **P4-map:**
  - Un-adopt deletes only connector-sourced snapshots dated on or after `adopted_at`. M1 refuses
    manual balances on adopted accounts.
  - Service tests (Ledger on #74): after un-adopt the account has no `account_coverage` rows and
    no snapshots with a `bank_connection_id`.
  - Deleting a connection that still has adopted accounts gives `409
    connection_has_adopted_accounts`. Un-adopt them first (ADR 0033 §5).
- **P2a and P5b (snapshots, Ledger on #74):** a connector snapshot upsert on `(account_id,
  as_of_date)` never overwrites a manual snapshot (one with `bank_connection_id IS NULL`). It
  skips that date and counts the skip.
  - Bulwark rules in that review on assigning another person as owner of a `new` account.
  - Before adoption ships, a chosen-account upload refuses `account_currency_mismatch` unless the
    account currency equals the file's (USD today).
  - Check that totals exclude or flag `needs_review` runs.
- **Follow-up (next migration window):** add a CHECK requiring the `hf2.` prefix on
  `household_setting.value_enc` and `bank_connection.access_token_enc`.
- **P5b and P7:** every scheduled job skips `pending_setup` connections. The M-link Item stays
  untouched until its mapping is decided.
- **H2:**
  - The prune hook binds only Claude sessions, so H2 also blocks `rm` under the instance directory.
  - `instance-up` refuses to start unless the directory is mode 0700.
  - The real-data instance's container socket and the session bus are on the hide list in
    `scripts/fc-hides.sh` (PR #64). No Codex or Grok worker runs without it.
- **V1:** `SET TRANSACTION READ ONLY`, no published port.
- **P4-link:**
  - Bind the `public_token` to its reservation: same household, `created_by_user_id` is the
    caller, state `reserved`.
  - A second, different `public_token` on a `linked` reservation is a new Item. Record it as its
    own counted row and flag it.
  - An update-mode exchange never creates a connection.
  - The access token is plaintext only in the exchange response. Parse it straight into
    `SecretToken`, seal, and commit before `/accounts/get`.
  - Never log a Plaid response body. Run httpx and httpcore at WARNING.
  - Pass no `webhook`. The setup check warns if `PLAID_WEBHOOK_URL` is set.
  - "Real data" means any account, import run, manual entry or production connection. Also refuse
    a production link on a household with any sandbox connection.
  - OAuth: the redirect URI is the exact registered `https://<tailnet host>/…`.
    - The copy says that registering it tells Plaid the tailnet name.
    - The return page changes no state on GET.
    - The Link token for OAuth resumption lives in `sessionStorage` and is cleared on exit.
  - `GET /connections` reads accounts through RLS. A connection whose accounts are all hidden shows
    no names or masks.
  - `tokens_unreadable` opens tokens without `reveal()`.
- **P4-preview:**
  - Cap the preview rows held in memory.
  - The owner can adopt only accounts they can write. Document it.
- **P4-map:** decide whether a `new` decision that assigns another person as owner needs their
  consent. ADR 0033 §3 records the gap; Bulwark rules on it in review.
- **P5a, P5b, P7:**
  - Household-wide sync is acceptable.
  - Store `error_class` only, never Plaid's message text.
- **K1:**
  - `openai_compat_base_url` and its API key are one key group. Changing the host needs step-up, is
    audited, and requires the key to be re-entered.
  - Audit the new `client_fp` when `plaid_client_id` changes.
  - `PUT` and `DELETE` return a status only.
  - Document step-up when TOTP can't be decrypted after losing `FERNET_KEY`.
- **S1:**
  - Send `Authorization: Basic` itself, because the outbound resolver strips userinfo.
  - Use `trust_env=False` and `follow_redirects=False`.
  - Keep the claim body out of request logging.
- **P9b-link, P9b-map:**
  - CSP additions are for Plaid Link only (`script-src` and `frame-src https://cdn.plaid.com`) and
    Bulwark-gated.
  - The service worker never caches anything under `/api/*`.
- **X1:**
  - The crypto endpoint URL in `household_setting` also needs `hf2`.
  - A node on the LAN or tailnet has a private address, which the outbound resolver refuses. X1
    therefore needs a self-host-only opt-in rule, with its own ADR.

---

## 7. Open PRs and branches, in order
1. **#56: merged** (`6fea745`). P4-link owns its follow-ups: `optional_products`, the slot count,
   product warnings and the `.env.example` default.
2. **#31: merged** (`afdc55b`, L1 done). It
   must merge before F5d, and F5d before P4, so the first access token is sealed as `hf2`, bound to
   its `bank_connection` row (ADR 0015 §6 gate).
3. **#32: lands next** (L2). APPROVE-WITH-NOTES at `82cff55`, which includes the `FOR KEY SHARE`
   fix in `_check_references`.
4. **`feat/P9a-plaid-setup-page`:**
   - Cherry-pick `d973679` into P9b-link and drop `6b8af28`.
   - The preview and decision views follow in P9b-map.
   - The key-entry form waits for K1.
5. **`feat/E10a-crypto-track`:** X1 later. Renumber first, and seal the endpoint URL as `hf2`.

---

## 8. Carry-over money rules, mapped to the slice that enforces each
| Rule | Enforced by | Evidence |
|---|---|---|
| Dedupe Plaid against imports, Plaid wins. **Amended by ADR 0035:** coverage intervals, plus order-preserving ±3-day matching per amount, plus held pairs | **P2m** (matcher), **P2b** (both directions), **P4-map** (adoption puts both in one account) | ADR 0035 §5. Fixture gate. §6.4 measures 3, 3b and 6 |
| Fingerprint with a same-day ordinal | merged. Used in the matcher's sort key | golden tests |
| Pending→posted | **P2a**, **P5a**/**P5b** | fixture |
| `tx_cursor` after commit | **P5b**. Un-adopt resets it (**P4-map**) | kill test. Re-adopt test |
| Upsert and `removed` | **P2a**, **P2b**, **P5b** | replay test |
| `parse_float=Decimal` | merged. S1 too | grep |
| Negate Plaid once, a test per product | **P5a** (`mapping.py`), **P6** | sign tests |
| String amounts in the API | merged. Every new schema uses `MoneyStr` | OpenAPI hash |
| Never sum across currencies | **P2b**, S1, P8 | fixture |
| Splits through one service | **P2a** (moved only inside `LedgerWriter`) | lint |
| Transfer pairing service-enforced | **P2a** (the verb keeps pair rules), plus the Phase 4 trigger | per-field tests |
| Metrics exclude transfers and superseded rows | Phase 5 B2. Supersede keeps state on the live row | B2 |

---

## 9. Secrets per connector (ADR 0015, amended by ADR 0032 and §6 `hf2`)
| Secret | Stored | Entered by |
|---|---|---|
| Plaid trio (one group) | M-link to M-live: `holdfast-instance`'s env file only. After K1: env **or** `household_setting` (`hf2`), the whole group from one source | owner |
| Plaid access token | `bank_connection.access_token_enc` (`hf2`, bound to household_id\|bank_connection\|access_token_enc\|id). The row id is generated before sealing | exchange |
| Plaid Link token | `plaid_link_slot.link_token_enc` (`hf2`, bound to its row) only while `reserved`, at most 4 hours, then NULL | P4-link |
| `public_token` | never | Plaid Link |
| Plaid cursor | `tx_cursor`, plaintext | P5b |
| SimpleFIN setup token | never | owner |
| SimpleFIN access URL | `bank_connection.access_token_enc` (`hf2`, bound to household_id\|bank_connection\|access_token_enc\|id). The row id is generated before sealing | claim |
| Owner-only preview rows | Redis, 30 minutes, `hf2` bound to `(connection_id, requested_by)` | P4-preview |
| Crypto endpoint URL | `household_setting` (`hf2`) | owner |
| Exchange keys | `exchange_account.*_enc` (`hf2`, plus a rotator ADR) | owner |
| AI keys | as for the Plaid group | owner |

**Key entry: options and choice.**
- (A) `.env` plus a prompt. Rejected by the owner.
- (B) The row overrides env.
- (C) Env falls back to the row.
- (D) **One source per key group; both = conflict.** Chosen (ADR 0032).
- (E) The API writes `.env`.

Built after M-live. Until then the dedicated instance's env file holds the keys.

---

## 10. Crypto 10a (slice level) and the custody rules
X1 brings `feat/E10a-crypto-track` onto main as PRs of at most M each:
1. schema
2. pure domain and the guard
3. adapters
4. service and routes

It renumbers the ADR and migration first. X2 adds CCXT with read-only keys. Crypto reaches net
worth through Phase 5 views, never through `IngestService`.

Hard rule 11, gated by Cipher:
- No seed phrase or private key is ever accepted, stored, logged or transmitted. A pure guard
  rejects `xprv`-family keys, WIF, 64-hex keys and word lists before any parser runs.
- Only xpubs, descriptors and addresses are stored, and `CHECK (is_watch_only)` stays until 10c.
- No spend, PSBT or signer code. `FEATURE_CRYPTO_SPEND=false`.
- Exchange keys must be read-only: trade or withdraw rights are refused, or a blocking warning is
  shown. Keys are sealed `hf2`.
- No public chain endpoint without a per-chain opt-in. A private node address needs X1's own
  self-host opt-in ADR.

---

## 11. Fastest path for the owner's own instance
**Now:**
- Statement PDFs through the wizard on the dev stack.
- Once H2 lands, real data moves to `holdfast-instance` by re-importing from the private
  directory.

**Next:**
- **M-link in about 3–4 windows.** The first production Item is held safely, which anchors the 730
  days sooner.
- **M-preview** next: the owner sees the match evidence on real data before any write.
- **M-live in about 7–9 windows** in total.
- CSV/OFX when I2b lands on P4a's resolver.

**Waits:**
- K1, P6, P7 (until then "Sync now"), M1
- SimpleFIN (only if subscribed), I3, X1
- Phase 6, Phase 7

**Phone:** ADR 0021 PA1 or PA-D9. The owner installs Tailscale, and passkeys use the tailnet name
until PA-D1. An ad-hoc `tailscale serve` in front of Caddy is not recommended.

---

## 12. Owner questions and actions

**Questions only the owner can answer:**
1. **Is the Plaid Trial approved for production**, and which products does it list? Is the
   first institution OAuth? (**VERIFY**: OAuth may need the company profile and per-bank
   registration.) **Which dashboard field** shows production Item usage? Is it lifetime usage, or
   active Items only? If it is active only, keep a written slot log (PLAN 3.2) and type that
   number at every link.
2. **The slot plan and the first institution.** Path A (default): a bank or card login with
   imported statements, adopted only after a clean preview. Path B: an investment login, holdings
   first (§6.3). **!** Each production Item is one of 10 lifetime slots.
3. **SimpleFIN ($15/year):** yes or no. Only worthwhile beyond 10 logins or for an institution Plaid
   lacks. ADR 0030 stays Proposed until you answer.
4. **Which machine and directory hosts `holdfast-instance`?** This is a precondition for M-link.
5. **Sandbox keys for `holdfast-sandbox`**, so you can run R1 once and an agent can commit its
   scrubbed output. Or add them as a CI secret.
6. **Backups:** where off the machine do the dumps go, and which password manager holds
   `FERNET_KEY`?
7. **Phone:** Tailscale on the host and phone, a passkey at the tailnet name, and optionally the
   OAuth redirect URI.
8. **Crypto:** your own node, the public-endpoint privacy trade-off, and exchanges.
9. **After M-live:** move the keys in-app (K1)?

**Owner actions before M-link:**
1. Run `instance-up` (H2) on the chosen machine, and enrol a passkey or TOTP there.
2. In that instance's env file, set the production keys and `PLAID_ENV=production`, and delete the
   `PLAID_PRODUCTS` and `PLAID_TX_DAYS_REQUESTED` lines, or set them to the allowlisted values.
   Then `docker compose up -d --force-recreate api worker` for that project.
3. Remove the production keys from the dev checkout's `.env`.
4. Take a backup following `docs/ops/backup.md`. Store `FERNET_KEY` separately. Back up again right
   after the link.
5. At the link: type your slot count from the named source, confirm the existing-connections list,
   type the slot phrase, and check that the institution supports Transactions.
6. At M-preview and M-live: choose the owner and visibility per account, and adopt only when the
   evidence agrees.

---

## 13. Out of this contract
Webhooks and Funnel. The Statements product. An in-app Sandbox/Production switch. Hosted shared
keys. Phases 6, 7, 10b and 10c. Plugin entry points. Description similarity beyond the held rule
(I4).

---

## 14. Review responses

Gadfly's and Bulwark's claims were checked against the code before building on them:
- the card date capture
- `profile:last4:block`
- the `.env.example` defaults and the compose pass-through
- no `optional_products`
- `backup.sh` exits 1
- `_require_live`
- statement import writes no `balance_snapshot`
- #59's `supersede_transaction` and its trigger at `f651389`

`.env` was not read.

### 14.1 Gadfly round 1 (B1–B8, S1–S11)
Revision 2 closed all of them. §14.2 and §14.3 note where round 2 changed a round-1 answer.
- **B1:** ADR 0035, now with coverage intervals and an order-preserving walk.
- **B2:** ADR 0033 rev. 3.
- **B3:** §3.3, now the single definition.
- **B4:** §5.1, now an allowlist with no override.
- **B5:** ADR 0033 §7 and H2.
- **B6:** §6.4, revised.
- **B7:** §6.3.
- **B8:** ADR 0034 rev. 3 and H1, now a runbook.
- **S1–S11:** accepted as before. S1's P9b cut is now P9b-link and P9b-map. S4's resolution uses
  the sealed Link token (R2-B5). S8: R1 is in P5a, and RPL replaces the owner smoke.

### 14.2 Gadfly round 2
| # | Response | Where |
|---|---|---|
| R2-B1 | **Accepted.** One verb pair (`supersede_transaction`, `unsupersede_transaction`) with Phase 2's semantics. #59's trigger is kept and gains the value → NULL branch. The Phase 4 change list is in §3.3.1. **Rejected in part:** "un-adopt runs household-wide". Bulwark's must-fix 3 puts un-adopt in the person-scoped session. `unsupersede_transaction` clears the winner's transfer state first, so `42501` can fire only for a winner-only leg whose partner the person can't write, and un-adopt then returns `409 unadopt_blocked` | §3.3, §3.3.1, ADR 0033 §5 |
| R2-B2 | **Accepted** (Keel: order-preserving). A two-pointer walk per `(account, amount)` with a symmetric ±3-day window (maximum cardinality). Matching is deferred until history is complete. The property is restated as no residue and the same live multiset, not the same pairs. The i1–i4/c1–c4 case is named. The asymmetric `[-1, +3]` window was not taken, because deposit layouts print posting dates while Plaid's `authorized_date` is earlier | ADR 0035 §2, §4, §5 |
| R2-B3 | **Accepted.** `account_coverage` intervals over reconciled runs only (`accepted` excluded). Start is `period_start`, else the minimum row date. Twins in `close` count with any run status. Gap months are ingested. Measure 6 uses the same definition | ADR 0035 §1, §4 P4a, §6.4 |
| R2-B4 | **Accepted.** Un-adopt resets `tx_cursor`, deletes snapshots dated on or after `adopted_at`, deletes coverage, requires `confirm_lost`, and is tested by a re-adopt round trip | ADR 0033 §5 |
| R2-B5 | **Accepted.** `link_token_enc` sealed `hf2` and bound to its row, NULLed on resolution or after 4 hours at the latest. The rotator covers it. Canary tests cover it | ADR 0034 §3 |
| R2-B6 | **Accepted.** Measure 6 is the gate, on two syncs at least 6 hours apart, with a meaningful check for `new` accounts. Measure 3 fails only inside the matcher's domain. 3b added. Adoption requires a reconciled run | §6.4, ADR 0033 §3 |
| R2-B7 | **Accepted, per Keel:** `PLAID_ENV`'s default is unchanged. Sandbox and real data never share a household, enforced in code both ways (ADR 0033 §7). The restore drill uses a synthetic dump only. Real data sits on 0700 bind mounts outside the repo. A hook blocks volume prunes. **Not taken:** the `INSTANCE_KIND=real` startup flag, because the data rule closes the gap without touching the default, as Keel ruled | ADR 0033 §7, ADR 0034 §5, H2, H1 |
| R2-S1 | **Accepted** (Keel). The override path is deleted. Products are an allowlist (Bulwark 2). `balance` is a **VERIFY** item | §5.1, D5–D7 |
| R2-S2 | **Accepted, changed.** Pairs that aren't unique in the window and share no description token are **held**: listed for the owner and confirmed by count before adoption. The merchant signal is not a sort key, which would break the order-preserving walk | ADR 0035 §3 |
| R2-S3 | **Accepted.** Owner-only rows, sealed in Redis. Adopt requires complete history | ADR 0033 §2, §3 |
| R2-S4 | **Accepted.** The field is a VERIFY item, and the slot log is the fallback. Q1 names it | ADR 0034 §2, §12 |
| R2-S5 | **Accepted** (Keel). P4a takes the token right after 0010. The skip rule now requires both verdicts | §4 |
| R2-S6 | **Accepted** (Keel). P4 is split into P4-link, P4-preview and P4-map | §6.1 |
| R2-S7 | **Accepted.** V1 is a one-off `audit` service with DB-only env and no Plaid keys | §6.1 V1 |
| R2-S8 | **Accepted.** H2 depends on L1, and its acceptance tests `make down -v` | §6.1 H2 |
| Faster path | **Accepted** (Keel). M-link and M-preview milestones. H1 is a runbook plus one synthetic restore. The owner sandbox smoke is dropped, and the RPL replay is kept | §6.1 |

### 14.3 Bulwark BLOCK on `91f6b72`
| # | Closed | Where |
|---|---|---|
| 1 `hf2` gate | F5d row inserted with Bulwark's exact text, before P4-link. F5d is in the Depends of P4-link, K1, S1 and X1. §6.5 row added. §7.2 sentence replaced. §9 rows use `hf2` bound to the row. ADR 0030 and ADR 0032 changed. The Link token and preview rows are sealed `hf2` too | §6.1 row 3, §6.5, §7, §9, ADR 0030 §3, ADR 0032 §2 |
| 2 Product allowlist | Exact text in §5.1 and D5. ADR 0034 §2's phrase variants removed | §5.1, D5, ADR 0034 §2 |
| 3 Requester identity in jobs | L2 acceptance appended. P4a item 7 (`job_run.requested_by`). ADR 0033 §2 (person-scoped candidates, no-requester failure, Redis key and GET restriction). The session rule after §3.2 and at the start of §5 | §6.1 L2, §4, ADR 0033 §2–3, §3.2, §5 |
| 4 Audit | §5.3 added, without the deviation flag (the phrase path is gone) | §5.3 |
| 5 Slot count | ADR 0034 §1 "What counts" | ADR 0034 §1 |
| Slice notes | All copied into the slice briefs | §6.6 |
