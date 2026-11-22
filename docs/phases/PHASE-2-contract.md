# Phase 2: connector-lane contract (every source into the owner's own instance)

- Revision 2 (2026-11-21): the rework after Gadfly's REWORK-large
  (`docs/phases/reviews/PHASE-2-contract-gadfly.md`, against revision 1 at `77aafbe`). Keel's
  rulings on B1–B8 are applied. §14 answers every blocker and should-fix.
- Spec: PLAN 3, 7, 13 Phase 2 and 3, and 20 (Section 20 wins). ROADMAP E2 (P1–P10), S1, E3 (I1–I6),
  10a.
- ADRs: 0007, 0008, 0011, 0015, 0016, 0017, 0019, 0021, 0022, 0023, and this lane's **0030**
  (SimpleFIN, Proposed), **0032** (in-app key entry, Proposed), **0033** (adoption after a
  dry-run preview), **0034** (per-client slot ledger) and **0035** (overlap matching and statement
  horizon).
- ADR numbers: 0031 is on main (#60). 0024 and 0026–0029 belong to PR #59. 0025 stays reserved for
  the Plaid lane.

This file is binding for every connector-lane story. A builder who must deviate says so in the PR
body and asks Lattice for an amendment, which lands here before the code merges.

**Goal (owner, 2026-11-21):** "hook up every connector with our data" and view it on the phone.

**Milestone M-live:** the owner links one real institution through Plaid on their **dedicated
instance**. Measured counts (§6.4) show the link created zero duplicates and that balances agree.
M-live is reached on the PC. The phone needs the ADR 0021 lane (§11). PLAN 13 Phase 2 acceptance
remains P10's job.

---

## 0. What exists (`origin/main` at `6fea745`, and every branch)

| Piece | State | Where |
|---|---|---|
| F2 jobs (registry, IDs-only payloads, per-household lock, `job_run`) | merged | `app/jobs/`, ADR 0016 |
| F2b job hardening: confused deputy, stuck `running`, `enqueue(requested_by=)` | **PR #32 open**. 24 commits behind main, and conflicts with #31 on `backend/app/worker/main.py` | `fix/F2b-job-hardening` |
| F5a `SecretToken` | merged | `app/security/secret_token.py` |
| F5c `get_secret`, `hf1` envelope, `rotate-keys`, `resolve_outbound`, migration `0010_list_household_ids` | **PR #31 open**, CI green, no review | `feat/F5c-secret-resolver-rotation` |
| F6 `import_run`, `sync_run`, `superseded_by_id` | merged | ADR 0017 |
| Household-wide context guard (no `household_wide=True` from `app/api/`) | merged (#61) | `tests/test_household_wide_guard.py` |
| Connector and investment schema (`bank_connection`, `account`, `balance_snapshot`, holdings, liabilities) | merged, schema only | `models/accounts.py`, `models/investments.py` |
| Statement PDF import, wizard, review queue, Transactions screen | merged | `services/{imports,statement_import}.py` |
| PDF profiles | merged: 6 layouts, 2 institutions. The card layout stores the **first** of two printed dates and drops the second. Interest and fee rows take the period end | `app/importers/profiles/` |
| CSV/OFX parsers | merged (I2a). The import into the ledger (I2b) is in progress | `feat/I2b-csv-ofx-import` |
| `LedgerWriter` (insert only; `_require_live` refuses edits to superseded rows) | merged (C1) | `services/ledger_service.py` |
| Category tree and PFC mapping | merged (C2) | ADR 0022 |
| **P3 Plaid client and setup checklist** | **merged (#56, `6fea745`)**. `link_token_create(products, transactions_days_requested, ...)` has **no `optional_products`**. The setup check reads `PLAID_PRODUCTS` and `PLAID_TX_DAYS_REQUESTED` | `integrations/plaid/client.py`, `services/plaid_setup.py`, ADR 0023 |
| P9a Connections page | local branch, one commit `d973679` on top of P3's old first commit | `feat/P9a-plaid-setup-page` |
| P1 `BankConnector`, P2 `IngestService`, supersession | **do not exist** on any branch | — |
| Compose and env | `.env.example` ships `PLAID_PRODUCTS=transactions,auth,balance,investments,liabilities,statements` and `PLAID_TX_DAYS_REQUESTED=730`. Compose passes both through. Ports are fixed (`127.0.0.1:443`, 5432, 56379), so there is no second-instance support | `docker-compose*.yml` |
| Backups | `scripts/backup.sh` exits 1, "not implemented yet (Phase 9)" | `scripts/` |
| Crypto Track | local branch, 7 commits. ADR 0020 and migration 0007 collide with main | `feat/E10a-crypto-track` |
| Manual accounts, competitor exports, credit, tax documents | not started | — |
| Phone access | ADR 0021 Proposed, no code | — |

**What skipping P1 and P2 costs.** Without them:
- Plaid-wins dedupe, pending-to-posted and `removed` would live inside each connector.
- The owner's imported history and the linked bank would be separate accounts, so every overlap
  month would show twice.

**Ruling:** P1 and P2 are built before the first live sync.

---

## 1. Every source the product plans

Cost markers: **$** costs the owner money. **!** has an irreversible external effect.

| Source | State | Depends on | Owner must supply |
|---|---|---|---|
| **Plaid Transactions** | client merged. Sync not started (P5) | §6.1 path | Trial approved for production (free). Production keys in the **dedicated instance's** `.env` (in-app after K1). The slot plan (PLAN 3.2). **!** one of 10 lifetime slots per Item, not returned on delete. Dashboard Item count at each link. Bank sign-in and MFA |
| **Plaid Balances** | not started. From the sync response in P5; daily `/accounts/balance/get` in P6 | P5 | — |
| **Plaid Investments** (401(k), HSA, brokerage) | not started (P6) | P5 (path A) or before M-live (path B, §6.3) | which login holds the 401(k), the HSA custodian. **!** one slot per login |
| **Plaid Liabilities** | not started (P6) | P5 | — |
| **SimpleFIN** | not started. ADR 0030 is **Proposed** until the owner answers | M-live, P6, P7, M1 | **$** Bridge subscription ($1.50/month or $15/year plus tax). Worth it only if the slot plan exceeds 10 logins or an institution is missing from Plaid |
| **Statement PDF** | merged (6 layouts) | — | PDFs from the private directory (ADR 0019) |
| **CSV/OFX/QFX** | parsers merged. I2b in progress | I2b, which must use P4a's resolver (S3) | bank downloads |
| **Competitor exports** | not started (I3) | I2b | export files, if any |
| **Credit-report PDF** | Phase 6 (Q2) | — | three free reports |
| **Crypto watch-only** (xpub, descriptor, address) | local branch (X1) | X1, Cipher and Bulwark | public xpubs or descriptors and addresses. An Electrum server and/or an EVM RPC endpoint you run. **Never a seed or private key** |
| **Crypto exchange key** | not started (X2) | X1 | **read-only** keys |
| **Manual accounts** | not started (M1) | — | names and balances. Free cover for low-activity accounts |
| **Tax documents** | Phase 7, `FEATURE_TAX` | — | forms, last year's return |

---

## 2. Decisions at a glance

| # | Question | Decision | Record |
|---|---|---|---|
| D1 | P1 and P2 before the first live sync | Yes. P2 is split into P2a (writer verbs) and P2b (ingest) | §0 |
| D2 | Plaid key entry | In-app, with exactly one source per **key group**; both sources means conflict. Built **after** M-live (K1). `PLAID_ENV` stays in `.env` | ADR 0032 |
| D3 | Imported history versus linked accounts | Dry-run preview with ranked candidates and evidence. Explicit adopt, new or ignore. Un-adopt in P4. Never automatic. A sandbox connection can never adopt | ADR 0033 |
| D4 | Slot guard | Per-client ledger, reserved at token time, no DELETE. Every production link is confirmed against the dashboard count. `/link/token/get` resolves reservations | ADR 0034 |
| D5 | Products | Built in code from `login_kind`. `bank_card`: `products = ["transactions"]`, `optional_products = ["liabilities"]`. `retirement_hsa`: `["investments"]`. `brokerage`: `["investments"]`. `auth` and `statements` are **refused**. **VERIFY** `optional_products` on Trial in P4 | §5.1 |
| D6 | History depth | 730 days. Plaid fixes it per Item at creation (**irreversible**) | §5.1 |
| D7 | Env overrides | When `PLAID_PRODUCTS` or `PLAID_TX_DAYS_REQUESTED` differ from D5 and D6, the link is blocked until the owner types the longer phrase naming the effective values (ADR 0034 §2). The setup check flags it. Owner action: delete both lines | §5.1 |
| D8 | Overlap dedupe | Statement horizon, plus amount equality with a 3-day window against `posted_date` or `authorized_date`, plus a deterministic greedy tie-break | ADR 0035 |
| D9 | Supersede | Carries category, splits, transfer membership, tax fields, receipt and notes. Phase 4 adds rejections and recurring members | §3.3 |
| D10 | Sync shape | One full pagination run in memory, applied in **one** transaction together with `tx_cursor` and `sync_run` | ADR 0017 |
| D11 | Initial history without webhooks | P5 re-enqueues with backoff until `transactions_update_status = HISTORICAL_UPDATE_COMPLETE`. **VERIFY** with R1's recording | PLAN 7.4 (a) |
| D12 | Where sandbox runs | Only in a throwaway compose project (`holdfast-sandbox`). Code refuses a sandbox Link token on any household with an import run or a production connection | §6.1 H2, P4 |
| D13 | Real data instance | A dedicated compose project with its own volumes and env file. The only place `PLAID_ENV=production` exists | §6.1 H2 |
| D14 | Link from the phone | Not for M-live. Desktop only (OAuth pop-up). Phone Link needs a registered HTTPS redirect URI | ADR 0023 |
| D15 | Who may link | The owner role, with step-up, for new Items, relinks, adoption, un-adopt, SimpleFIN claims and key entry | security rule |

---

## 3. Interfaces

### 3.1 Domain value types (P1): `backend/app/domain/connector.py`, pure
```python
@dataclass(frozen=True)
class ConnectorAccount:
    external_account_id: str
    name: str
    official_name: str | None
    mask: str | None                  # at most 4 digits
    type: AccountType
    subtype: str | None
    currency: str | None              # ISO 4217; None = unsupported, never stored
    institution_name: str | None

@dataclass(frozen=True)
class ConnectorBalance:
    external_account_id: str
    as_of: date
    current: Decimal | None           # Holdfast sign: owed is negative
    available: Decimal | None
    limit: Decimal | None             # positive magnitude

@dataclass(frozen=True)
class ConnectorTxn:
    external_account_id: str
    external_id: str
    posted_date: date
    authorized_date: date | None
    amount: Decimal                   # outflow negative, already converted
    description_raw: str
    merchant_name: str | None
    pfc_primary: str | None
    pfc_detailed: str | None
    is_pending: bool
    pending_external_id: str | None   # on a posted row: the pending row it replaces

@dataclass(frozen=True)
class SyncBatch:
    accounts: tuple[ConnectorAccount, ...]
    balances: tuple[ConnectorBalance, ...]
    added: tuple[ConnectorTxn, ...]
    modified: tuple[ConnectorTxn, ...]
    removed: tuple[tuple[str, str], ...]   # (external_account_id, external_id)
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
                    cursor: str | None) -> SyncBatch          # one complete pagination run
    def health(self, state: ConnectionState, now: datetime) -> ConnectorHealth   # pure
```
- **Errors:** `ConnectorError(kind, code, request_id)`, where `kind` is one of `needs_relink`,
  `retryable`, `rate_limited`, `payment_required`, `revoked`, `client_changed`, `fatal`. No
  provider message text.
- **Registry:** `registry.get(provider)` returns the connector. `registry.override` is a context
  manager for tests only.
- **`FakeConnector`:** replays scripted batches and errors, including mutation-during-pagination.

### 3.3 Ledger writer sync verbs (P2a): `services/ledger_service.py`
```python
async def upsert_external(self, new: NewTransaction) -> UpsertOutcome   # inserted | updated | unchanged
async def remove_external(self, account_id: UUID, external_id: str) -> bool
async def post_pending(self, pending_external_id: str, posted: NewTransaction) -> UUID
async def supersede(self, loser_id: UUID, winner_id: UUID) -> None
```

**`upsert_external`**
- Matches on `(account_id, external_id)`.
- An update may change the amount (through `validate_amount`), dates, description, merchant, PFC
  and `is_pending`.
- It never replaces a `user` or `rule` category.
- A changed amount clears splits and reports `splits_cleared`.

**`post_pending`**
- Inserts the posted row and deletes the pending row in one unit of work.
- It carries the category (when it outranks), the notes, and the splits (only when the amount is
  unchanged).

**`supersede(loser, winner)`: the clause.** Keel passes the same text to the Phase 4 contract; it is
recorded in `~/.local/state/holdfast-keel/supersede-clause.md`.

*Preconditions.* If any fails, nothing is written and the result is `LedgerRuleError` or
`SupersedeConflict`:
- Same account.
- The loser has `source = 'import'`.
- The winner has a connector source and its connection has `env = 'production'`.
- Both rows are live and not pending.
- Exactly equal amounts.
- When both are transfer legs, the same group. Otherwise `SupersedeConflict`, which is counted, and
  both rows stay live.

*Lock.* Lock the loser, the winner and every other leg of their groups, in id order.

*Effects.* One unit of work, in this order. All of them happen before `superseded_by_id` is set,
because `_require_live` refuses edits to a superseded row.
1. **Category.** The winner takes the loser's `category_id` and `category_source` when the loser's
   source outranks the winner's (user > rule > plaid > ml). From C3b on it also takes
   `category_rule_id`, which **C3b adds**.
2. **Splits.**
   - The loser has splits and the winner has none: the loser's split rows move to the winner. The
     sums hold because the amounts are equal.
   - Both have splits: each keeps its own. Counted as `splits_kept_on_loser`.
3. **Transfers.** No rejection row is written.
   - The loser is paired in group `G` and the winner is in no transfer: the winner joins `G`
     (`is_transfer`, `transfer_group_id = G`) and the loser leaves (`false`, `NULL`).
   - The loser is single-marked: the winner becomes single-marked, and the loser is cleared.
   - The winner is already in `G`: nothing changes.
4. **Tax.** `winner.is_tax_relevant = winner OR loser`. `tax_tag` keeps the winner's value when it
   has one; otherwise it takes the loser's.
5. **Receipt.** `receipt_document_id` keeps the winner's value when it has one; otherwise it takes
   the loser's.
6. **Notes.** The winner's notes when non-empty, otherwise the loser's.
7. **`transfer_rejection`.** Rows naming the loser are rewritten to the winner. A duplicate is
   deleted. **C4c adds this.**
8. **`recurring_member`.** The loser's rows move to the winner, or are deleted when the winner is
   already in that series. Series state is unchanged. **C5b adds this.**
9. **Transfer suggestions.** Rows naming the loser are deleted. **C4d adds this.**
10. **Mark.** `loser.superseded_by_id = winner`. The loser keeps its fingerprint and run id, so a
    re-import inserts nothing.

*Reverse.* `remove_external` of a winner, and un-adopt:
1. Clear the loser's `superseded_by_id` first.
2. Move back from the winner to the loser:
   - splits, when the loser has none
   - transfer membership
   - tax, receipt and notes, only where the loser's value is empty
   - category, when the winner's source outranks the loser's
   - rejections and members, through the C4c and C5b steps
3. Delete the winner.

Removing a transfer leg with no loser leaves its partner unpaired. No rejection row is written.

None of these verbs is audited per row. `sync_run.rows_superseded` (P4a) counts them.

### 3.4 IngestService (P2b): `services/ingest.py`
`apply(connection_id, batch, sync_run_id) -> IngestCounts` runs in the job's household-wide
session, inside the caller's one transaction:
1. **Accounts.** Resolve each account through `connector_account_link` (ADR 0033 §1).
   - `mapped`: ingest into the account.
   - `ignored`: skip.
   - unseen: insert a `pending` row and skip.
   - `currency is None`: skip, counted as `unsupported_currency`.
2. **Balances.** Upsert per `(account_id, as_of_date)`. Never summed.
3. **Horizon.** Posted rows with `posted_date < account.connector_since` are dropped from `added`
   and `modified`, counted as `skipped_before_horizon` (ADR 0035 §1).
4. **Removals.** `removed` goes to `remove_external`. Then `modified` and `added` go to
   `upsert_external`. A posted row whose `pending_external_id` names a live pending row goes to
   `post_pending` instead.
5. **Categories.** Plaid rows get the ADR 0022 mapping (`category_source = 'plaid'`). Inline rules
   are added by **C3e** (it lands after P2b), with a test.
6. **Matching.** `domain/supersede.py` runs ADR 0035 §2 to §4 for every touched account, and each
   accepted edge goes to `supersede`. `statement_import` calls the same matcher after each block
   insert; that call ships in **P4a's** PR (S2).

**Invariants (property tests):**
- Replaying a batch twice makes zero writes.
- 100 random interleavings of statement blocks and sync batches reach the same end state.
- No row is both a loser and a winner.
- The result doesn't depend on input order.
- **Fixture gate:** sanitized real-layout card rows (`make sanitize`, ADR 0019) against synthetic
  connector rows posted 0 to 3 days later, with `authorized_date` equal to the statement date, must
  produce zero live duplicates before P2b merges.

---

## 4. Schema (one migration in this lane before M-live)

**Fixed migration-token order, shared with Phase 4.** Keel passes it on with the note.
1. `0010_list_household_ids` (#31)
2. M1 `<n>_rules` (C3b)
3. **`<n>_connector_link` (P4a)**
4. M2 `<n>_import_run_reason_final` (I1d)
5. M3 `<n>_transfer_corrections` (C4c)
6. M4 `<n>_recurring_series` (C5b)

A builder may write the migration before holding the token, but numbers it from the head and
rebases only once they hold it. If the next holder has no open PR one window after the token
frees, the following slice may take it once. Keel notes the skip in SPRINTS.

**Other shared files:**
- `ledger_service.py`: P2a → C3b → C4c → C5b.
- `statement_import.py`: P4a → C3e.
- I2b uses P4a's resolver. If I2b lands first, P4a switches its call.

**P4a `<n>_connector_link`:**
1. `plaid_link_slot`, exactly as ADR 0034 §1:
   - per-client unique `reserved` row
   - no DELETE grant
   - transition trigger
   - `plaid_slot_count(client_fp, env)` `SECURITY DEFINER`, returning a count only, built on #31's
     `list_household_ids` pattern
2. `connector_account_link`, as ADR 0033 §1.
3. `bank_connection`: add `client_fp text NULL` (16 lowercase hex, CHECK). `status` gains
   `pending_setup`.
4. `account`:
   - Add `statement_key text NULL`, backfilled for `source = 'import'`. The new
     `UNIQUE (household_id, statement_key) WHERE statement_key IS NOT NULL` replaces
     `uq_account_household_id_external_account_id_import`. Downgrade restores the old index.
   - Add `connector_since date NULL`.
   - Add the adoption trigger, as ADR 0033 §6. That includes "the connection's `env` is
     `production`".
5. `sync_run`: add `rows_superseded`, `rows_skipped_horizon` and `history_complete`, all
   non-negative and defaulting to 0 or false.
6. In the same PR:
   - `app/services/statement_accounts.py`, the one resolver, used by `statement_import._account()`
     (and by I2b)
   - the reverse matcher call in statement import

---

## 5. API and Plaid parameters

### 5.1 Link parameters (P4)
`login_kind` is required on every link plan.

| `login_kind` | `products` | `optional_products` | `days_requested` |
|---|---|---|---|
| `bank_card` | `transactions` | `liabilities` | 730 |
| `retirement_hsa` | `investments` | — | — |
| `brokerage` | `investments` | — | — |

- `PLAID_PRODUCTS` is still read by the setup probe. For Link it is an **override**: when it differs
  from the table, or `PLAID_TX_DAYS_REQUESTED` differs from 730, the link plan lists the deviation
  and the token needs the longer phrase (ADR 0034 §2). The phrase shows a lower day count as
  permanent.
- `auth` and `statements` are refused for Link even if listed: `409 product_not_allowed`.
  - `auth` would consent full account and routing numbers (data minimization).
  - `statements` has no slice yet.
- The setup check reports the env list as `products: warn` when it contains either.
- P4 adds `optional_products` to `PlaidClient.link_token_create` and owns `client.py` for that
  change.
- **VERIFY** in P4 with the setup check's probe that `optional_products` is accepted on Trial. If
  it isn't, `bank_card` drops to `["transactions"]`. P6 then adds liabilities in a later slice by
  re-consent, and needs no new Item.

### 5.2 Endpoints
| Method and path | Who | Body and response | Slice |
|---|---|---|---|
| GET `/plaid/setup` · POST `/plaid/setup/check` | owner | ADR 0023 checklist. P4 switches `item_slots` to `plaid_slot_count` and adds `products: warn`, `tokens_unreadable` and `ledger_orphans` | merged, P4 |
| POST `/connections/plaid/link-plan` | owner | `{login_kind, mode: new\|update, connection_id?}` → `{products, optional_products, days_requested, deviations, ledger_count, existing_connections, phrase_template}`. No Plaid call | P4 |
| POST `/connections/plaid/link-token` | owner + step-up | `{login_kind, mode, connection_id?, dashboard_count, not_a_duplicate, confirm_phrase}` → `{link_token, expiration, reservation_id}`. `409 confirm_required\|no_slots_left\|product_not_allowed\|sandbox_on_real_data` | P4 |
| POST `/connections/plaid/exchange` | owner | `{public_token, reservation_id}` → `{connection}` (status `pending_setup`). Idempotent on `reservation_id` | P4 |
| POST `/connections/plaid/reservations/{id}/resolve` | owner | → the slot state after the `/link/token/get` check | P4 |
| POST `/connections/plaid/reservations/{id}/abandon` | owner + step-up + phrase | → 204 (`unknown` → `abandoned`) | P4 |
| POST `/connections/{id}/preview` · GET `/connections/{id}/preview` | owner | → `202 {job_run_id}` · → ADR 0033 §2 evidence (counts and dates) | P4 |
| POST `/connections/{id}/accounts` | owner + step-up | `{decisions: [{external_account_id, action: adopt\|new\|ignore, account_id?, owner_person_id?, visibility?}], close?: [account_id]}` → connection `active`, initial sync enqueued. `409 preview_required\|account_has_other_connection` | P4 |
| POST `/connections/{id}/accounts/{external_account_id}/unadopt` | owner + step-up | → 204 | P4 |
| GET `/connections` | member | `[{id, provider, institution_name, env, status, error_code, last_success_at, pending_accounts, accounts}]` plus `plaid_slots` | P4 |
| POST `/connections/{id}/sync` | owner | → `202`. 15-minute idempotency bucket | P5 |
| GET, PUT, DELETE `/settings/secrets[/{name}]` | owner (+ step-up for writes) | ADR 0032 | K1 |
| POST `/connections/simplefin/claim` | owner + step-up | preview `{host}`, then confirm | S1 |
| GET `/sync-health` | member | per-account status, stale > 48h, error codes, cleanup counts | P8 |

**Never in a response, a log, an audit row, a job payload or a Redis key:** `public_token`, setup
tokens, access tokens, access URLs, or the client id in plaintext. A canary test covers each.

---

## 6. Slices (one PR each)

Kinds: **land** (an existing PR), **pure**, **db**, **web**, **ops**. Bulwark gates anything
touching auth, tokens, Plaid, `app/security/`, compose or a migration. Ledger gates money paths.
Prism gates `web/`. Sounding gates acceptance.

**Process rule (S10):** no more merging `main` into open PR branches before review. Rebase once
when the gate is ready, then review once.

### 6.1 Path A to M-live (default: a bank or card login that has imported statements)

| # | ID | Scope | Owner | Reviewers | Token | Depends | Acceptance evidence |
|---|---|---|---|---|---|---|---|
| 1 | **L1** | Land #31. Rebase once. Fix ADR 0015's stale "builds without `list_household_ids`" paragraph | Harbor | **Bulwark** (full, protected paths), Ledger | yes (0010) | — | SHA-bound verdicts. ADR 0015 test list. Up/down/up |
| 2 | **L2** | Land #32. Rebase once onto main after L1, resolving `worker/main.py` | Lattice | **Bulwark** | no | L1 | Verdict at head. Confused-deputy and timeout tests |
| 3 | **H2** | Two isolated compose projects. `holdfast-instance`: real data, own volumes, own env file outside the main checkout's `.env`, `PLAID_ENV` allowed to be `production`, no published DB or Redis port. `holdfast-sandbox`: throwaway, own volumes, own env with fresh `FERNET_KEY`, `PLAID_ENV` forced to `sandbox` at startup, Caddy on another loopback port. Both with `make` targets (`instance-up`, `sandbox-up`, `sandbox-destroy`). Agents' test stacks never use either | Harbor | **Bulwark** (compose) | no | — | `check-compose-ports.py` extended. The sandbox api refuses to start with `PLAID_ENV=production`. `make down` in the main checkout leaves both projects' volumes intact |
| 4 | **H1** | Minimal `scripts/backup.sh` / `restore.sh`: `pg_dump` (custom format) plus the document store, into an owner-chosen directory off the machine. Prints the key-handling steps (ADR 0034 §5). A restore drill into `holdfast-sandbox` | Harbor | Bulwark | no | H2 | The drill restores a synthetic dataset, the restored instance opens a sealed canary with the kept key, and fails cleanly with a different key |
| 5 | **P1** | §3.1, §3.2, `FakeConnector`, contract tests | Tide | Ledger, Bulwark | no | — | Contract suite. Value types refuse `float` and 3-decimal amounts. import-linter |
| 6 | **P2a** | §3.3: verbs, the supersede clause steps 1–6 and 10 and its reverse, `SupersedeConflict`. Lands **before C3b** | Ledger | Bulwark, Ledger (independent run) | no | P1 types | A test per carried field (category, splits moved and kept, paired and single transfer, tax, receipt, notes) and its reverse. Conflict leaves both live. Single-writer lint |
| 7 | **P2b** | §3.4, `domain/supersede.py` (ADR 0035) | Tide | **Ledger**, Bulwark | no | P1, P2a | ADR 0035 tests. Two-coffees table. Interleaving property. **Sanitized trans≠post fixture: zero live duplicates** |
| 8 | **P4a** | §4 migration, models, resolver, reverse matcher call | Tide | **Bulwark** (RLS), Ledger | yes (after M1) | L1, P2b | RLS lint. No DELETE on `plaid_link_slot`. Two households, one client, one `reserved`. Transition matrix. The adoption trigger refuses a sandbox connection. Backfill equality. Re-import after adoption lands on the same account. Up/down/up |
| 9 | **P4** | §5: link plan, guard (ADR 0034), idempotent exchange, `/link/token/get` resolution, preview job, decisions, un-adopt, sandbox refusal on real data, `optional_products` in `client.py`, D5–D7, `client_fp` check at use time | Tide | **Bulwark**, Ledger | no | L1, L2, P1, P4a | Guard at 7/8/9/10 and dashboard > ledger. A deviation needs the long phrase. `auth` refused. An exchange retried after a crash makes no second connection. `onExit` alone never abandons. Adopt without a preview gives `409`. Un-adopt restores the import rows. A sandbox token on a household with an import run gives `409`. Token canaries |
| 10 | **P5** | `plaid.sync_item` job (§2 D10, D11). Balances from the sync response. Mapping signs in `integrations/plaid/mapping.py`. Enqueues `ledger.refresh` when registered. Plus **R1** `scripts/record_plaid_sandbox.py` (sandbox only; strips tokens and ids; writes `tests/fixtures/plaid/recorded/`) | Tide | **Ledger**, Bulwark | no | P2b, P4 | Hand-built fixtures: kill between pages, then resume, gives no duplicates and the old cursor. Mutation restart. Sign tests (debit, credit, card and loan balance). `removed`. Pending→posted. `client_changed`. Merges on hand-built fixtures. R1 output replays before SBX |
| 11 | **V1** | `python -m app.tools.connector_audit`, read-only, counts only, opaque account indexes (§6.4) | Tide | Sounding, Bulwark | no | P5 | Runs on synthetic data. The output schema has no names, masks, merchants or amounts |
| 12 | **P9b-min** | From P9a's `d973679` (cherry-pick onto main, drop `6b8af28`): setup checklist, link-plan dialog (products, days, dashboard count, the "not a duplicate" list, phrase), Plaid Link button, preview and decision sheet with evidence, un-adopt, "Sync now", minimal connection list. No key-entry form | Prism | Prism, Sounding, **Bulwark** | no | P4 API (mocks first) | 375 px. vitest: the phrase is required, the 11th is blocked, a deviation shows the long phrase, adopt is disabled until the preview loads |
| 13 | **SBX** | Sandbox gate **in `holdfast-sandbox` only**: R1 recording replayed. Owner smoke (`user_good`, `user_transactions_dynamic`) creating only `new` accounts. A restore drill (H1). Then `sandbox-destroy` | Sounding, owner | Keel | no | 1–12 | `docs/phases/PHASE-2-gate.md`, counts only. Re-sync makes zero writes. The owner's real instance is untouched (V1 row counts on it before and after are equal) |
| — | **M-live** | Owner: backup (H1), then link **one** institution on `holdfast-instance` from the desktop, run the preview, decide, sync. Sounding runs V1 before and after | owner, Sounding | Keel | — | SBX, owner preconditions (§12) | §6.4 numbers |

**Estimate (S10, restated):** **6–8 windows** of agent time to M-live.
- Rows 1–2 are about 1 window of review (#31 alone is 1,671 lines on protected paths).
- Rows 3–12 are 10 build slices with about 22 SHA-bound review runs.
- The supersede clause and preview add real work to P2a and P4.
- Owner steps come on top: Trial check, backups, dashboard reads, the link itself.

### 6.2 After M-live
| ID | Scope | Owner | Reviewers | Token | Depends |
|---|---|---|---|---|---|
| **K1** + **P9c** | ADR 0032 key entry, and the key-entry form on Connections | Harbor, Prism | **Bulwark**, Prism | no | L1, M-live |
| **P6** | Investments (holdings, securities, investment txns), liabilities, daily balance. A sign test per product | Tide | Ledger, Bulwark | only if a key is missing | P5 |
| **P7** | `needs_relink`, update mode, backoff, 6-hour cron at a per-connection minute, `seed_sandbox.py` | Tide (+ Harbor) | Bulwark | no | P5 |
| **P8** | `/sync-health`, stale > 48h. Item replacement defined (plaid-over-plaid supersession for a disconnected connection's rows, or the documented refusal) | Tide | Ledger, Bulwark | maybe | P5 |
| **P9d** | Sync Health page, relink | Prism | Prism, Sounding | no | P7, P8 |
| **M1** | Manual accounts and balance entry (`source = 'manual'`) | Ledger (+ Prism) | Bulwark, Prism | no | — |
| **I2b**, **I3**, **I4** | CSV/OFX (on P4a's resolver), competitor exports, wider overlap dedupe | Tide | Ledger | per brief | I2a, P2b |
| **S1** | SimpleFIN (ADR 0030), only if the owner subscribes | Tide | **Bulwark**, Ledger | no | P6, P7, M1 |
| **X1**, **X2** | Crypto Track rebase and renumber, then CCXT exchanges | Tide / Cipher | **Cipher**, **Bulwark** | yes | Keel's schedule |
| **P10** | Phase 2 threat model and acceptance | Bulwark, Sounding | — | no | all |

### 6.3 Path B (an investment login first), and why A is the default
| | Path A: statement-backed bank or card first (default) | Path B: 401(k), HSA or brokerage first |
|---|---|---|
| What the owner sees | transactions and balances for an account with history | holdings and balances only |
| Extra slices before M-live | none beyond §6.1 | **P6h** (holdings and investment balances from P6) moves before M-live |
| Slices that can move after M-live | none | P2b's supersession, the `statement_key`, `connector_since` and adoption trigger parts of P4a, and the preview. P4 keeps `new` and `ignore` only |
| Risk | adoption and dedupe are proven on the first real link. The preview must be clean first, and un-adopt is the fallback | no dedupe risk on the first link. The hard path is proven later, on a second link |
| Time to first link | 6–8 windows | about the same: P6h replaces the adoption work, and M-live-2 follows |

Default: **A**, linked only after a dry-run preview shows the histories agree. That means
`matched_before_horizon / (matched_before_horizon + connector_only_before_horizon) >= 0.95`, and
every unmatched row is explained by the owner. Path B is planned only if the owner picks an
investment login in Q2. The two paths are never planned together.

### 6.4 M-live acceptance (B6): measured on the owner's instance, counts only
Sounding runs V1 on `holdfast-instance` under ADR 0019. Per linked account, by opaque index:

| # | Measure | Pass |
|---|---|---|
| 1 | `rows_before`: live rows in the account before the link. `rows_after`: after the initial sync completes | reported |
| 2 | `inserted`, `superseded`, `skipped_before_horizon`, `unmatched_import_in_window`, `supersede_conflicts` | conflicts = 0 |
| 3 | **Duplicates created:** live pairs (connector row, import row) on the account with equal amount and dates within 3 days (ADR 0035 `d`). The connector row is on or after `connector_since`; the import row can have any date, so pairs across the horizon boundary count | **0** |
| 4 | Latest `sync_run` succeeded with `history_complete` | yes |
| 5 | A second "Sync now" | 0 rows added, modified or superseded |
| 6 | **Balance agreement:** expected = closing balance of the last reconciled statement + Σ live posted rows with `posted_date > H`. Compare with the connector's `current` (Holdfast sign). An account with no reconciled statement reports `n/a` | within $0.01, or within $0.01 once live pending rows are included, and the report says which |
| 7 | Slot ledger | exactly one new `linked` row. The owner confirms the dashboard shows the same count |

Any failure fails M-live. The fix is un-adopt (ADR 0033 §5), a code fix, then another preview. No
new Item is needed.

### 6.5 File ownership per run
| Slice | Owns |
|---|---|
| H2 | `docker-compose.instance.yml`, `docker-compose.sandbox.yml` (new), `Makefile` (targets), `scripts/check-compose-ports.py`, `scripts/dev.ps1`, `docs/ops/instances.md`, `backend/app/config.py` (startup refusal only) |
| H1 | `scripts/backup.sh`, `scripts/restore.sh`, `scripts/tests/test_backup*.py`, `docs/ops/backup.md` |
| P1 | `backend/app/domain/connector.py`, `backend/app/integrations/{base,registry,fake}.py`, tests |
| P2a | `backend/app/services/ledger_service.py`, `backend/app/domain/ledger_rules.py`, `backend/tests/ledger/test_ledger_sync_verbs.py`, `backend/tests/test_ledger_single_writer.py` |
| P2b | `backend/app/services/ingest.py`, `backend/app/domain/supersede.py`, `backend/tests/services/test_ingest*.py`, `backend/tests/domain/test_supersede*.py`, `backend/tests/fixtures/supersede/*.sanitized.*` |
| P4a | `backend/migrations/versions/<n>_connector_link.py`, `backend/app/models/{accounts,connectors,imports}.py`, `backend/app/models/{__init__,_common}.py`, `backend/app/services/statement_accounts.py` (new), `backend/app/services/statement_import.py`, `backend/tests/models/test_connector_link*.py`, `backend/tests/test_migrations.py` |
| P4 | `backend/app/integrations/plaid/{client,link,mapping}.py`, `backend/app/services/{plaid_link,plaid_slots,connections,connection_preview}.py`, `backend/app/services/plaid_setup.py`, `backend/app/jobs/handlers/connection_preview.py`, `backend/app/api/routes_connections.py`, `backend/app/schemas/connections.py`, `backend/tests/api/test_connections*.py`, `backend/tests/services/test_plaid_slots.py`, `web/src/api/schema.d.ts` (generated) |
| P5 | `backend/app/integrations/plaid/{sync,mapping}.py`, `backend/app/jobs/handlers/plaid_sync.py`, `backend/app/services/sync.py`, `scripts/record_plaid_sandbox.py`, `backend/tests/integrations/plaid/**`, `backend/tests/fixtures/plaid/**` |
| V1 | `backend/app/tools/connector_audit.py` (new package `app/tools/`), `backend/tests/tools/test_connector_audit.py` |
| P9b-min | `web/src/pages/settings/Connections*.tsx`, `web/src/pages/settings/plaidCopy.ts`, `web/src/features/connections/**`, `web/src/api/{plaid,connections}.ts`, `web/src/mocks/{plaid,connections,handlers}.ts`, `web/src/app/{routes,lazyRoutes}.tsx` |
| K1 | `backend/app/security/secrets.py`, `backend/app/services/secret_settings.py`, `backend/app/api/routes_settings_secrets.py`, `backend/app/schemas/settings.py`, `backend/app/services/plaid_setup.py`, tests, `docs/adr/0015-*.md` and `docs/adr/0023-*.md` (status lines), `docs/setup/plaid.md` |

P4 and P5 both edit `integrations/plaid/mapping.py`, so P5 starts after P4 merges. P2b and P4a both
touch the matcher: P4a only calls it.

---

## 7. Open PRs and branches: what happens, in order
1. **PR #56: merged** (`6fea745`). Its follow-ups belong to P4:
   - `optional_products`
   - the slot count
   - the product warnings
2. **PR #31: land first** (L1). Full Bulwark review. Rebase once. Fix the stale ADR 0015 paragraph.
   It must merge before P4, so the first access token is sealed as `hf1`.
3. **PR #32: land second** (L2). Rebase once onto main after #31, resolving `worker/main.py`. It
   must merge before P4, the first route that enqueues a job.
4. **Branch `feat/P9a-plaid-setup-page`: rework into P9b-min.**
   - Cherry-pick `d973679` onto main. `6b8af28` is superseded by `6fea745`.
   - Rewrite the copy for the dedicated instance and the link plan.
   - Push as `feat/P9b-connections-min`. The key-entry form waits for K1.
5. **Branch `feat/E10a-crypto-track`: rework later** (X1). Renumber the ADR and migration first.

---

## 8. Carry-over money rules, mapped to the slice that enforces each

| Rule | Enforced by | Evidence |
|---|---|---|
| Dedupe Plaid against imported rows; Plaid wins. **Amended by ADR 0035**: horizon, plus amount with a 3-day window against `posted_date`/`authorized_date`, plus a deterministic tie-break | **P2b** (matcher), **P4a** (statement-side call, horizon column), **P4** (adoption brings both into one account) | ADR 0035 tests. Sanitized fixture. §6.4 measure 3 |
| Import fingerprint with a same-day ordinal | already on main (I1). Used as the ADR 0035 tie-break key | golden tests |
| Pending→posted via `pending_external_id` | **P2a**, **P5**. S1 is posted-only | fixture keeps category and notes |
| `tx_cursor` only after the batch commits | **P5** | kill-between-pages test |
| Upsert on `(account_id, external_id)`, handle `removed` | **P2a**, **P2b**, **P5** | replay-twice test |
| `parse_float=Decimal` | merged (#56). **S1** too | grep test |
| Negate Plaid signs once in `integrations/plaid/`, a test per product | **P5**, **P6** (`mapping.py` is the only site) | sign tests. Grep test |
| API amounts are strings, with an OpenAPI test | merged (F3). Every new schema uses `MoneyStr` | OpenAPI hash |
| Never sum across currencies | **P2b**, **S1**, **P8** | custom-currency fixture |
| Splits through one domain service | C1 merged. **P2a** moves splits only inside `LedgerWriter` | single-writer lint |
| Transfer pairing service-enforced | C4 merged. **P2a**'s supersede keeps pair rules. **P5** enqueues `ledger.refresh` | per-field tests |
| Metrics: `COALESCE(splits, parent)`, exclude `is_transfer` and superseded rows | Phase 5 **B2**. Supersede moves splits and transfer state to the live row, which keeps this rule correct | B2 |

---

## 9. Secrets per connector (ADR 0015, amended by ADR 0032)

| Secret | Stored | Entered by | Notes |
|---|---|---|---|
| Plaid trio (client id, sandbox secret, production secret), one **group** | M-live: the dedicated instance's env file only. After K1: env **or** `household_setting`, the whole group from one source, both = conflict | owner | `PLAID_ENV` stays in env |
| Plaid access token | `bank_connection.access_token_enc` (`hf1`) | exchange | committed before `/accounts/get` |
| `public_token`, Link token | never (`link_token_fp` only) | Plaid Link | — |
| Plaid cursor | `tx_cursor`, plaintext | P5 | ADR 0017 |
| SimpleFIN setup token | never | owner | one-time |
| SimpleFIN access URL | `bank_connection.access_token_enc` | claim | not a `get_secret` name |
| Crypto endpoint URL | `household_setting` (encrypted) | owner | X1 decides the allowlist |
| Exchange keys | `exchange_account.*_enc` | owner | read-only. A new column needs a rotator ADR |
| AI keys | as for the Plaid group | owner | Phase 8 |

**In-app Plaid key entry: options and choice** (ADR 0032):
- (A) `.env` plus a prompt. Rejected by the owner.
- (B) The row overrides env.
- (C) Env falls back to the row.
- (D) **One source per key group; both = conflict.** Chosen.
- (E) The API writes `.env`.

D keeps ADR 0015's rule that no source silently replaces or fills in another, needs no restart for
in-app changes, and reuses a column the rotator already covers. Scheduled after M-live: the first
link uses the dedicated instance's env file.

---

## 10. Crypto 10a (slice level) and the custody rules

X1 brings `feat/E10a-crypto-track` onto main as PRs of at most M each:
1. schema
2. pure domain and the guard
3. adapters (Electrum, EVM RPC, fake)
4. service and routes

The ADR and migration are renumbered first. X2 adds CCXT with read-only keys. Crypto reaches net
worth through Phase 5 views, never through `IngestService`.

Hard rule 11, gated by Cipher on every X PR:
- No seed phrase or private key is ever accepted, stored, logged or transmitted. A pure guard
  rejects `xprv`-family keys, WIF, 64-hex keys and word lists before any parser runs, with a fixed
  error.
- Only xpubs, descriptors and addresses are stored, and `CHECK (is_watch_only)` stays until 10c.
- No spend, PSBT or signer code. `FEATURE_CRYPTO_SPEND=false`, self-host only.
- Exchange keys must be read-only. Trade or withdraw rights are refused, or a blocking warning is
  shown where permissions aren't reported. Keys are sealed.
- No public chain endpoint without a per-chain opt-in and the privacy warning. No default URL.

---

## 11. Fastest path for the owner's own instance

**Now, with what is merged:** statement PDFs through the Import wizard (6 layouts), the review
queue and the Transactions screen.
- Today this runs on the dev stack at `https://localhost`.
- Once H2 merges, real data moves to `holdfast-instance`. That means re-importing from the private
  directory; the dev stack's data is not migrated.

**Next:**
- CSV/OFX when I2b lands on P4a's resolver.
- Plaid M-live after §6.1, which is 6–8 windows plus the owner's preconditions.

**Waits:**
- after M-live: K1, P6 (investments and liabilities), P7 (polling; until then "Sync now"), M1
- SimpleFIN, only if subscribed
- I3, crypto (X1)
- Phase 6 credit, Phase 7 tax

**The phone** is outside this lane: ADR 0021 PA1, or the smaller manual host path PA-D9 (Harbor,
Bulwark). Keel can pull it forward.
- The owner installs Tailscale on the host and the phone.
- Until PA-D1 there is one RP id, so passkeys are registered at the tailnet name, and the PC uses
  that name too.
- An ad-hoc `tailscale serve` in front of Caddy is not recommended: every phone request would look
  like `127.0.0.1`.

---

## 12. Owner questions and actions

**Questions only the owner can answer:**
1. **Is the Plaid Trial approved for production?** Does the dashboard list Transactions, Liabilities
   and Investments? Is the institution you'd link first an OAuth institution? OAuth institutions may
   need the company profile and per-bank registration completed before production Link works
   (**VERIFY**). Sandbox can't show this.
2. **The slot plan (PLAN 3.2), and the first institution.** Path A (default) is a bank or card login
   with imported statements, linked after a clean preview. Path B is a 401(k), HSA or brokerage
   login, with holdings only at first (§6.3). **!** Each production Item is one of 10 lifetime
   slots.
3. **SimpleFIN Bridge: subscribe or not?** ($15/year.) Only worthwhile if your plan exceeds 10
   logins or an institution is missing from Plaid. ADR 0030 stays Proposed until you answer.
4. **Which machine and directory hosts `holdfast-instance`?** This is a precondition for SBX and
   M-live, not a later question.
5. **Sandbox keys for `holdfast-sandbox` and R1.** Will you paste them into that project's env file
   and run the recording script, or add them as a GitHub Actions secret? Agents never read env files.
6. **Backups:** where off the machine do the dumps go, and which password manager holds
   `FERNET_KEY`?
7. **Phone:** Tailscale on the host and phone, passkey at the tailnet name, and optionally the OAuth
   redirect URI in Plaid's dashboard.
8. **Crypto:** your own Electrum or EVM node, the public-endpoint privacy trade-off, and exchanges.
9. **After M-live:** move the Plaid keys in-app (K1) and delete them from the env file?

**Owner actions before M-live (S11):**
1. Run H2's `instance-up` on the chosen machine, and enrol a passkey or TOTP there (step-up needs
   it).
2. In that instance's env file, set the production keys and `PLAID_ENV=production`, and **delete**
   the `PLAID_PRODUCTS` and `PLAID_TX_DAYS_REQUESTED` lines. Then
   `docker compose up -d --force-recreate api worker` for that project.
3. Remove the production keys from the dev checkout's `.env` (owner-queue item 1).
4. Take a backup (H1) and store `FERNET_KEY` separately. Take another right after the link.
5. Before the link: read the production Item count in Plaid's dashboard and type it into the
   link-plan dialog. Check the institution supports Transactions.
6. At the mapping step: choose the owner and visibility for each account, and adopt only when the
   preview's evidence agrees.

---

## 13. Out of this contract
Plaid webhooks and Funnel. The Statements product. An in-app Sandbox/Production switch. Hosted-mode
shared keys across households. Phase 6, Phase 7, 10b and 10c. Plugin entry points. Description
similarity in matching (I4).

---

## 14. Review responses (Gadfly, `docs/phases/reviews/PHASE-2-contract-gadfly.md`)

Gadfly's factual claims were checked against the code before building on them:
- The card profile captures the first of two printed dates (lines 20 and 23), and interest takes
  the period end (line 3).
- The import key is `profile:last4:block`.
- `.env.example` line 62 lists `auth` and `statements`, and compose passes both variables through.
- `link_token_create` has no `optional_products`.
- `scripts/backup.sh` exits 1.
- `pair_transfers` skips superseded rows.
- `_require_live` refuses edits to superseded rows.

The owner's `.env` was not read. B4 is reasoned from `.env.example`, compose and `config.py`. The
claim that `make keys` copied the example into the owner's `.env` is consistent with
`gen-keys.py --write` but is unverified. The fix doesn't depend on it.

### Blockers
| # | Accepted | How it is closed | Where |
|---|---|---|---|
| B1 | yes (Keel ruling) | Statement horizon `connector_since`, fixed at adoption. Amount plus `d <= 3` against `posted_date` or `authorized_date`. A deterministic greedy tie-break with the two-coffees case stated. A sanitized trans≠post fixture gates P2b. Keel ruled "deterministic tie-break" over Gadfly's "ties go to review" | ADR 0035, §3.4, §8 |
| B2 | yes | Dry-run preview job with evidence counts. Ranked (not filtered) candidates. `adopt`, `new` or `ignore`. `close` for twins. Adopt refused without a fresh preview. Un-adopt in P4 with a reverse trigger path | ADR 0033 rev. 2, §5.2, §6.1 P4 |
| B3 | yes | The full supersede clause and its reverse. Steps named per slice (P2a, C3b, C4c, C4d, C5b). The note for PR #59 is in `~/.local/state/holdfast-keel/supersede-clause.md` | §3.3 |
| B4 | yes (Keel ruling) | Products built from `login_kind`. Env deviations block until the long phrase names the effective products and days. `auth` and `statements` refused. Setup check warns. `optional_products` added to the client. Owner action to delete both lines. **Narrowing:** `auth` is refused outright, not just confirmable, under the data-minimization rule | §5.1, ADR 0034 §2, §12 |
| B5 | yes (Keel ruling) | SBX runs only in `holdfast-sandbox` (H2). A sandbox token on a household with real data gives `409`. A sandbox connection can never adopt: trigger, candidate query and supersede precondition, each tested. Q4 is a precondition | §2 D12–D13, §6.1, ADR 0033 §6 |
| B6 | yes (Keel ruling) | V1 audit. Counts only, run by Sounding under ADR 0019: rows before and after, duplicates created = 0, idempotent second sync, balance agreement within $0.01, slot ledger versus dashboard | §6.4 |
| B7 | yes (Keel ruling) | Both orders presented with trade-offs. Default A: a statement-backed login after a clean preview, with a stated threshold. B is planned only if Q2 picks it | §6.3, Q2 |
| B8 | yes (Keel ruling) | The restore, fresh-install and new-key outcomes stated plainly. Ledger keyed by client with a count function. Dashboard count typed on every production link. `tokens_unreadable` and `ledger_orphans` checks. Backup items and the H1 drill are preconditions | ADR 0034 rev. 2 §4–§5, §6.1 H1 |

### Should-fixes
| # | Response |
|---|---|
| S1 | **Accepted.** K1 and the key form move after M-live. P9b is cut to P9b-min. The path is 10 build slices plus 2 landings (H1, H2 and V1 were added by B5, B6 and B8) |
| S2 | **Accepted.** Fixed token order and file orders in §4, shared with Phase 4 through the note. P2b's statement-import call moves into P4a |
| S3 | **Accepted.** P4a extracts `statement_accounts.py`. I2b must use it, and P4a switches I2b if I2b lands first |
| S4 | **Accepted.** `/link/token/get` resolution (**VERIFY** on Trial, with a fallback rule). Idempotent exchange. Commit before `/accounts/get`. `onExit` only triggers a check. Duplicate-login list required. The Trial-limit code and where it surfaces are **VERIFY** |
| S5 | **Accepted.** The trio is one key group. `client_fp` compared at use time (P4, P5). A conflict is one banner and leaves connection status alone. The recreate step is in the copy and owner actions |
| S6 | **Accepted.** Unseen accounts go to `pending` in `connector_account_link`. Adopting an account with another connection's live rows is refused until P8 defines replacement, and P8's text no longer claims `supersede` covers it |
| S7 | **Accepted.** C3e adds inline rules to `IngestService`, with a test (P2b lands first by the §4 order) |
| S8 | **Accepted, changed.** P5 merges on hand-built fixtures. R1, the recording script, ships in P5. Recorded output is required before SBX, not before P5. Q5 asks for the keys |
| S9 | **Accepted.** S1 moves after P6, P7 and M1, and only if the owner subscribes. The backfill walks 8 windows regardless of empty windows. Status is per `conn_id`. Q3 is reworded. ADR 0030 is Proposed |
| S10 | **Accepted.** 6–8 windows. Rebase once, review once. Stale SHAs removed (#56 is merged) |
| S11 | **Accepted.** All seven actions are in §12. The passkey enrolment and the dashboard read are tied to H2 and the link-plan dialog |
