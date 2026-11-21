# Phase 4: wiring contract (rules, transfers, recurring)

Spec: PLAN 8.2–8.4, 11, 13 Phase 4 (Section 20 wins), ROADMAP E4 (C3–C7), ADRs 0008, 0011, 0016,
0017, 0022, and the new ADRs 0024–0028. This file is binding for every Phase 4 story. A builder
who must deviate says so in the PR body and asks Lattice for an amendment; the amendment lands in
this file before the code merges.

**Problem.** Three engines are merged as pure code and nothing runs them:
`app/domain/rules.py` (C3a), `app/domain/recurring.py` (C5a), `app/domain/transfers.py` +
`app/services/transfer_pairing.py` (C4, guarded so no job or handler may call it).

**Phase 4 acceptance (ROADMAP, measured by C7 on Sounding's synthetic holdout):**
1. At least 90% of transactions categorised at 95% precision or better, after about 200 labels.
   The ML classifier is cut from Q1 (ROADMAP "Cut"), so coverage comes from rules (made with "make
   a rule from this transaction") plus the Plaid category mapping (ADR 0022) on Plaid-sourced rows.
2. Transfers are excluded from spend: a card payment from checking is excluded on both legs, and
   the card's purchases are counted.
3. At least 95% of planted bills are recovered in the recurring list (both tiers count; false
   series are reported per tier).
4. Preview count = applied count, and re-applying a rule changes nothing.
Checks on the owner's real data are recorded as *unverified* until the owner runs them (owner
queue 5).

---

## 1. Decisions at a glance

| # | Question | Decision | Record |
|---|---|---|---|
| D1 | Regex engine | `google-re2==1.1.20251105` behind `app/domain/rule_regex.py`; literal modes ship first; `regex` mode is rejected by the API until C3d merges | ADR 0024 |
| D2 | Who sees rules | Household-readable unless `account_scope = 'own'`; only the author writes; a rule only touches rows on joint accounts or the author's own | ADR 0025 |
| D3 | Rule hit count | Not stored. `transaction.category_rule_id`, counted under the reader's RLS | ADR 0025 |
| D4 | Rule actions | Set `category_id` only. `mark_transfer_candidate`, tax tag and rename are not exposed in Phase 4 (a rule to a `transfer`-kind category already excludes rows from metrics, ADR 0022 rule 1) | here |
| D5 | Rules vs split parents, transfer legs | Split parents are skipped (the person's splits own reporting). Transfer legs are categorised like any row (harmless while `is_transfer`, correct if unmarked) | here |
| D6 | Where rules run | Inline at ingest; retroactive preview/apply in the author's request session with a preview token | ADR 0028 |
| D7 | Transfer pairs | Only between accounts with the same owner; cross-owner moves become single-leg transfers; pairing never changes categories | ADR 0026 |
| D8 | "Not a transfer" | `transfer_rejection` (`pair` and `leg` kinds), written by reject and by every unmark; the matcher honours it | ADR 0026 |
| D9 | Suggestions | Computed per person in that person's session; stored with `person_id`; RLS also requires both legs visible | ADR 0025 |
| D10 | Recurring identity | Stable series ids reconciled by member overlap; lapsed predecessors merged; `confirmed`/`dismissed` stick; status computed at read time; `bill`/`possible` tiers | ADR 0027 |
| D11 | Jobs | One `ledger.refresh` job: auto-pair, then per-person suggestions, then recurring in a process pool | ADR 0028 |
| D12 | Step-up re-auth | None of the Phase 4 endpoints (section 5.6) | here |
| D13 | `merchant_key` | Pure, versioned `app/domain/merchant.py`; computed when read, not stored | here |

---

## 2. Pure domain changes (no database; Codex-suitable unless noted)

All new code is pure: no I/O, no models, no `app.services` or `app.jobs` imports (import-linter
contract), `Decimal` only, deterministic and independent of input order.

### 2.1 `app/domain/merchant.py` (new, C3c)
```python
MERCHANT_KEY_VERSION = 1
def merchant_key(merchant_name: str | None, description_raw: str) -> str | None
```
- Source: `merchant_name` if it has at least one letter after normalisation, else `description_raw`.
- Normalise with `normalize_description` (upper case, collapsed whitespace), then split on
  whitespace and on `* # / \ | : ; ,`. Drop tokens that contain a digit. Drop leading noise tokens
  from a module-level tuple (`POS`, `DEBIT`, `PURCHASE`, `CHECKCARD`, `RECURRING`, `ACH`, `SQ`,
  `TST`); generic processor words only, never an institution or a person (hard rule 13).
- Keep the first four remaining tokens, join with one space, cut to 64 characters. Fewer than two
  characters gives `None`.
- Tests: table-driven with synthetic descriptions, idempotence (`merchant_key(k, k) == k`),
  property test that output never contains a digit.

### 2.2 `app/domain/rule_regex.py` (new in C3c, engine swapped in C3d)
```python
class CompiledPattern(Protocol):
    def search(self, text: str) -> bool: ...
MAX_PATTERN_LENGTH = 128
MAX_SUBJECT_LENGTH = 1024
def compile_pattern(pattern: str) -> CompiledPattern   # raises RuleValidationError
```
- C3c moves the existing C3a validator here unchanged (so `rules.py` stops owning regex).
- C3d replaces it with RE2 per ADR 0024 (options, empty-match rejection, first 1,024 characters
  searched, fixed error messages) and adds `google-re2` to `backend/pyproject.toml` + `uv.lock`.
  C3d needs network for `uv lock`, so it is not a Codex slice.
- `RuleValidationError` moves to `rule_regex.py` and is re-exported from `rules.py`.

### 2.3 `app/domain/rules.py` v2 (C3c)
- `Rule` gains `author_person_id: UUID` and `created_at: datetime`. Order is
  `(priority, created_at, id)`. A draft being previewed uses `created_at = datetime.max (UTC)` and
  `id = UUID(int=2**128 - 1)`, so it sorts last among equal priorities, exactly where it will sit
  once saved.
- `RuleTransaction` gains `account_owner_person_id: UUID | None` and `is_split: bool = False`.
  `merchant_key` is filled by the loader with `merchant.merchant_key`.
- `rule_may_touch(rule, txn) -> bool`: `txn.account_owner_person_id is None or
  == rule.author_person_id` (ADR 0025). `match` and `preview` consider, per row, only the rules
  that may touch it.
- `preview` skips `is_split` rows and rows whose source a rule cannot replace (unchanged).
- New:
  ```python
  @dataclass(frozen=True) class RulePreview:
      changes: tuple[RuleMatch, ...]       # rows where `target` wins and something changes
      skipped_user: int                    # target's conditions match, category_source = 'user'
      skipped_split: int                   # ... and the row is split
      skipped_not_writable: int            # ... and rule_may_touch is False
  def preview_for(target: Rule, others: Iterable[Rule], txns: Iterable[RuleTransaction]) -> RulePreview
  def preview_token(target: Rule, account_ids: frozenset[UUID], changes: Sequence[RuleMatch]) -> str
  ```
  `preview_for` evaluates only `others` that rank before `target` plus `target` (later rules
  cannot beat it). `preview_token` is the SHA-256 hex of canonical JSON (sorted keys, no
  whitespace) of `{v: 1, conditions: conditions_to_json(...), category_id, priority, enabled,
  account_ids: sorted, changes: sorted transaction ids}`. It excludes the rule's id, so a draft's
  token equals the saved rule's token when nothing else changed.
- Codec (`v: 1`), strict, used by the service and the migration CHECK:
  ```python
  def conditions_to_json(c: RuleConditions) -> dict[str, object]
  def conditions_from_json(obj: Mapping[str, object], *, account_ids: frozenset[UUID] | None) -> RuleConditions
  ```
  Keys: `v`, `description` (`{mode, value}`), `merchant_key`, `amount_equals`, `amount_min`,
  `amount_max` (decimal **strings**; a JSON number is rejected), `amount_sign`, `date_from`,
  `date_through` (ISO dates). Unknown keys are rejected. Account ids are not in the JSON (they live
  in `rule_account`). `RuleAction.mark_transfer_candidate` stays in the dataclass but the codec
  and the API never produce it (D4).
- `DescriptionCondition` regex mode calls `rule_regex.compile_pattern`.

### 2.4 `app/domain/transfers.py` v2 (C4b)
- `match_transfers(candidates, *, rejected_pairs: frozenset[tuple[UUID, UUID]] = frozenset(),
  rejected_legs: frozenset[UUID] = frozenset())`. Rejected `(outflow_id, inflow_id)` edges are
  never created; rejected legs leave the pool entirely (not even competitors).
- `_suggest_reason`: `different_owners` whenever `out.owner_person_id != inn.owner_person_id`,
  including joint (`None`) versus owned (ADR 0026).
- New, for the review list:
  ```python
  REASON_AMBIGUOUS = "ambiguous"
  @dataclass(frozen=True) class ReviewEdge:
      outflow_id: UUID; inflow_id: UUID; reason: str; days_apart: int; hints: int
  def review_edges(candidates: Iterable[TransferCandidate], result: MatchResult) -> tuple[ReviewEdge, ...]
  def group_edges(edges: Iterable[ReviewEdge]) -> tuple[tuple[ReviewEdge, ...], ...]
  ```
  `review_edges` turns `suggested` into edges with their reason and every `AmbiguousLeg`
  candidate into an `ambiguous` edge oriented outflow to inflow, deduplicated, sorted by
  `(outflow_id, inflow_id)` as strings. `group_edges` returns connected components (legs as
  nodes), each sorted, components ordered by their smallest edge.
- Unchanged: auto-apply rules, closer-alternative rule, pending rows as competitors only.

### 2.5 `app/domain/recurring_series.py` (new, C5c; `recurring.py` is not edited)
```python
Direction = Literal["outflow", "inflow"]
UserState = Literal["auto", "confirmed", "dismissed"]
Status = Literal["active", "late", "possibly_cancelled"]
Tier = Literal["bill", "possible"]
NOMINAL_DAYS = {weekly: 7, biweekly: 14, semi-monthly: 15, monthly: 30, quarterly: 91, annual: 365}
def series_status(cadence: Cadence, next_expected_date: date, as_of: date) -> Status
def series_tier(confidence: Decimal, cadence: Cadence, is_variable: bool, user_state: UserState) -> Tier
@dataclass(frozen=True) class StoredSeries:
    id: UUID; account_id: UUID; merchant_key: str; direction: Direction; cadence: Cadence
    last_date: date; lapsed: bool; user_state: UserState; member_ids: frozenset[UUID]
@dataclass(frozen=True) class PlannedSeries:
    stored_id: UUID | None            # None: create
    detected: RecurringSeries | None  # None: kept as is (confirmed/dismissed, not re-detected)
    member_ids: frozenset[UUID]
@dataclass(frozen=True) class SeriesPlan:
    upserts: tuple[PlannedSeries, ...]; deletes: tuple[UUID, ...]
def reconcile_series(stored: Sequence[StoredSeries], detected: Sequence[RecurringSeries]) -> SeriesPlan
```
Rules exactly as ADR 0027 points 1–4. Invariants tested by property tests: member sets are
pairwise disjoint; every detected series appears in exactly one upsert; no stored `confirmed` or
`dismissed` id is ever in `deletes`; the result does not depend on input order.
`series_status`: tolerance 2 days (weekly, biweekly) or 3 (others); `active` while
`as_of <= next + tol`; `possibly_cancelled` once `as_of > next + NOMINAL_DAYS // 2` (and past
`tol`); `late` between. `series_tier` per ADR 0027 point 3 (threshold `Decimal("0.60")`, strictly
greater for `bill`).

---

## 3. Schema: one ordered migration plan

One migration per story; Keel hands the migration token in this order. Conventions from the
Phase 1 contract hold (UUID PKs, `timestamptz`, text + CHECK enums, `household_id NOT NULL` + index
+ ENABLE/FORCE RLS + `household_isolation`, composite FKs `(x_id, household_id)`, explicit
`ON DELETE`). Every migration has a working downgrade and passes up/down/up as `holdfast_owner`
and the RLS lint. Every new per-person policy is RESTRICTIVE (it can only narrow isolation).

**This plan fixes an order, not numbers.** Revision numbers are assigned at build time by
whoever holds the migration token: the next free number after the current Alembic head (0009 when
this was written), with `down_revision` set to that head. Other lanes' migrations (for example the
Plaid lane's P4 slot tombstone) may land in between; that changes the numbers, not this order or
the content. Below, M1–M4 name the migrations; the revision slug is fixed, the numeric prefix is not.

| Order | Revision slug | Story | Holder |
|---|---|---|---|
| M1 | `<n>_rules` | C3b | Ledger |
| M2 | `<n>_import_run_reason_final` | I1d | Tide |
| M3 | `<n>_transfer_corrections` | C4c | Ledger |
| M4 | `<n>_recurring_series` | C5b | Ledger |

M2 is tiny and independent, so it fills the token gap while C4b/C6a run. If its story slips, it
simply takes the token later; M3 and M4 do not wait for it. M3 needs M1 (the helper functions);
M4 needs M1.

### 3.1 M1 `<n>_rules` (C3b)
**Helpers** (ADR 0025): `app_account_details_visible(uuid) RETURNS boolean` and
`app_account_writable(uuid) RETURNS boolean`, bodies equal to 0002's `ACCOUNT_DETAIL_VISIBLE` /
`ACCOUNT_DETAIL_WRITABLE` (inlined in the migration as frozen text, importing no app code).
EXECUTE revoked from PUBLIC, granted to `holdfast_app`.

**`rule` rework.** The table has no writer today. The upgrade first counts every row across all
households and raises if any exist (no silent destruction; noted in the phase report). FORCE RLS
binds `holdfast_owner` too and `app.household_wide` does not lift household isolation, so the
check runs between `ALTER TABLE rule NO FORCE ROW LEVEL SECURITY` and `FORCE` in the same
migration transaction (ADR 0008, Consequences); a count under RLS would always be zero. Then:
| Column | Type | Notes |
|---|---|---|
| `author_person_id` | uuid NOT NULL | composite FK → `person(id, household_id)` ON DELETE CASCADE; index |
| `name` | text NOT NULL | CHECK 1–80 chars |
| `enabled` | boolean NOT NULL DEFAULT true | |
| `priority` | integer NOT NULL DEFAULT 100 | CHECK 1–10000 (exists, add CHECK) |
| `conditions` | jsonb NOT NULL | renamed from `match`; CHECK `jsonb_typeof = 'object'`, `conditions->>'v' = '1'`, and each of `amount_equals/min/max` absent or `jsonb_typeof = 'string'` |
| `category_id` | uuid NOT NULL | composite FK → `category(id, household_id)` ON DELETE CASCADE (a deleted category takes its rules; categories are hidden, not deleted, per ADR 0022) |
| `account_scope` | text NOT NULL DEFAULT 'any' | CHECK IN ('any','joint','own') |
| `created_by` | text | unchanged CHECK ('user','agent'); Phase 4 writes only `user` |
| `last_applied_at` | timestamptz NULL | set by retroactive apply |
| `updated_at` | timestamptz NOT NULL DEFAULT now() | |
Dropped: `set`, `hit_count`. Added: `UNIQUE (id, household_id)`; index
`(household_id, priority, created_at, id)`.
Policies: `person_read` FOR SELECT `app_household_wide() OR author_person_id = app_person_id() OR
account_scope IN ('any','joint')`; `person_insert`/`person_update`/`person_delete`
`app_household_wide() OR author_person_id = app_person_id()` (UPDATE as USING and WITH CHECK).
Trigger `rule_scope_guard` (BEFORE UPDATE OF `account_scope`): rejects the change while
`rule_account` rows exist (the service deletes them first in the same transaction).

**`rule_account`** `(household_id, rule_id, account_id)`, PK `(rule_id, account_id)`; composite FKs
→ `rule(id, household_id)` CASCADE and → `account(id, household_id)` CASCADE; index on
`(household_id)` and `(account_id)`. Read: `EXISTS (SELECT 1 FROM rule r WHERE r.id =
rule_account.rule_id) AND app_account_details_visible(account_id)`. Write: `EXISTS` on a rule the
session may write (author or household-wide). Trigger `rule_account_scope_guard` (BEFORE INSERT OR
UPDATE): scope `any` rejects every row; `joint` requires `account.owner_person_id IS NULL`; `own`
requires `account.owner_person_id = rule.author_person_id`. Messages name ids only.

**`transaction.category_rule_id`** uuid NULL; composite FK `(category_rule_id, household_id)` →
`rule(id, household_id)` ON DELETE SET NULL (category_rule_id); CHECK
`category_rule_id IS NULL OR category_source = 'rule'`; partial index on `category_rule_id`
WHERE NOT NULL. Only `LedgerWriter` sets it (add to `tests/test_ledger_single_writer.py`).

**Visibility rule.** A person sees a rule if they wrote it or it is `any`/`joint` scoped; a
`rule_account` row only if they can see the rule and the account's details; an `own` rule of
someone else never.

### 3.2 M2 `<n>_import_run_reason_final` (I1d, PR #55 follow-up)
`CREATE OR REPLACE FUNCTION import_run_guard()` with one more frozen column: once
`OLD.reconcile_status <> 'pending'`, `NEW.review_reason IS DISTINCT FROM OLD.review_reason`
raises `23514` ("reconciliation of import run % is final"). Downgrade restores the 0005 body
verbatim. RLS unchanged. Tests: set at the pending → needs_review update (allowed), change after
(rejected), on accepted (rejected).

### 3.3 M3 `<n>_transfer_corrections` (C4c)
**`transfer_rejection`**
| Column | Type | Notes |
|---|---|---|
| `id`, `household_id`, `created_at` | | standard |
| `kind` | text NOT NULL | CHECK IN ('pair','leg') |
| `transaction_id`, `transaction_account_id` | uuid NOT NULL | composite FK → `transaction(id, account_id)` CASCADE; for `pair` this is the outflow |
| `other_id`, `other_account_id` | uuid NULL | composite FK → `transaction(id, account_id)` CASCADE; the inflow |
| `rejected_by_person_id` | uuid NULL | composite FK → `person` ON DELETE SET NULL (rejected_by_person_id): the correction outlives the person |
CHECKs: `(kind = 'leg') = (other_id IS NULL)`, both other columns null together, `other_id <>
transaction_id`. `UNIQUE NULLS NOT DISTINCT (household_id, transaction_id, other_id)`; index on
`other_id`. Read: `app_account_details_visible(transaction_account_id) AND (other_account_id IS
NULL OR app_account_details_visible(other_account_id))`. Insert/delete: read predicate AND
`(app_account_writable(transaction_account_id) OR (other_account_id IS NOT NULL AND
app_account_writable(other_account_id)))`. No UPDATE grant.

**`transfer_suggestion`**
| Column | Type | Notes |
|---|---|---|
| `id`, `household_id`, `created_at` | | standard |
| `person_id` | uuid NOT NULL | composite FK → `person` CASCADE |
| `outflow_id`, `outflow_account_id` | uuid NOT NULL | composite FK → `transaction(id, account_id)` CASCADE |
| `inflow_id`, `inflow_account_id` | uuid NOT NULL | same |
| `reason` | text NOT NULL | CHECK IN ('no_hint','one_sided_hint','refund_candidate','user_treated_as_spending','different_owners','ambiguous') |
| `days_apart` | smallint NOT NULL | CHECK 0–5 |
| `hints` | smallint NOT NULL | CHECK 0–6 |
| `job_run_id` | uuid NULL | composite FK → `job_run(id, household_id)` ON DELETE SET NULL (job_run_id) (retention pruning) |
CHECK `outflow_account_id <> inflow_account_id`; `UNIQUE (person_id, outflow_id, inflow_id)`;
indexes `(household_id, person_id)`, `(outflow_id)`, `(inflow_id)`. Read:
`(person_id = app_person_id() OR app_household_wide()) AND
app_account_details_visible(outflow_account_id) AND app_account_details_visible(inflow_account_id)`.
Insert/delete: the same predicate. No UPDATE grant.

**Visibility rule.** A suggestion is visible only to the person it was computed for, and only
while both legs are visible to them. A rejection is visible to anyone who can see both legs.

### 3.4 M4 `<n>_recurring_series` (C5b)
**`recurring_series`**
| Column | Type | Notes |
|---|---|---|
| `id`, `household_id`, `created_at`, `updated_at` | | standard |
| `account_id` | uuid NOT NULL | composite FK → `account(id, household_id)` CASCADE |
| `merchant_key` | text NOT NULL | CHECK 1–64 |
| `merchant_key_version` | smallint NOT NULL | |
| `direction` | text NOT NULL | CHECK IN ('outflow','inflow') |
| `cadence` | text NOT NULL | CHECK IN the six `Cadence` values |
| `typical_amount`, `amount_low`, `amount_high` | NUMERIC(14,2) NOT NULL | signed (outflow negative); CHECK `amount_low <= typical_amount AND typical_amount <= amount_high`; CHECK sign agrees with `direction` for all three |
| `is_variable` | boolean NOT NULL | |
| `first_date`, `last_date`, `next_expected_date` | date NOT NULL | CHECK `first_date <= last_date AND last_date < next_expected_date` |
| `confidence` | NUMERIC(4,3) NOT NULL | CHECK 0–1 |
| `lapsed` | boolean NOT NULL | detector's flag, used only for merging |
| `user_state` | text NOT NULL DEFAULT 'auto' | CHECK IN ('auto','confirmed','dismissed') |
| `label` | text NULL | CHECK 1–80 |
| `last_job_run_id` | uuid NULL | composite FK → `job_run` ON DELETE SET NULL (last_job_run_id) |
`UNIQUE (id, household_id)`, `UNIQUE (id, account_id)`; index `(household_id, next_expected_date)`.
Read: `app_account_details_visible(account_id)`. Insert/delete: `app_household_wide()`. Update:
`app_account_writable(account_id)`. Trigger `recurring_series_guard` (BEFORE UPDATE): unless
`app_household_wide()`, any change other than `user_state`, `label`, `updated_at` raises `42501`.

**`recurring_member`** `(household_id, series_id, account_id, transaction_id)`, PK
`(series_id, transaction_id)`, `UNIQUE (transaction_id)`; composite FKs `(series_id, account_id)` →
`recurring_series(id, account_id)` CASCADE and `(transaction_id, account_id)` →
`transaction(id, account_id)` CASCADE (so a member is always on its series' account). Read:
`app_account_details_visible(account_id)`. Write: `app_household_wide()` only.

**Visibility rule.** A series and its members are visible exactly when the series account's
transactions are; summary-only viewers see none.

### 3.5 Tests every schema story adds
Parametrised RLS tests as `holdfast_app` (household A vs B, private vs shared vs summary_only vs
joint, unset context), guessed-id composite FK tests (another household's category, account,
transaction), the helper-equivalence test (ADR 0025 point 1), trigger tests run on their own with
RLS lifted in a rolled-back transaction (ADR 0008 pattern), and the exact privilege set per new table.

---

## 4. Who runs what, and when

| Work | Where | Session | Writes through | Idempotence |
|---|---|---|---|---|
| Rule CRUD | request | author, person-scoped | `RuleService` (rules are not ledger fields) | n/a |
| Rules at ingest | statement import request today; sync job later | the importer's session | `NewTransaction(category_id, category_source='rule', category_rule_id)` → `LedgerWriter.insert_transaction` | a re-import inserts nothing (fingerprints) |
| Rule preview | request, `anyio.to_thread` | author | none | pure |
| Rule apply (retroactive) | request | author | `LedgerWriter.apply_rule_bulk` (one UPDATE, precedence in WHERE, exact-count check) | preview token; a second apply changes 0 rows |
| Set / release category, mark / unmark transfer, manual pair, accept / reject suggestion | request | person | `LedgerWriter` (+ `transfer_rejection` in the same unit of work) | each call is a state transition; repeating it is a 409 or a no-op |
| Auto-pair transfers | `ledger.refresh` step 1 | household-wide | `pair_transfers` → `apply_transfer_pair` with `job_run_id` | paired rows leave the pool |
| Transfer suggestions | `ledger.refresh` step 2 | one `scoped_session` per person with a login | delete + insert own `transfer_suggestion` rows | replace per person |
| Recurring detection | `ledger.refresh` step 3, process pool, 120 s timeout | household-wide | `recurring_series` / `recurring_member` via `reconcile_series` | reconcile is a pure function of rows + stored series |

**Triggers for `ledger.refresh`:** after a statement upload that inserted at least one row
(`refresh:import:<document_id>`), after each committed sync run (Phase 2a,
`refresh:sync:<sync_run_id>`), and `POST /api/ledger/refresh`
(`refresh:manual:<UTC 5-minute bucket>`). Retry policy: 3 attempts, base 30 s. Budget: the whole
job under 30 s at 50,000 rows and two people.

**Rows a person has touched:**
- `category_source = 'user'` (including a cleared category): rules never change it
  (ADR 0022), and it is counted in `skipped_user`. "Hand back to automation" (`release`) clears
  the source; the next ingest-time or retroactive rule may then set it.
- Split rows: rules skip them; the transfer matcher only suggests them.
- A user-set category of kind `transfer` or `excluded` is not protection against pairing (ADR 0026).
- Unmarked pairs get a `pair` rejection and are never re-paired or re-suggested; unmarked single
  legs get a `leg` rejection. A manual pair or mark lifts the matching rejection.
- Recurring: detection ignores `is_transfer` rows; user categories do not matter. `confirmed` and
  `dismissed` stick across runs; `label` sticks.
- Disabling or deleting a rule leaves its categories (C3a semantics). Deleting sets
  `category_rule_id` to NULL on those rows (source stays `rule`).

**Service changes outside the new modules:**
- `LedgerWriter.apply_category(..., rule_id: UUID | None = None)`: sets `category_rule_id` when the
  source is `rule`, clears it otherwise; `set_category` and `release_category` clear it.
- `LedgerWriter.apply_rule_bulk(rule_id, category_id, transaction_ids) -> int`: one `UPDATE ...
  WHERE id = ANY(:ids) AND superseded_by_id IS NULL AND category_source IS DISTINCT FROM 'user'
  AND NOT EXISTS (split)` under RLS; raises `LedgerRuleError("preview_stale")` unless the row
  count equals `len(ids)`; one audit row `rule.applied` `{rule_id, count}` with the acting user.
- `LedgerWriter._check_pair` rejects different `owner_person_id` (ADR 0026).
  `unmark_transfer` writes the rejection. `mark_transfer_pair` / `mark_transfer_single` delete a
  matching rejection.
- `pair_transfers`: loads rejections, passes them to the matcher, computes `protected` per
  ADR 0026 point 4, and raises when `user_id` and `job_run_id` are both None.

---

## 5. API surface

All under `/api`, JSON, errors `{"error": {"code", "message"}}`, `X-Holdfast-CSRF: 1` on every
non-GET, money as decimal strings (`MoneyStr`), dates ISO. Routers stay thin (ADR 0011). Schema
names below are the Pydantic class names, so `make api-types` output is predictable. `ApiError`
gains one code: **`preview_stale` (409)**, added to `web/src/api/contracts.ts` in the same PR.

### 5.1 Transactions (C6a; transfer fields filled by C4c)
`TransactionOut` gains:
| Field | Type |
|---|---|
| `merchant_key` | `str \| null` |
| `category_id` | `uuid \| null` |
| `category_source` | `"user" \| "rule" \| "plaid" \| "ml" \| null` |
| `category_rule_id` | `uuid \| null` (null unless the reader can see that rule; from C3b) |
| `transfer_group_id` | `uuid \| null` |
| `has_splits` | `bool` |
| `can_edit` | `bool` (`app_account_writable(account_id)`) |

| Method + path | Body → response | Errors |
|---|---|---|
| GET `/transactions` | existing query + `category_id[]`, `uncategorized: bool`, `category_source`, `rule_id`, `transfer: "only" \| "exclude"` (keeps `exclude_transfers`) → `TransactionPage` | 422 |
| GET `/transactions/{id}` | → `TransactionOut` | 404 |
| PUT `/transactions/{id}/category` | `CategoryChoiceIn {category_id: uuid \| null}` → `TransactionOut` | 404 (missing, invisible, or not writable), 409 `conflict` (superseded) |
| POST `/transactions/{id}/category/release` | → `TransactionOut` | 404, 409 |
| POST `/transactions/{id}/transfer/mark` | → `TransactionOut` (single leg) | 404, 409 (already in a transfer) |
| POST `/transactions/{id}/transfer/unmark` | → `TransactionUnmarkOut {legs: TransactionOut[]}` | 404, 409 | 
| POST `/transfers/pairs` | `TransferPairIn {outflow_id, inflow_id}` → `TransferPairOut {group_id, legs: TransactionOut[2]}` | 404, 409 (different owners, amounts, currency, already paired) |
(The last three are C4c.)

### 5.2 Rules (C3e)
```
DescriptionMatch   {mode: "contains" | "starts-with" | "exact" | "regex", value: str (1–128)}
RuleConditionsIO   {description: DescriptionMatch | null, merchant_key: str | null (≤ 64),
                    amount_sign: "outflow" | "inflow" | null, amount_equals: MoneyStr | null,
                    amount_min: MoneyStr | null, amount_max: MoneyStr | null,
                    date_from: date | null, date_through: date | null}      # at least one set
RuleIn             {name: str (1–80), enabled: bool = true, priority: int (1–10000) = 100,
                    conditions: RuleConditionsIO, account_scope: "any" | "joint" | "own",
                    account_ids: uuid[] (0–50; empty iff scope "any"), category_id: uuid}
RuleOut            RuleIn fields + {id, author_person_id, is_mine: bool, hit_count: int,
                    last_applied_at: datetime | null, created_at, updated_at}
RuleList           {items: RuleOut[]}                 # ordered (priority, created_at, id); no paging
RulePreviewOut     {change_count: int, sample: TransactionOut[] (≤ 25, newest first),
                    skipped_user: int, skipped_split: int, skipped_not_writable: int,
                    preview_token: str}
RuleCreateIn       {rule: RuleIn, apply: {preview_token: str} | null}
RuleCreateOut      {rule: RuleOut, applied_count: int | null}
RuleApplyIn        {preview_token: str}
RuleApplyOut       {applied_count: int}
```
| Method + path | Body → response | Errors |
|---|---|---|
| GET `/rules` | → `RuleList` (every rule the reader can see) | |
| POST `/rules/preview` | `RuleIn` → `RulePreviewOut` (draft, nothing saved) | 422 |
| POST `/rules` | `RuleCreateIn` → 201 `RuleCreateOut`; with `apply`, create + apply in one transaction | 422, 409 `preview_stale` (nothing saved), 409 `conflict` (200 rules per author) |
| GET `/rules/{id}` | → `RuleOut` | 404 |
| PATCH `/rules/{id}` | partial `RuleIn` → `RuleOut` (never applies) | 404, 403 `forbidden` (visible, not yours), 422 |
| DELETE `/rules/{id}` | → 204 (categories stay) | 404, 403 |
| POST `/rules/{id}/preview` | → `RulePreviewOut` | 404 |
| POST `/rules/{id}/apply` | `RuleApplyIn` → `RuleApplyOut` | 404, 403, 409 `preview_stale` |
Validation (422, fixed messages, never echoing input): unknown category; an account the author
cannot write; accounts that do not fit the scope; `amount_min > amount_max`; a pattern RE2
rejects; `mode: "regex"` before C3d ("Patterns aren't available yet."). Rate limit: preview and
apply share 30 per minute per user (existing Redis limiter, 429 `rate_limited`).

### 5.3 Transfers review (C4c)
```
TransferEdgeOut    {suggestion_id: uuid, outflow_id: uuid, inflow_id: uuid,
                    reason: "no_hint" | "one_sided_hint" | "refund_candidate" |
                            "user_treated_as_spending" | "different_owners" | "ambiguous",
                    days_apart: int, can_pair: bool, can_mark_legs: bool}
TransferGroupOut   {key: str, legs: TransactionOut[], edges: TransferEdgeOut[]}
TransferReviewPage {groups: TransferGroupOut[], next_cursor: str | null}
TransferAcceptOut  {result: "paired" | "marked", legs: TransactionOut[]}
TransferRejectIn   {scope: "pair" | "legs" = "pair"}
```
| Method + path | Body → response | Errors |
|---|---|---|
| GET `/transfers/review?limit=1..50&cursor=` | → `TransferReviewPage`. Groups via `group_edges` over the reader's live suggestions (legs not transfers, not superseded, not rejected); newest leg first; `key` = smallest suggestion id | 422 |
| POST `/transfers/suggestions/{id}/accept` | → `TransferAcceptOut`. `paired` when `can_pair` (same owner, both writable), else `marked` (each writable leg marked single) | 404, 409 (a leg changed) |
| POST `/transfers/suggestions/{id}/reject` | `TransferRejectIn` → 204. `pair`: pair rejection. `legs`: a leg rejection for each writable leg ("Never a transfer") | 404 |

### 5.4 Recurring (C5b)
```
RecurringOut       {id, account_id, currency, label: str | null, merchant_key, direction,
                    cadence, typical_amount: MoneyStr, amount_low: MoneyStr, amount_high: MoneyStr,
                    is_variable, first_date, last_date, next_expected_date,
                    status: "active" | "late" | "possibly_cancelled", tier: "bill" | "possible",
                    user_state: "auto" | "confirmed" | "dismissed", confidence: DecimalStr,
                    member_count: int, can_edit: bool}
RecurringList      {items: RecurringOut[], as_of: date}   # ≤ 1,000, ordered next_expected_date, id
RecurringDetailOut RecurringOut + {members: TransactionOut[] (newest 24)}
RecurringPatch     {user_state?: "auto" | "confirmed" | "dismissed", label?: str | null}
```
| Method + path | Body → response | Errors |
|---|---|---|
| GET `/recurring?include_dismissed=false&direction=` | → `RecurringList` | 422 |
| GET `/recurring/{id}` | → `RecurringDetailOut` | 404 |
| PATCH `/recurring/{id}` | `RecurringPatch` → `RecurringOut` | 404, 403 (visible, account not writable), 422 |

### 5.5 Refresh and status (C4d; `recurring_count` from C5b)
| Method + path | Body → response |
|---|---|
| GET `/ledger/status` | → `LedgerStatusOut {refresh: {status: "queued" \| "running" \| "retrying" \| "succeeded" \| "failed", finished_at: datetime \| null} \| null, transfer_review_count: int, recurring_count: int}` (counts under the reader's RLS) |
| POST `/ledger/refresh` | → 202 `RefreshOut {run_id: uuid, status: str, created: bool}`; 503 `server_error` if the queue is unreachable |

### 5.6 Step-up re-auth
None. The re-auth list (security rule: adding an Item, export, package generation, PSBT build) is
about secrets, data leaving the instance, and money movement. Phase 4 endpoints edit labels on the
person's own rows, are bounded by RLS to rows they may write, and are confirmed explicitly where
they are bulk (the preview token). Every write is audited (`ledger.*`, `rule.*`,
`transfer.rejected`, `recurring.updated`).

### 5.7 Pagination
Cursor pagination (opaque `next_cursor`, `limit`) on `/transactions` (existing) and
`/transfers/review`. Rules (≤ 200 per author) and recurring (≤ 1,000) return whole lists.

---

## 6. Screens (Direction 01, semantic tokens, money in mono, 375 px first, 44 px targets)

Only what acceptance needs. No new nav entries; Transactions gains tabs, Settings gains Rules.

1. **Transactions → Activity (changed, W1).** Each row shows a category chip, "Transfer", or
   "Needs category". Filters gain category, "needs category" and transfers. Tapping a row opens
   the existing `ResponsiveSheet` (bottom sheet on mobile) with: category picker (grouped tree from
   `GET /categories`), "Hand back to automation" when `category_source = 'user'`, the rule that
   set it ("Set by rule: <name>" or "Set by another person's rule"), and from W3 "Mark as
   transfer" / "Not a transfer". Read-only (`can_edit = false`) rows show no actions. After a
   category change, a toast offers "Make a rule for <merchant_key>?".
2. **Rule sheet (W2).** Opened from that toast, "Make a rule from this transaction" in the row
   sheet (prefills `merchant_key` and `amount_sign`), or Settings → Rules → New. Fields: category,
   match (merchant, or description contains / starts with / is exactly / pattern), money in or
   out, optional amount range, accounts (any / joint / mine), and advanced: name, priority, dates.
   "Preview" shows "N past transactions will change" with up to 25 of them and the skipped counts
   in one line. Buttons: "Save and apply to N" (creates with the token) and "Save for new
   transactions only". `preview_stale` re-runs the preview and says the count changed.
3. **Settings → Rules (new, W2).** List of rules the person can see: name, category, hit count,
   enabled switch (own rules), author for others' rules (read only). Edit, delete, "Apply to past
   transactions" (preview first).
4. **Transactions → Transfers to review (new tab, W3)**, with the count from `/ledger/status` as a
   badge. One card per group: the legs (date, account, amount in mono), a plain reason ("Same
   amount, two days apart, no transfer wording"). Single-edge cards: "It's a transfer" and "Not a
   transfer". Multi-edge cards: one row per candidate with the same two actions; accepting one
   hides the rest of that card. "Never a transfer" sits in an overflow menu. "Check again" calls
   `POST /ledger/refresh` and shows the last run time.
5. **Transactions → Recurring (new tab, W4).** Sections: Bills (outflows, tier `bill`), Income
   (inflows, tier `bill`), Maybe recurring (tier `possible`, collapsed), and a filter for
   dismissed. Row: label or merchant, cadence, typical amount in mono ("about" when variable),
   next date, status chip (active, late, possibly cancelled). Sheet: "Yes, this recurs",
   "Not recurring", rename, last postings.

Changed pages: Transactions (tabs, row chips, sheet), Settings (Rules section). MSW handlers in
`web/src/mocks/ledger.ts` (+ new `rules.ts`, `recurring.ts`) mirror section 5 so Prism can build
before the backend lands.

---

## 7. Story slices

Kinds: **pure** (no database, no pnpm: Codex-suitable through firstchair), **db** (Ledger or Tide,
needs Postgres), **web** (Prism). Each is one PR. Bulwark gates anything touching RLS migrations,
new write endpoints, jobs or the regex engine; Ledger gates money paths; Prism gates web brand;
Sounding gates acceptance.

| ID | Slice | Kind / owner | Depends | Gates | Acceptance evidence |
|---|---|---|---|---|---|
| **C3b** | Migration M1 (rules), helper functions, `Rule`/`RuleAccount` models, `category_rule_id`, `NewTransaction.category_rule_id`, `apply_category(rule_id)`, `apply_rule_bulk` | db · Ledger (token) | — | Bulwark, Ledger | RLS matrix for `rule`/`rule_account`; scope triggers; guessed-id FKs; helper equivalence; bulk apply exact-count and precedence tests; up/down/up |
| **C3c** | `merchant.py`, `rule_regex.py` (validator moved), rules v2 (author scoping, splits, ordering, `preview_for`, `preview_token`, codec) | pure · Codex | — | Ledger, Bulwark | Table and property tests; draft token = saved token; order independence |
| **C4b** | transfers v2 (rejections, owner rule, `review_edges`, `group_edges`) | pure · Codex | — | Ledger | Rejected edge never paired or suggested over 500 random pools; joint-vs-owned suggests `different_owners`; grouping order-independent |
| **C5c** | `recurring_series.py` (`series_status`, `series_tier`, `reconcile_series`) | pure · Codex | — | Ledger | Property tests for the ADR 0027 invariants; lapsed-predecessor merge; PLAN 1.5-interval boundary table |
| **W1** | Activity tab chips, filters, row sheet with category picker and release, rule toast stub | web · Prism | contract (mocks); merges after C6a | Prism, Sounding | 375 px screenshots; vitest against MSW; read-only rows show no actions |
| **C6a** | Transaction category API (5.1 first block), `TransactionOut` fields, filters | db · Ledger | C3b, C3c | Bulwark, Ledger | API tests incl. private/shared/summary_only/joint; `make api-types` diff; superseded → 409 |
| **I1d** | Migration M2, review_reason final | db · Tide (token) | none (takes the token whenever it is free) | Bulwark, Ledger | Trigger tests in 3.2 |
| **C3d** | RE2 in `rule_regex.py`, dependency pin, timing tests, `docs/user/rules.md` | pure + lockfile · Ledger | C3c | Bulwark, Ledger | Both PR #53 patterns + corpus within budget; `import re2` only in one module; pip-audit clean |
| **C3e** | `RuleService`, `/api/rules`, ingest-time rules in statement import, `preview_stale` code | db · Ledger | C3b, C3c, C6a (C3d for regex) | Bulwark, Ledger | preview count = applied count; second apply = 0; author scoping across two people; 50k-row timing under 2 s; re-import inserts nothing and keeps categories |
| **W2** | Rule sheet, preview, Settings → Rules, "make a rule" entry points | web · Prism | C3e (mocks earlier) | Prism, Sounding | Preview → save-and-apply flow in vitest; stale path; 375 px |
| **C4c** | Migration M3, writer changes (ADR 0026), `pair_transfers` honours rejections, endpoints 5.1 (transfer) + 5.3 | db · Ledger (token) | C3b, C4b, C6a | Bulwark, Ledger | Unmark then rerun never re-pairs; cross-owner accept marks legs; per-person suggestion invisibility; guard test still forbids the matcher in `app/api/` |
| **C4d** | `ledger.refresh` job (steps 1–2), arq pool in the API, enqueue after import, 5.5 endpoints; guard test allows only `app/jobs/handlers/ledger_refresh.py`; tighten the household-wide grep guard (PR #50 follow-up) | db · Ledger | C4c | Bulwark, Ledger | Enqueue twice = one run; job audit carries `job_run_id`; person A never sees a suggestion shaped by B's private rows |
| **W3** | Transfers to review tab, badge, sheet transfer actions | web · Prism | C4c, C4d | Prism, Sounding | Accept / reject / multi-edge cards in vitest; 375 px |
| **C5b** | Migration M4, recurring service, job step 3 (process pool), 5.4 endpoints, `recurring_count` | db · Ledger (token) | C5c, C4d | Bulwark, Ledger | Confirm/dismiss survive a rerun; guard trigger; event loop stays responsive during a 50k-row run (a heartbeat task keeps ticking) |
| **W4** | Recurring tab | web · Prism | C5b | Prism, Sounding | Sections and statuses render at 375 px |
| **C7** | Eval harness + Phase 4 acceptance (`docs/phases/PHASE-4.md`) | Sounding | all | Sounding | The four acceptance numbers on the holdout seeds |

**Soonest visible:** C3b → C6a → W1 puts categories on the Transactions screen and lets the owner
change them. Run C3c, C4b and C5c (Codex) and W1 (against mocks) alongside C3b. Then C3d + C3e + W2
(rules usable), then C4c + C4d + W3 (transfers), then C5b + W4 (recurring), then C7.

### 7.1 File ownership per run
| Slice | Owns (creates or edits) |
|---|---|
| C3b | `backend/migrations/versions/<n>_rules.py`, `backend/app/models/transactions.py`, `backend/app/models/__init__.py`, `backend/app/services/ledger_service.py`, `backend/tests/models/test_rls_rules.py`, `backend/tests/ledger/test_ledger_service.py`, `backend/tests/test_ledger_single_writer.py` |
| C3c | `backend/app/domain/{merchant,rule_regex,rules}.py`, `backend/tests/domain/{test_merchant,test_rule_regex,test_rules}.py` |
| C4b | `backend/app/domain/transfers.py`, `backend/tests/domain/test_transfers.py` |
| C5c | `backend/app/domain/recurring_series.py`, `backend/tests/domain/test_recurring_series.py` |
| W1 | `web/src/pages/transactions/**`, `web/src/mocks/ledger.ts`, `web/src/api/ledger.ts` |
| C6a | `backend/app/api/routes_ledger.py`, `backend/app/schemas/ledger.py`, `backend/app/services/ledger.py`, `backend/tests/api/test_ledger*.py`, `web/src/api/schema.d.ts` (generated) |
| I1d | `backend/migrations/versions/<n>_import_run_reason_final.py`, `backend/tests/models/test_import_run*.py` |
| C3d | `backend/app/domain/rule_regex.py`, `backend/pyproject.toml`, `backend/uv.lock`, `backend/tests/domain/test_rule_regex.py`, `docs/user/rules.md` |
| C3e | `backend/app/services/rules.py`, `backend/app/api/routes_rules.py`, `backend/app/schemas/rules.py`, `backend/app/services/statement_import.py`, `backend/app/services/errors.py`, `web/src/api/contracts.ts` (error code only), `web/src/api/schema.d.ts`, `backend/tests/api/test_rules*.py`, `backend/tests/services/test_rules*.py` |
| W2 | `web/src/pages/settings/Rules*.tsx`, `web/src/features/rules/**` (new), `web/src/mocks/rules.ts`, `web/src/api/rules.ts` |
| C4c | `backend/migrations/versions/<n>_transfer_corrections.py`, `backend/app/models/transactions.py`, `backend/app/services/{ledger_service,transfer_pairing}.py`, `backend/app/services/transfers.py` (new, request-side), `backend/app/api/routes_transfers.py`, `backend/app/schemas/transfers.py`, `backend/app/api/routes_ledger.py` (transfer endpoints), tests, `web/src/api/schema.d.ts` |
| C4d | `backend/app/jobs/handlers/ledger_refresh.py`, `backend/app/api/deps.py`, `backend/app/services/imports.py` (enqueue), `backend/app/api/routes_ledger_status.py`, `backend/tests/ledger/test_transfer_pairing_guard.py`, `backend/tests/models/test_household_wide_guard.py`, job tests |
| W3 | `web/src/pages/transactions/Transfers*.tsx`, `web/src/mocks/ledger.ts` |
| C5b | `backend/migrations/versions/<n>_recurring_series.py`, `backend/app/models/recurring.py` (new), `backend/app/services/recurring.py`, `backend/app/jobs/handlers/ledger_refresh.py`, `backend/app/worker.py` (pool lifecycle), `backend/app/api/routes_recurring.py`, `backend/app/schemas/recurring.py`, tests, `web/src/api/schema.d.ts` |
| W4 | `web/src/pages/transactions/Recurring*.tsx`, `web/src/mocks/recurring.ts`, `web/src/api/recurring.ts` |
Shared files (`backend/app/models/__init__.py`, `web/src/api/schema.d.ts`, `web/src/app/routes.tsx`):
add only what the slice needs and say so in the PR. Generated `schema.d.ts` conflicts are resolved
by rerunning `make api-types`, never by hand.

---

## 8. Reviewer findings folded in

| Source | Finding / precondition | Answer |
|---|---|---|
| PR #53 Bulwark | No user regex saved or run until a linear-time engine or killable subprocess is chosen | ADR 0024: RE2; API rejects `regex` until C3d (5.2) |
| PR #53 Bulwark | Rule's category and accounts must belong to the author's household and visible accounts | Composite FKs on `rule.category_id` and `rule_account` (3.1); scope trigger; 422 for accounts the author cannot write |
| PR #53 Bulwark | Preview and apply in the author's person-scoped session | Section 4; ADR 0028 point 2 |
| PR #53 Bulwark | Write only through `LedgerWriter` | `apply_rule_bulk`, `apply_category(rule_id)`; single-writer lint covers `category_rule_id` |
| PR #53 Bulwark | Timing test includes the two slow patterns | C3d evidence |
| `rules.py` docstring | Split parents, transfer legs, where `transfer_candidate` lives | D4, D5 |
| PR #50 Ledger/Bulwark/Keel | Pair-level "not a transfer" record, honoured | `transfer_rejection` (3.3); `match_transfers(rejected_pairs=...)` (2.4) |
| PR #50 | `job_run_id` required whenever `user_id` is None | Section 4 service changes; ADR 0026 point 6 |
| PR #50 | Store suggested/ambiguous per person with RLS; a household-wide report never over an API | Per-person passes in person sessions; `transfer_suggestion.person_id` + both-legs policy (ADR 0025 point 4) |
| PR #50 | Either owner can dissolve a pair touching their leg | Same-owner pairs only; cross-owner = single legs (ADR 0026) |
| PR #50 follow-up | Tighten the household-wide grep guard to any use of the setting name outside `db_context` and migrations | C4d |
| `transfer_pairing.py` | NOT SAFE TO SCHEDULE until rejection exists | C4d is ordered after C4c; the guard test is narrowed, not removed |
| PR #52 Ledger | Consumers must require confidence above 0.60 (annual pairs) | `tier = possible` at ≤ 0.60 (ADR 0027 point 3) |
| PR #52 | A regular weekly shop returned as a variable weekly series | Fast-cadence variable series are `possible` unless confirmed |
| PR #52 | Typical amount can be a value never charged | API returns the band; UI says "about" for variable series |
| PR #52 | Resumed schedules form a duplicate lapsed series | `reconcile_series` merge rule |
| PR #52 | CPU-heavy, off the event loop | Process pool with timeout (ADR 0028 point 3.3); C5b responsiveness test |
| PR #52 | Bills hidden when ad-hoc in-range purchases share the merchant; variable weekly pay with a missed cycle dropped | Known detector limits, not wiring. C7 reports them by category; a detector follow-up is scheduled only if C7 misses 95% |
| PR #55 Keel | Freeze `review_reason` in `import_run_guard` (own migration) | M2 (3.2) |
| ADR 0016 | Wire `arq_pool` into the API when the first API-triggered job lands | C4d |

---

## 9. Owner decisions (each has a default, so nothing is blocked)

1. **Rules are visible to everyone in the household** unless limited to your own accounts.
   Default: yes (ADR 0025). The alternative is that each person sees only their own rules. That
   would make a joint account's preview disagree with what imports do.
2. **A move between two people's accounts** is recorded as two separate transfers, not one linked
   pair. Default: yes (ADR 0026). Both sides are still kept out of spending.
3. **Real-data checks** ("recurring list matches your real bills", 90% categorised after about 200
   labels) are recorded as *unverified* until you run them on your own machine with your
   statements. Default: Sounding reports synthetic results; your run is optional.

---

## 10. Out of Phase 4 (recorded, not built)
ML classifier and nightly retrain (cut from Q1); rule actions for tax tag, rename and transfer
marking; undoing a retroactive apply; splits UI and API (C6 remainder, Phase 5 if needed);
agent-authored rules (Phase 8: created disabled, a person enables); a household time-zone setting
for `as_of`; the account-claim path's dissolving of cross-owner pairs (ADR 0026); `v_recurring`
view for the agent (Phase 8); recurring-price-increase alerts (Phase 5, B4).

Phase 4 does not touch secrets handling, `household_setting`, ADR 0015 or anything in the Plaid
lane (its key entry and slot-tombstone migration are separate stories). ADR 0024 is the first
Phase 4 ADR because 0023 belongs to the Plaid lane's PR #56.
