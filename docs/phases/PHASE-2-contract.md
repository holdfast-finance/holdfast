# Phase 2: connector-lane contract (every source into the owner's own instance)

Spec: PLAN 3, 7, 13 Phase 2 and 3, and 20 (Section 20 wins). ROADMAP E2 (P1 to P10), S1, E3 (I1
to I6) and 10a. ADRs 0007, 0008, 0011, 0015, 0016, 0017, 0019, 0021, 0022 and 0023 (PR #56), plus
the new ADRs **0030** (SimpleFIN), **0032** (in-app key entry), **0033** (linked accounts adopt
imported history) and **0034** (Plaid slot ledger). 0031 is taken by `docs/status-refresh-2026-11-21`.
0023 and 0025 belong to the Plaid lane, and 0024 and 0026 to 0029 to PR #59.

This file is binding for every connector-lane story. A builder who must deviate says so in the PR
body and asks Lattice for an amendment, which lands here before the code merges.

**Goal (owner, 2026-11-21):** "hook up every connector with our data" and view it on the phone.

**Milestone M-live:** the owner links one real institution through Plaid on their own instance and
sees its transactions and balances. M-live is reached on the PC. The phone needs the ADR 0021 lane
(section 10). PLAN 13 Phase 2 acceptance, which covers sandbox Items, relink and cursor restart, is
still P10's job.

---

## 0. What exists (checked on `origin/main` at `733132e`, and on every branch)

| Piece | State | Where |
|---|---|---|
| F2 jobs (registry, IDs-only payloads, per-household lock, `job_run`) | merged | `app/jobs/`, ADR 0016 |
| F2b job hardening: confused deputy and stuck `running` ("must land before any API route enqueues jobs") | **PR #32, open** | `fix/F2b-job-hardening` |
| F5a `SecretToken` | merged | `app/security/secret_token.py` |
| F5c resolver `get_secret`, `hf1` envelope, `rotate-keys`, outbound SSRF validator, migration `0010_list_household_ids` | **PR #31, open**, CI green, **no review yet** | `feat/F5c-secret-resolver-rotation` |
| F6 `import_run`, `sync_run`, `superseded_by_id` | merged | `models/imports.py`, ADR 0017 |
| `bank_connection`, `account`, `balance_snapshot`, holdings, investment txns, liabilities | merged (schema only) | `models/accounts.py`, `models/investments.py` |
| Statement PDF import: upload API (21 MiB), reconcile, review reasons, wizard, review queue, Transactions screen | merged | `services/{imports,statement_import}.py`, `web/src/pages/{import,transactions}` |
| PDF bank profiles | merged: 6 layouts, 2 institutions | `app/importers/profiles/` |
| CSV/OFX parsers (pure) | merged (I2a) | `domain/{csv_parse,ofx_parse}.py` |
| CSV/OFX import into the ledger | **I2b in progress** (branch, no commits yet) | `feat/I2b-csv-ofx-import` |
| `LedgerWriter`, the one writer of money fields (insert with `skip_duplicates` only) | merged (C1) | `services/ledger_service.py` |
| Category tree and Plaid PFC mapping | merged (C2) | ADR 0022 |
| `app/integrations/plaid/` | **empty stub** on main | — |
| P3 Plaid client (`httpx`, `parse_float=Decimal`, `request_id`, no `error_message`), setup checklist API, ADR 0023, `docs/setup/plaid.md` | **PR #56, open**. Bulwark APPROVE at `88dda90`; the head is now merge commit `cba7f7b`, so the sign-off is stale | `feat/P3-plaid-client-preflight` |
| P9a Settings, Connections page (checklist UI) | **local branch only**, one commit `d973679`, stacked on P3's *first* commit `6b8af28`. Its own message says "not pushed yet" because the copy assumes `.env` keys | `feat/P9a-plaid-setup-page` |
| **P1** `BankConnector`, registry, `FakeConnector` | **does not exist** on any branch | — |
| **P2** `IngestService` | **does not exist** on any branch | — |
| Cross-source supersession (Plaid wins) | **not built**. No code writes `superseded_by_id` | — |
| SimpleFIN | not started. `Settings.simplefin_access_url` and the allowlist name exist | — |
| Crypto Track (watch-only BTC and EVM, ChainSource, schema) | **local branch only**, 7 commits, base `1ddbd8e`. Its ADR is numbered 0020 and its migration 0007, and **both collide** with main | `feat/E10a-crypto-track` |
| Manual accounts | schema allows `source = 'manual'`. No API, no UI | — |
| Competitor exports, credit reports, tax documents | not started | — |
| Phone access (ADR 0021 PA1, PA-D*) | design only (ADR 0021 Proposed) | — |

**What skipping P1 and P2 costs.** The Plaid lane went straight from the client to the setup check.
That was harmless so far, because nothing ingests yet. Building sync without P1 and P2 would cost
three things:
1. Upsert, pending-to-posted, `removed` and Plaid-wins logic would be written inside the Plaid sync
   job and then again for SimpleFIN. That duplicates the money paths the carry-over rules exist to
   keep single.
2. There would be no cross-source dedupe. The owner has imported statements (ADR 0019) and will
   link the same banks, so every overlap month would show twice.
3. There would be no fake connector, so P5's job tests would need recorded Plaid traffic for every
   case.

**Ruling: P1 and P2 are built before the first live sync, not after.** P1 is S and runs in
parallel with the review-only PR landings. P2 is split into P2a (Ledger) and P2b (Tide) so neither
is L. Statement import is not rewritten onto `IngestService`. It keeps its fingerprint path and only
gains the reverse supersession call (P2b).

---

## 1. Every source the product plans

Cost markers: **$** costs the owner money. **!** has an irreversible external effect.

| Source | State | Depends on | Owner must supply |
|---|---|---|---|
| **Plaid Transactions** | client and setup check in PR #56. Sync not started (P5) | L1, L2, L3, P1, P2a, P2b, P4a, P4, P5, P9b | Plaid account with **Trial approved** for production (free). Production client id and secret, entered in-app after K1 or kept in `.env` until then. The 10-slot plan (PLAN 3.2). **! Each production Item uses one of 10 lifetime slots. Deleting it does not return the slot.** Clicking Link and signing in to the bank (MFA) |
| **Plaid Balances** | not started. From the `/transactions/sync` account block in P5; daily `/accounts/balance/get` in P6 | P5 | nothing beyond the Item |
| **Plaid Investments** (401(k), HSA, brokerage) | not started (P6) | P5 | which login holds the 401(k) and which custodian holds the HSA (PLAN 3.1). **!** one slot per institution login |
| **Plaid Liabilities** (cards, loans) | not started (P6) | P5 | nothing beyond the Item. The product must be on the Trial bundle (ADR 0023 lists it) |
| **SimpleFIN** | not started (S1, ADR 0030) | P1, P2a, P2b, P5 | **$** a SimpleFIN Bridge subscription ($1.50/month or $15/year plus tax, up to 25 institutions). A setup token pasted in-app |
| **Statement PDF** | **merged** (6 layouts, 2 institutions). Other layouts need a profile (`bank-profile` skill) | — | the PDFs, kept outside the repo (ADR 0019). Upload them through the Import wizard |
| **CSV / OFX / QFX** | parsers merged. Import in progress (I2b) | I2b | bank-site downloads |
| **Competitor exports** (Mint, YNAB, Monarch, Copilot) | not started (I3) | I2b | the export files, if any exist |
| **Credit-report PDF** | not started (Phase 6, Q2) | Phase 6 | three AnnualCreditReport.com PDFs (free) |
| **Crypto, watch-only**: xpub, descriptor, address | local branch `feat/E10a-crypto-track`, not a PR | X1 rebase, Cipher and Bulwark gates | public xpubs or descriptors exported from the hardware wallet, and addresses. An Electrum server (electrs or Fulcrum) and/or an EVM JSON-RPC endpoint the owner runs. Public endpoints are deferred and need the owner's opt-in to a privacy cost. **Never a seed or private key** |
| **Crypto, read-only exchange key** (CCXT) | not started | X1, then X2 | a **read-only** API key per exchange. Keys with trade or withdraw rights are refused |
| **Manual accounts** (balance entry) | not started (M1) | — | account names and balances, typed in. A cheap way to cover low-activity accounts without spending a Plaid slot |
| **Tax documents** (W-2, 1099s, 1098, 5498-SA) | not started (Phase 7, `FEATURE_TAX`) | Phase 7 | the forms, kept outside the repo. Last year's return for validation |

---

## 2. Decisions at a glance

| # | Question | Decision | Record |
|---|---|---|---|
| D1 | P1 and P2 before or after the first live sync | Before. P2 is split into P2a (writer verbs) and P2b (ingest) | §0 |
| D2 | How Plaid keys get in | In-app entry. Exactly one source per key: env only, row only, or **conflict** when both exist. `PLAID_ENV` stays in `.env` | ADR 0032 |
| D3 | Imported history versus linked accounts | At link time the person maps each connector account to an imported account or to "new". Adoption keeps the row. `account.statement_key` keeps statement import pointed at it | ADR 0033 |
| D4 | Slot guard | `plaid_link_slot` ledger. Reserve at Link-token time. No DELETE. Unknown counts as used | ADR 0034 |
| D5 | Plaid products requested at link | `products = ["transactions"]` for bank and card logins, `["investments"]` for retirement and HSA logins. `liabilities` and `investments` go through `optional_products` where the Trial allows it (**VERIFY** in P4 with the setup check's probe). **`auth` and `statements` are dropped from the defaults**: `auth` returns full account and routing numbers (data minimization), and statements are not needed for M-live | here |
| D6 | History depth | `transactions.days_requested = 730` on every new Item. Plaid fixes it when the Item is created, so it is **irreversible**. A test pins it | here |
| D7 | Cross-source dedupe for M-live | Exact multiset match on `(account_id, posted_date, amount)` between live connector rows and live import rows in the same account. Plaid wins. The fuzzy ±3-day match is I4, later | carry-over rule 1 |
| D8 | Sync shape | One job run = one full `/transactions/sync` pagination loop held in memory, applied in **one** DB transaction together with `tx_cursor` and the `sync_run` success | ADR 0017, carry-over rule 4 |
| D9 | Initial history without webhooks | P5 re-enqueues itself with backoff until `transactions_update_status = HISTORICAL_UPDATE_COMPLETE` (**VERIFY** the field against recorded sandbox output) | PLAN 7.4 (a) |
| D10 | SimpleFIN credential | Access URL in `bank_connection.access_token_enc`. It is not a `get_secret` name. Setup token transient. Posted-only | ADR 0030 |
| D11 | Link from the phone | Not for M-live. Link from the desktop browser (OAuth runs in a pop-up, so no redirect URI is needed). Phone Link needs an HTTPS redirect URI on the tailnet name, registered by the owner in Plaid's dashboard | ADR 0023, PLAN 7.3 |
| D12 | Who may link | The household owner role, with a step-up re-auth within 5 minutes, for new Items, relinks, SimpleFIN claims and key entry | `.claude/rules/security.md` |

---

## 3. Interfaces

### 3.1 Domain value types (P1): `backend/app/domain/connector.py`, pure
```python
@dataclass(frozen=True)
class ConnectorAccount:
    external_account_id: str
    name: str
    official_name: str | None
    mask: str | None                  # at most 4 digits; anything longer is cut to the last 4
    type: AccountType                 # depository | credit | loan | investment | other
    subtype: str | None
    currency: str | None              # ISO 4217; None = unsupported, never stored

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
Constructors refuse `float`, refuse amounts with more than 2 decimal places, and refuse a pending row
that carries `pending_external_id`.

### 3.2 Protocol and registry (P1): `backend/app/integrations/base.py`, `registry.py`, `fake.py`
```python
class BankConnector(Protocol):
    provider: ClassVar[str]                                   # "plaid" | "simplefin"
    async def begin_link(self, ctx: LinkContext) -> LinkStart # Plaid: link token; SimpleFIN: create URL
    async def complete_link(self, ctx: LinkContext, proof: SecretToken) -> LinkedConnection
    async def begin_relink(self, ctx: LinkContext, conn: ConnectionRef) -> LinkStart
    async def fetch(self, conn: ConnectionRef, credential: SecretToken,
                    cursor: str | None) -> SyncBatch          # one complete pagination run
    def health(self, state: ConnectionState, now: datetime) -> ConnectorHealth   # pure, no I/O
```
- `LinkedConnection` carries `external_id`, `credential: SecretToken` (the access token or access
  URL), `accounts`, `institution_name` and `client_fp`.
- Errors are a single `ConnectorError(kind, code, request_id)` with these kinds: `needs_relink`,
  `retryable`, `rate_limited`, `payment_required`, `revoked`, `client_changed` and `fatal`. It
  carries no provider message text.
- `registry.get(provider)` returns the connector. `registry.override(provider, impl)` exists for
  tests only and is a context manager.
- `FakeConnector` replays a scripted sequence of `SyncBatch` values and errors, including
  mutation-during-pagination.
- Routes and jobs depend on the protocol. Only `integrations/plaid/` and `integrations/simplefin/`
  know a provider's wire format.

### 3.3 Ledger writer sync verbs (P2a): `services/ledger_service.py`, Ledger's lane
```python
async def upsert_external(self, new: NewTransaction) -> UpsertOutcome   # inserted | updated | unchanged
async def remove_external(self, account_id: UUID, external_id: str) -> bool
async def post_pending(self, pending_external_id: str, posted: NewTransaction) -> UUID
async def supersede(self, loser_id: UUID, winner_id: UUID) -> None
```
- **`upsert_external`** matches on `(account_id, external_id)`. An update may change `amount`
  (through `validate_amount`), the dates, the description, the merchant, the PFC fields and
  `is_pending`. It never changes a category whose source is `user` or `rule`. When the amount
  changes and splits exist, the splits are cleared and the outcome reports `splits_cleared`.
- **`post_pending`** inserts the posted row and deletes the pending row in one unit of work. It
  carries over the pending row's category (when the source outranks the posted row's), `notes` and
  splits (only when the amount is unchanged).
- **`supersede`** requires the same account, a live winner, a winner with a connector source and a
  loser with `source = 'import'`. The winner takes the loser's category when the loser's source
  outranks it, plus the loser's notes when the winner has none. The ADR 0017 triggers still guard
  one-to-one and no chains.
- None of these verbs is audited per row. The `sync_run` is the record (C1 docstring).
- **File conflict:** Phase 4's C3b and C4c also edit `ledger_service.py`. Keel serializes P2a with
  them, and whichever lands second rebases.

### 3.4 IngestService (P2b): `backend/app/services/ingest.py`
`IngestService(session).apply(connection_id, batch, sync_run_id) -> IngestCounts` runs in the job's
household-wide session, inside the caller's single transaction. Steps:
1. **Accounts.** Resolve each `ConnectorAccount` by `(bank_connection_id, external_account_id)`. A
   new account is created only when the connection has a confirmed mapping (ADR 0033). An unmapped
   account is skipped and counted. `currency is None` is skipped and counted as
   `unsupported_currency`.
2. **Balances.** Upsert `balance_snapshot` on `(account_id, as_of_date)`. Never sum.
3. **`removed`** goes to `remove_external`.
4. **`modified`, then `added`** go to `upsert_external`. A posted row with `pending_external_id`
   that matches a live pending row goes to `post_pending` instead.
5. **Categories.** Plaid rows get the ADR 0022 mapping as `category_source = 'plaid'`. When C3e has
   merged, rules run inline first (ADR 0029 §1).
6. **Supersession.** Pure `app/domain/supersede.py`, `match(connector_rows, import_rows) ->
   list[(loser, winner)]`. For every `(account_id, posted_date, amount)` key it pairs the k-th
   unmatched live import row with the k-th unmatched live posted connector row. Import rows are
   ordered by `(import_fingerprint, id)` and connector rows by `(external_id)`. Pending rows never
   take part. Each pair goes to `supersede`.

`statement_import._import_block` calls the same matcher after inserting a block, so a statement
imported after a sync is superseded by the connector rows already there. P2b owns that one call.

**Invariants (property tests):**
- Applying the same batch twice changes nothing.
- 100 random interleavings of import blocks and sync batches over one synthetic month reach the
  same end state. That means the same live row multiset per account and no live duplicate on
  `(account_id, posted_date, amount)` beyond the true multiplicity.
- No row is both superseded and a winner.

---

## 4. Schema: one migration in this lane before M-live

The builder holding the token numbers it from the head at build time (Phase 4 contract §3 rule).
The order is: PR #31's `0010_list_household_ids` (already numbered), then **P4a
`<n>_connector_link`**. Phase 4's M1 to M4 may land between them. That changes numbers, not
content. Conventions follow the Phase 1 contract and the migrations rule. Up/down/up as
`holdfast_owner`, and the RLS lint applies.

**P4a `<n>_connector_link`:**
1. `plaid_link_slot`, exactly as ADR 0034 §1: forced RLS, `household_isolation`, grants
   SELECT/INSERT/UPDATE to `holdfast_app` with no DELETE, a transition trigger, a partial unique
   index on one `reserved` row per `(household_id, client_fp, env)`, and an index on `household_id`.
2. `bank_connection.client_fp text NULL` (16 lowercase hex characters, CHECK).
   `bank_connection.status` gains `pending_setup`.
3. `account.statement_key text NULL`, backfilled from `external_account_id` for
   `source = 'import'`. `UNIQUE (household_id, statement_key) WHERE statement_key IS NOT NULL`
   replaces `uq_account_household_id_external_account_id_import`. Downgrade restores the old index
   from `statement_key`.
4. The adoption trigger (ADR 0033 §4): `source` changes only `import` to `plaid`/`simplefin`, only
   with a non-null `bank_connection_id` set in the same UPDATE, and never back.
5. Same PR: `statement_import._account()` switches to `statement_key`, and its tests change.

No other slice before M-live needs the token. P6 uses the existing `security`, `holding`,
`investment_txn` and `liability` tables. If the P6 builder finds a missing unique key, that is a
separate token request.

---

## 5. API (all under `/api`, JSON, CSRF header, errors `{"error": {"code", "message"}}`)

| Method and path | Who | Body to response | Slice |
|---|---|---|---|
| GET `/settings/secrets` | owner | `{items: [{name, state: not_set\|set_in_app\|set_in_env\|conflict}]}` | K1 |
| PUT, DELETE `/settings/secrets/{name}` | owner + step-up | `{value}` → 204. `409 managed_in_env` | K1 |
| GET `/plaid/setup` · POST `/plaid/setup/check` | owner | ADR 0023 checklist. `item_slots` switches to the ADR 0034 count in P4 | #56, P4 |
| POST `/connections/plaid/link-token` | owner + step-up | `{mode: new\|update, connection_id?, confirm_phrase?}` → `{link_token, expiration, reservation_id?, slots: {used, limit: 10}}`. `409 confirm_required\|no_slots_left` | P4 |
| POST `/connections/plaid/exchange` | owner | `{public_token, reservation_id}` → `{connection, accounts: [{external_account_id, name, mask, type, candidates: [{account_id, name, mask}]}]}`. Status `pending_setup` | P4 |
| POST `/connections/plaid/reservations/{id}/exit` · `/abandon` | owner (abandon: step-up + phrase) | → 204 | P4 |
| POST `/connections/{id}/accounts` | owner | `{mappings: [{external_account_id, adopt_account_id\|null, owner_person_id\|null, visibility}]}` → connection `active` and the initial sync enqueued | P4 |
| GET `/connections` | member | `[{id, provider, institution_name, status, error_code, last_success_at, stale, accounts: [...]}]` plus `plaid_slots` | P4 (P8 extends) |
| POST `/connections/{id}/sync` | owner | → `202 {job_run_id}`. Idempotency bucket of 15 minutes. SimpleFIN quota applies | P5 |
| POST `/connections/simplefin/claim` | owner + step-up | `{setup_token}` → first `{host}` (a preview, nothing claimed), then a second call `{setup_token, confirm: true}` → exchange-shaped response | S1 |
| GET `/sync-health` | member | per account: `status`, `last_success_at`, `stale` (> 48h), `error_code`, duplicate-cleanup counts. Amounts as strings | P8 |

`public_token`, `setup_token`, access tokens and access URLs never appear in a response, a log
line, an audit row, `job_run.payload` or a Redis key. A test greps captured logs for canaries of
each.

---

## 6. Slices, in dependency order (one PR each)

Kinds: **land** (an existing PR), **pure**, **db** (needs Postgres), **web**. Bulwark gates every
slice that touches auth, tokens, Plaid, `app/security/` or a migration. Ledger gates money paths,
Prism gates `web/`, and Sounding gates acceptance.

### 6.1 To M-live

| # | ID | Scope | Owner | Reviewers | Token | Depends | Acceptance evidence |
|---|---|---|---|---|---|---|---|
| 1 | **L1** | Land PR #31 (F5c). Fix its ADR 0015 text first: the "Amendment (F5c): builds without it" paragraph contradicts the shipped `0010_list_household_ids` migration | Harbor | **Bulwark** (protected paths), Ledger (migration) | **yes** (0010, already numbered; rebase only if the head moved) | — | `ci-required` green at head. SHA-bound Bulwark verdict. The ADR 0015 test list passes. Up/down/up |
| 2 | **L2** | Land PR #32 (F2b) | Lattice | **Bulwark** | no | — | Bulwark verdict at head. Confused-deputy and timeout tests |
| 3 | **L3** | Land PR #56 (P3) as is | Keel | **Bulwark** delta verdict at the merge head (`cba7f7b` or later). **Ledger** sign-off (lane reviewer: confirms no amount path) | no | — | Both verdicts name the head SHA. The `link_token_create` caller-pin test passes |
| 4 | **P1** | §3.1 and §3.2, `FakeConnector`, contract tests | Tide | Ledger, Bulwark | no | — (runs alongside L1 to L3) | The contract suite runs against `FakeConnector`. Value types refuse `float` and 3-decimal amounts. import-linter passes |
| 5 | **P2a** | §3.3 writer verbs + `test_ledger_single_writer` update | Ledger | Bulwark, Ledger (independent run) | no | P1 types only. Serialized with Phase 4 C3b and C4c | Table tests per verb. A user category survives `modified`. `post_pending` keeps notes and category. `supersede` refuses a cross-account pair, a chain or an import winner |
| 6 | **P2b** | §3.4 `IngestService`, `domain/supersede.py`, reverse call in statement import | Tide | **Ledger**, Bulwark | no | P1, P2a | The 100-interleaving property test. Replay twice gives zero writes. A statement-after-sync overlap supersedes. A currency-less account is skipped |
| 7 | **P4a** | §4 migration, models, `_account()` switch | Tide | **Bulwark** (RLS migration), Ledger | **yes** | L1 | RLS lint. DELETE as `holdfast_app` is refused. The transition trigger matrix. One reservation under two concurrent inserts. Backfill equality. Re-import of a statement after adoption lands on the same account. Up/down/up |
| 8 | **P4** | Link-token, exchange, reservations, account mapping (ADR 0033), guard (ADR 0034), D5/D6 product defaults (`config.py`, `.env.example`), `client_fp`, enqueue of the initial sync. Plaid wire mapping for `/accounts/get` in `integrations/plaid/` | Tide | **Bulwark**, Ledger | no | L1, L2, L3, P1, P4a | Guard at 7, 8, 9 and 10. Deletion doesn't lower the count. A relink reserves nothing. `days_requested = 730` pinned. Step-up is enforced. No token in logs (canary). Candidates exclude invisible accounts |
| 9 | **P5** | `plaid.sync_item` job: full pagination, mutation-restart, one-transaction apply + cursor + `sync_run`, balance snapshots from the sync response, follow-up enqueue until history is complete, `ledger.refresh` enqueue when registered (ADR 0029). Sign mapping for transactions and balances in `integrations/plaid/mapping.py` | Tide | **Ledger**, Bulwark | no | P2b, P4 | Recorded-fixture replay. A kill between pages, then resume, gives no duplicates and the old cursor. Mutation-during-pagination restarts from the saved cursor. Sign tests: debit, credit, card balance, loan balance. `removed` deletes. The pending→posted fixture |
| 10 | **K1** | ADR 0032: `get_secret` change, `SecretConflict`, `/settings/secrets`, `plaid_credentials()` moves to `get_secret`, setup check reports `conflict` | Harbor | **Bulwark** | no | L1, L3 | ADR 0032 consequence tests. `GET` never echoes a canary. `PUT` with env set gives `409`. Rotation re-seals the new rows |
| 11 | **P9b** | Rework of P9a: cherry-pick `d973679` onto main, copy for in-app keys, key-entry form (K1), Plaid Link button (Plaid's CDN script; the CSP already allows `cdn.plaid.com`), the account-mapping sheet, slot counter with typed phrase, connection list, "Sync now" | Prism | Prism (brand), Sounding, **Bulwark** (Link token handling in the browser) | no | P4 and K1 APIs (MSW mocks first) | 375 px screenshots. vitest: the phrase is required at 8 and the 11th is blocked in the UI. The mapping sheet never offers an account the API didn't return. No `/api` caching in the SW |
| 12 | **G1** | Sandbox gate: end-to-end on recorded fixtures (Sounding), and the owner's sandbox smoke on their instance (`user_good`, then `user_transactions_dynamic`) | Sounding, then the owner | Keel | no | 1–11 | `docs/phases/PHASE-2-gate.md` with counts only. A re-sync is idempotent. A statement month that overlaps a sandbox link shows once |
| — | **M-live** | **The owner** sets `PLAID_ENV=production`, enters production keys (K1) or leaves them in `.env`, links **one** institution from the desktop, maps its accounts, and checks transactions | owner | — | — | G1 | Owner says it works. Sounding records counts only (ADR 0019) |

**Estimate:** about 3 windows of agent time for rows 4–12, plus review latency on L1 to L3.
- Wave 1: L1, L2, L3 reviews, P1 and P2a.
- Wave 2: P2b, P4a and P9b against mocks.
- Wave 3: P4 and K1.
- Wave 4: P5.
- Then G1.

### 6.2 After M-live (all connectors)

| ID | Scope | Owner | Reviewers | Token | Depends | Acceptance |
|---|---|---|---|---|---|---|
| **P6** | Investments (holdings, investment txns, securities), liabilities, daily `/accounts/balance/get`. A sign test per product | Tide | Ledger, Bulwark | only if a key is missing | P5 | One fixture test per product. Holdings quantities are `Decimal(28,10)`. Liability magnitudes are non-negative |
| **P7** | `ITEM_LOGIN_REQUIRED` / `PENDING_EXPIRATION` → `needs_relink`, update-mode Link, backoff, a 6-hour poll cron at a per-connection minute, `scripts/seed_sandbox.py` (`/sandbox/public_token/create`) | Tide (+ Harbor for `scripts/`) | Bulwark | no | P5, L2 | A sandbox reset-login recovers in the same slot (fixture). The cron test uses a fake clock |
| **P8** | `/sync-health`, stale > 48h, relink duplicate cleanup through `supersede` (reversible) | Tide | Ledger, Bulwark | no | P5 | Stale flag unit test. Duplicates after relink merged and restorable |
| **P9c** | Sync Health page, relink button | Prism | Prism, Sounding | no | P8, P7 | 375 px. Relink opens update mode |
| **S1** | SimpleFIN connector (ADR 0030): claim, fetch, windows, quota, errlist, allowlist name removed from `secrets.py` | Tide | **Bulwark** (protected path), Ledger | no | P1, P2b, P5 | ADR 0030 test list. Fixtures plus the Bridge demo token recorded once |
| **I2b** | CSV/OFX import (in progress) | Tide | Ledger, Bulwark | per its brief | I2a | Re-import gives zero rows |
| **M1** | Manual accounts: create, edit, balance entry (`source = 'manual'`). Web form in the same PR | Ledger (+ Prism web) | Bulwark, Prism | no | — | Writes go through services only. Balance sign tests for an asset and a loan |
| **I3** | Mint, YNAB, Monarch and Copilot exports | Tide | Ledger | no | I2b | Each re-import gives zero rows |
| **I4** | Fuzzy overlap dedupe (±3 days, unique match only, otherwise review) | Tide | Ledger | maybe | P2b | Overlap fixtures |
| **X1** | Crypto Track branch: rebase onto main, renumber its ADR (next free number) and migration (next head at token time), split into ≤ M PRs | Cipher (build review) / Tide | **Cipher**, **Bulwark** | yes | Keel's schedule (ROADMAP puts 10a after Phase 5) | §9 rules. The branch's own tests at a fresh base |
| **P10** | Phase 2 threat model and acceptance | Bulwark, Sounding | — | no | P1–P9c, S1 | `docs/phases/PHASE-2.md` PASS |

### 6.3 File ownership per run

| Slice | Owns (creates or edits) |
|---|---|
| P1 | `backend/app/domain/connector.py`, `backend/app/integrations/{base,registry,fake}.py`, `backend/tests/integrations/test_connector_contract.py`, `backend/tests/domain/test_connector_types.py` |
| P2a | `backend/app/services/ledger_service.py`, `backend/app/domain/ledger_rules.py`, `backend/tests/ledger/test_ledger_sync_verbs.py`, `backend/tests/test_ledger_single_writer.py` |
| P2b | `backend/app/services/ingest.py`, `backend/app/domain/supersede.py`, `backend/app/services/statement_import.py` (one call), `backend/tests/services/test_ingest*.py`, `backend/tests/domain/test_supersede.py` |
| P4a | `backend/migrations/versions/<n>_connector_link.py`, `backend/app/models/{accounts,connectors}.py`, `backend/app/models/__init__.py`, `backend/app/models/_common.py`, `backend/app/services/statement_import.py` (`_account`), `backend/tests/models/test_connector_link*.py`, `backend/tests/test_migrations.py` |
| P4 | `backend/app/integrations/plaid/{link,mapping}.py`, `backend/app/services/{plaid_link,plaid_slots,connections}.py`, `backend/app/services/plaid_setup.py` (slot step), `backend/app/api/routes_connections.py`, `backend/app/schemas/connections.py`, `backend/app/config.py` (product defaults), `.env.example`, `backend/tests/api/test_connections*.py`, `backend/tests/services/test_plaid_slots.py`, `web/src/api/schema.d.ts` (generated) |
| P5 | `backend/app/integrations/plaid/{sync,mapping}.py`, `backend/app/jobs/handlers/plaid_sync.py`, `backend/app/services/sync.py`, `backend/tests/integrations/plaid/**`, `backend/tests/fixtures/plaid/**` (synthetic) |
| K1 | `backend/app/security/secrets.py`, `backend/app/services/secret_settings.py`, `backend/app/api/routes_settings_secrets.py`, `backend/app/schemas/settings.py`, `backend/app/services/plaid_setup.py` (`plaid_credentials`), `backend/tests/test_secrets.py`, `backend/tests/api/test_settings_secrets.py`, `docs/adr/0015-*.md` and `docs/adr/0023-*.md` (status lines), `docs/setup/plaid.md` |
| P9b | `web/src/pages/settings/Connections*.tsx`, `web/src/pages/settings/plaidCopy.ts`, `web/src/features/connections/**` (new), `web/src/api/{plaid,connections,settings}.ts`, `web/src/mocks/{plaid,connections}.ts`, `web/src/mocks/handlers.ts`, `web/src/app/{routes,lazyRoutes}.tsx`, `web/package.json` and lockfile (only if a Link wrapper dependency is added; MIT license and maintenance checked) |
| S1 | `backend/app/integrations/simplefin/**`, `backend/app/jobs/handlers/simplefin_sync.py`, `backend/app/services/simplefin_link.py`, `backend/app/api/routes_connections.py` (claim route), `backend/app/security/secrets.py` (remove one name), `backend/app/config.py`, `.env.example`, `backend/tests/integrations/simplefin/**` |

P4 and S1 both edit `routes_connections.py`. S1 starts after P4 merges.

---

## 7. PR #56, PR #31, the P9a branch (and #32, E10a): what happens, in order

1. **PR #31 (F5c): land** (L1). It has no review yet, and it touches protected paths
   (`app/security/`, `docker-compose.yml`, `tests/conftest.py`), so a full Bulwark run is required.
   Fix the ADR 0015 contradiction about `list_household_ids` in the same PR. It holds the migration
   token for `0010`. It must merge before P4, so the first access token is sealed as `hf1` (ADR 0015
   consequence) and not as a legacy row.
2. **PR #32 (F2b): land** (L2) before P4, which is the first API route to enqueue a job.
3. **PR #56 (P3): land as is** (L3). Steps 1–3 are independent, so merge whichever is green first.
   It needs a new Bulwark verdict, because the APPROVE at `88dda90` is stale after merge commit
   `cba7f7b`, plus a Ledger sign-off. No rework: `plaid_credentials()` is the one swap point, and
   K1 does the swap. ADR 0023 §7 ("key entry is open") is settled by ADR 0032, and K1 edits its
   status line.
4. **Branch `feat/P9a-plaid-setup-page`: rework, then replace** (P9b). Its first commit `6b8af28`
   is an old copy of P3, so drop it. Cherry-pick `d973679` onto main after #56 merges. Rewrite
   `plaidCopy.ts` for in-app keys, add the P4 and K1 surfaces, and push it as
   `feat/P9b-connections-page`. Keep the local branch until P9b merges, then delete it.
5. **Branch `feat/E10a-crypto-track`: rework later** (X1). It is not on the M-live path. Renumber
   ADR `0020-crypto-track-watch-only` and migration `0007_crypto_track` before any PR.

---

## 8. Carry-over money rules (STATUS "Carry into Phase 2"), mapped to the slice that enforces each

| Rule | Enforced by | Evidence |
|---|---|---|
| Dedupe Plaid against imported rows by `(account_id, posted_date, amount)`, Plaid wins | **P2b** (`supersede.py`, both directions). **P4a/P4** make it reachable by putting both in one account (ADR 0033) | Interleaving property test. Overlap month shows once (G1) |
| Import fingerprint includes a same-day ordinal | **already on main** (`domain/fingerprint.py`, I1). P2b never fingerprints connector rows | Existing golden tests |
| Pending→posted via `transaction.pending_external_id` | **P2a** (`post_pending`), **P5** (Plaid `pending_transaction_id` → `pending_external_id`). S1 is posted-only (ADR 0030) | Pending→posted fixture keeps category and notes |
| Save `tx_cursor` only after its batch commits | **P5** (one transaction: batch, cursor, `sync_run` success). The `sync_run` CHECK is already merged (F6) | Kill-between-pages test |
| Upsert on `(account_id, external_id)` and handle `removed` | **P2a** (`upsert_external`, `remove_external`), **P2b**, **P5** | Replay-twice test. `removed` fixture |
| Parse Plaid JSON with `parse_float=Decimal` | **L3** (#56 client). **S1** does the same for SimpleFIN | Grep test: no `float` in the parse paths |
| Negate Plaid signs once in `integrations/plaid/`, sign test per product | **P5** (transactions, balances), **P6** (investments, liabilities, balance get). `mapping.py` is the only negation site. **S1** asserts no negation | One sign test per product. A grep test: unary minus on `amount` only in `integrations/plaid/mapping.py` |
| API amounts are strings, with an OpenAPI test | **already on main** (`MoneyStr`, `test_openapi_export.py`). Every new schema in P4, P8 and M1 uses `MoneyStr` | OpenAPI hash test |
| Never sum across currencies | **P2b** (skips currency-less accounts), **S1** (custom currencies), **P8** (per-account values, no totals). Totals are Phase 5 | Custom-currency fixture |
| Splits written through one domain service | **C1, merged**. **P2a** keeps splits inside `LedgerWriter` (`post_pending`, `upsert_external`, `supersede`) | Single-writer lint |
| Transfer pairing is service-enforced | **C4, merged**. Connectors never set transfer fields. **P5** enqueues `ledger.refresh` (ADR 0029) | Single-writer lint. P5 enqueue test |
| Metrics use `COALESCE(splits, parent)` and exclude `is_transfer` (and superseded rows, ADR 0017) | **Phase 5 B2**, not this lane. P8 reports no spend totals | B2 |

---

## 9. Secrets per connector (ADR 0015, amended by ADR 0032)

| Secret | Kind | Stored | Entered by | Notes |
|---|---|---|---|---|
| `plaid_client_id`, `plaid_secret_sandbox`, `plaid_secret_production` | operator key (`get_secret`) | `self_host`: `.env` **or** `household_setting` (exactly one, both = conflict). `hosted`: `household_setting` only | owner on Settings → Connections (K1), or `.env` | `PLAID_ENV` stays in `.env` (ADR 0032 §4) |
| Plaid access token | linked credential | `bank_connection.access_token_enc`, `hf1` | the exchange (P4), never a person | Opened only in the client call |
| Plaid `public_token`, Link token | transient | never | Plaid Link | Never logged or audited. Link tokens are discarded after use |
| Plaid cursor | not a secret | `bank_connection.tx_cursor`, plaintext (ADR 0017) | P5 | — |
| SimpleFIN setup token | transient, one-time | never | owner pastes it (S1) | Claimed at once. A `403` means it may be compromised |
| SimpleFIN access URL | linked credential | `bank_connection.access_token_enc`, `hf1` | the claim (S1) | Not a `get_secret` name (ADR 0030). `resolve_outbound` before every fetch |
| Crypto endpoint URL (Electrum, EVM RPC) | may carry an API key | `household_setting` `crypto.endpoint.<chain>`, encrypted (E10a design) | owner | X1 decides whether it joins the `get_secret` allowlist. Rotation already covers the column |
| Exchange API key, secret, passphrase | linked credential | `exchange_account.*_enc` (PLAN 18.5) | owner | Read-only only. A new ciphertext column needs a rotator ADR (ADR 0015 §2) |
| `anthropic_api_key`, `openai_compat_api_key` | operator key | as for the Plaid keys | owner (K1 covers them) | Phase 8 |

**In-app Plaid key entry: the options and the choice.**
- (A) Keep `.env` plus a `make plaid-keys` prompt. Rejected by the owner.
- (B) The stored row overrides env.
- (C) Env falls back to the row.
- (D) Either source, but **both is a conflict**.
- (E) The API writes `.env`.

**Chosen: D** (ADR 0032). It is the only option that keeps ADR 0015's rule that no source silently
replaces or fills in another. It needs no restart and no new store, because rotation already covers
`household_setting`. It also turns owner-queue item 1 into a concrete step: delete the production
keys from the dev `.env` and enter them in the app.

---

## 10. Crypto 10a (slice level only) and the custody rules it must obey

X1 brings the existing `feat/E10a-crypto-track` work onto main. That work is watch-only Bitcoin
(xpubs and descriptors through embit), EVM addresses, a `ChainSource` port with Electrum and EVM
JSON-RPC adapters and a fake, and the Track schema. It is rebased, the ADR and migration are
renumbered, and it is split into PRs of at most M:
- schema
- pure domain plus the guard
- adapters
- service plus routes

Exchanges (CCXT, read-only keys) are a later slice, X2. Crypto rows reach net worth through Phase 5
views, never through `IngestService`'s bank-transaction path. Hard rule 11, enforced by Cipher's
gate on every X PR:
- No input, field, parameter, log line or table may accept, store, log or transmit a seed phrase or
  private key. The guard rejects `xprv`-family keys, WIF, 64-hex keys and word lists before any
  parser runs, with a fixed error that never echoes the input.
- Only xpubs, descriptors and addresses are stored. `CHECK (is_watch_only)` stays until a 10c
  migration deliberately relaxes it.
- No spend, PSBT or signer code in 10a. `FEATURE_CRYPTO_SPEND=false` and self-host only.
- Exchange keys must be read-only. Keys that report trade or withdraw rights are refused (CHECK).
  Otherwise a blocking warning is shown, and the key is Fernet-sealed.
- No public chain endpoint without the owner's explicit, per-chain opt-in and the privacy warning.
  No default URL in code.

---

## 11. Fastest path for the owner's own instance

**This week, with what is merged (`733132e`):**
- **Statement PDFs: yes, now.** `make up && make migrate` on the owner's machine. Then Import → upload
  the PDFs from the private directory (ADR 0019). The six merged layouts reconcile or land in the
  review queue with a reason. The Transactions screen shows the rows, and categories exist. This
  works on the PC at `https://localhost`.
- **Everything else needs a merge first.** Nothing else on main ingests real data today.

**This week, if the lane runs as planned (about 3 windows plus reviews):**
- **Plaid, one institution (M-live).** This needs rows 1–12, then the owner acts: confirm Trial
  approval, pick the institution, and link from the desktop.
  - Link an institution **without** imported statements first. That way M-live doesn't depend on
    getting the adoption mapping right on the first try. Link a statement-backed institution second.
  - Balances arrive with P5. Investments and liabilities wait for P6.
- **CSV/OFX** when I2b merges.

**Waits:**
- SimpleFIN: S1 plus the owner's subscription.
- Plaid investments and liabilities: P6.
- Automatic polling: P7. Until then, use "Sync now".
- Manual accounts: M1.
- Competitor exports: I3.
- Crypto: X1, on Keel's schedule.
- Credit reports: Phase 6. Tax documents: Phase 7.

**The phone** is outside this lane. Merged code serves the PC on `localhost` only. The supported
path is ADR 0021: PA1, or the smaller manual host path PA-D9 (Harbor, S, Bulwark gate). Keel can
pull PA-D9 forward. Either way the owner installs Tailscale on the host and the phone. Until PA-D1
there is one RP id, so passkeys must be registered at the tailnet name, and the PC then uses that
name too. An ad-hoc `tailscale serve` in front of Caddy is not recommended: every phone request
would look like `127.0.0.1` (`docs/ops/client-ip-check.md`).

---

## 12. Questions only the owner can answer

1. **Is the Plaid Trial approved for production**, and does the dashboard list Transactions,
   Investments and Liabilities? (Free. It blocks M-live.)
2. **The 10-slot plan (PLAN 3.2).** Which logins get Plaid, which go to SimpleFIN, statements or
   manual entry, and **which institution is linked first**? (Irreversible: each production Item
   is one of 10 lifetime slots.)
3. **SimpleFIN Bridge: subscribe or not?** ($1.50/month or $15/year plus tax.)
4. **Production keys:** after K1, will you delete them from the dev `.env` and enter them in the
   app (owner-queue item 1)? Until K1, M-live uses the `.env` keys you already have.
5. **Sandbox gate (G1):** will you run the 10-minute sandbox smoke on your instance, or add
   sandbox-only Plaid keys as a GitHub Actions secret so Sounding can run it? Agents never read
   `.env`.
6. **Which machine is "your instance"** for real data: the dev stack on this machine, or a separate
   host (owner-queue item 3)? A separate compose project keeps agents' test stacks away from your
   data.
7. **Phone:** install Tailscale on the host and phone? Accept re-registering your passkey at the
   tailnet name? Optionally, register `https://<tailnet name>/plaid/oauth-return` in Plaid's
   dashboard so you can link from the phone later.
8. **Crypto:** do you run an Electrum server and/or an EVM node? If not, do you accept a public
   endpoint's privacy cost when that path is built? Which exchanges do you use, if any?

---

## 13. Out of this contract
Plaid webhooks and Funnel (cut from Q1). The Plaid Statements product. An in-app Sandbox/Production
switch (ADR 0032 §4). Hosted-mode shared operator keys and cross-household slot counts (ADR 0034).
Credit (Phase 6), tax documents (Phase 7), crypto tax (10b), spend (10c). Plugin entry points for
connectors (PLAN 20.3 "3+"). Un-adopting an account (ADR 0033 §5).
