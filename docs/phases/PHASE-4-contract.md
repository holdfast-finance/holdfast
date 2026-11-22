# Phase 4: wiring contract (rules, transfers, recurring)

Spec: PLAN 8.2–8.4, 11, 13 Phase 4 (Section 20 wins), ROADMAP E4 (C3–C7), ADRs 0008, 0011, 0016,
0017, 0019, 0022, and the Phase 4 ADRs 0024 and 0026–0029. This file is binding for every Phase 4
story. A builder who must deviate says so in the PR body and asks Lattice for an amendment. The
amendment lands in this file before the code merges.

Revision 2 (2026-11-21) answers Gadfly's two critiques (`docs/phases/reviews/PHASE-4-gadfly.md`,
`PHASE-4-contract-gadfly.md`) and Bulwark's review of PR #59. Section 11 lists every item and
where it was closed.

**Problem.** Three engines are merged as pure code and nothing runs them:
- `app/domain/rules.py` (C3a)
- `app/domain/recurring.py` (C5a)
- `app/domain/transfers.py` + `app/services/transfer_pairing.py` (C4). A guard test keeps the
  matcher away from every job and handler.

Statement import also makes every account private to the uploader, and nothing can change that.
So the second person sees nothing, and joint accounts can't exist on real data (slice A1 fixes
this).

---

## 0. Phase 4 acceptance (every criterion can fail)

Sounding measures criteria 1–4 twice:
- **S:** with the C7 harness on synthetic holdout seeds (section 7.4)
- **R:** on the owner's instance data, in the real-data run (section 7.5)

Both must pass. Criterion 5 is the screen check. Each number below has a stated denominator, and
the run records its value in `docs/phases/PHASE-4.md` (R as counts and rates only, ADR 0019).

1. **Categorised.** After the labelling simulation (200 labels, section 7.4):
   - Coverage ≥ 90%: rows with a category ÷ eligible rows.
   - Precision ≥ 95%: rows whose category slug equals the truth ÷ rows with a category.
   - Eligible means live (`superseded_by_id IS NULL`), posted, not a transfer by the truth, and
     not split. The 200 labelled rows are left out of both counts.
   - Plaid-sourced rows are 0% of S and R today (statement import only), so the ADR 0022 mapping
     adds nothing to this number. The run states that share.
2. **Transfers excluded from spend.**
   - (a) Every planted card payment and own-account move whose two legs both carry a hint ends with
     both legs `is_transfer` after one refresh, with no clicks. That includes joint checking paying
     a personally owned card, and one person's private account paying the other's.
   - (b) Every other planted transfer with both legs present, whose two legs at least one person
     can see, is excluded or is offered to at least one person who can write a leg. The run also
     reports the planted transfers no single person can see both legs of (an unhinted move between
     two private accounts, the stated residual in ADR 0027).
   - (c) No planted purchase is excluded.
   - (d) The spend total over the holdout equals the truth's spend total exactly (`Decimal`
     equality).
   - For R, "planted" means the agent's hand list of card payments (section 7.5). (a) becomes ≥ 90%
     with no click, and (b) stays at 100%.
3. **Recurring.**
   - Bill recovery ≥ 95%. A truth bill counts as recovered when one stored series, of either tier,
     holds at least 80% of its members.
   - Bill-tier recovery ≥ 80% (the same rule, `bill` tier only).
   - False series (no truth bill behind them): ≤ 5% of `bill`-tier series (S) or ≤ 10% (R), and
     ≤ 25% of `possible`-tier series.
4. **Rules are exact.** For every rule the simulation saves:
   - (a) The preview's `change_count` equals `applied_count`.
   - (b) After the simulation, previewing every rule over all rows returns zero changes.
   - (c) Two households built from one seed end with the same category on every row, matched by
     fingerprint. One saves the rules before importing months 7–24. The other imports first, then
     applies the same rules in the same order with preview tokens.
   - The C3a guarantee "a mismatch is refused" isn't a criterion: it can't fail.
5. **Every screen works against the real API.** Each screen has one Playwright test (section 7.3,
   "e2e") that drives the real backend on a seeded local stack, with MSW off. It fails if an
   endpoint is missing or returns the wrong shape. Sounding runs all of them at acceptance and
   attaches the run summary to `PHASE-4.md`.

---

## 1. Decisions at a glance

| # | Question | Decision | Record |
|---|---|---|---|
| D1 | Regex engine | `google-re2==1.1.20251105`, only in `app/domain/rule_regex.py`. No fallback to `re`. A save-time cost probe. 256-character subjects. 25 enabled `regex` rules per household. Until C3d, `compile_pattern` refuses every input | ADR 0024 |
| D2 | Who sees rules | `any` ("joint and mine") and `joint` rules are household-readable, name and conditions included. `own` rules are author-only. Only the author writes. A rule only touches rows its scope allows, on accounts that are joint or the author's. A rule made from a row on a non-joint account defaults to `own`. No per-rule account lists in Phase 4 | ADR 0026 |
| D3 | Rule hit count | Not stored. It's `count(*)` of `transaction.category_rule_id` under the reader's RLS | ADR 0026 |
| D4 | Rule actions | `category_id` only. A rule to a `transfer`-kind category already drops rows from metrics (ADR 0022 rule 1) | here |
| D5 | Rules vs split parents and transfer legs | Rules skip split parents. Transfer legs are categorised like any row, and a rule's category doesn't stop pairing. This deviates from PLAN 8.2's "first hit wins" (ADR 0029, Consequences) | here |
| D6 | Where rules run | Inline at ingest, with the matcher in a worker thread. Retroactive preview and apply run in the author's request session with a preview token | ADR 0029 |
| D7 | Transfer pairs | A group (`transfer_group_id`) only joins two accounts with the same owner. A move between owners is two single legs joined by a `transfer_link`. Step 1 auto-pairs and auto-links on the same evidence bar. Pairing never changes categories | ADR 0027 |
| D8 | "Not a transfer" | `transfer_rejection` (`pair` and `leg`). Unmarking a pair, or a single leg with a visible link, writes a `pair` rejection. Only "Never a transfer" writes a `leg` rejection | ADR 0027 |
| D9 | Suggestions | Computed per person in that person's session, stored with `person_id`. Step 2 never writes pairs: a confident edge in a person's view becomes a `likely` suggestion | ADR 0026 |
| D10 | Recurring identity | Stable ids, reconciled by member overlap under a row lock. `confirmed` and `dismissed` stick. A label implies `confirmed`. Status is computed at read time. Two tiers, `bill` and `possible` | ADR 0028 |
| D11 | Jobs | One `ledger.refresh` job, coalesced per household into 2-minute buckets: link cleanup and auto-pair, then per-person suggestions, then recurring in a `forkserver` process pool | ADR 0029 |
| D12 | Step-up re-auth | None of the Phase 4 endpoints (section 5.6) | here |
| D13 | `merchant_key` | Pure, versioned `app/domain/merchant.py`, computed when read, not stored. Rules store the version they were written against | here |
| D14 | Account ownership | `PATCH /accounts/{id}` changes owner and visibility (A1). Pairs on the account are dissolved into single legs in the same unit of work. A DB trigger refuses an owner change while pairs remain. Claiming a joint account needs the household-owner role | ADR 0027 |
| D15 | Deleted and superseded legs | A deleted leg's partner is unmarked by trigger. Superseding moves transfer state, user category and splits in `LedgerWriter`. A trigger re-points rejections, links and recurring members to the successor | ADR 0027, 0028 |

---

## 2. Pure domain changes (no database; Codex-suitable unless noted)

All new code is pure:
- no I/O, no models, no `app.services` or `app.jobs` imports (import-linter contract)
- `Decimal` only
- deterministic, and independent of input order

### 2.1 `app/domain/merchant.py` (new, C3c)
```python
MERCHANT_KEY_VERSION = 1
def merchant_key(merchant_name: str | None, description_raw: str) -> str | None
```
- Source: `merchant_name` if it has at least one letter after normalisation, else `description_raw`.
- Normalise with `normalize_description` (upper case, collapsed whitespace).
- Split on whitespace and on `* # / \ | : ; ,`, using string methods, not `re`. Drop tokens that
  contain a digit.
- Drop leading noise tokens listed in a module-level tuple: `POS`, `DEBIT`, `PURCHASE`, `CHECKCARD`,
  `RECURRING`, `ACH`, `SQ`, `TST`. These are generic processor words only, never an institution or a
  person (hard rule 13).
- Keep the first four remaining tokens, join them with one space, and cut to 64 characters. A result
  shorter than two characters is `None`.
- Tests:
  - table-driven, with synthetic descriptions
  - idempotence (`merchant_key(k, k) == k`)
  - a property test that the output never contains a digit

### 2.2 `app/domain/rule_regex.py` (new in C3c, engine in C3d)
```python
class CompiledPattern(Protocol):
    def search(self, text: str) -> bool: ...
MAX_PATTERN_LENGTH = 128
MAX_SUBJECT_LENGTH = 256          # ADR 0024 point 5 (was 1,024; measured, see the ADR)
PROBE_LIMIT_MICROSECONDS = 10     # ADR 0024 point 4b
def compile_pattern(pattern: str) -> CompiledPattern   # raises RuleValidationError
```
- **C3c deletes the C3a `re` validator** (`_REGEX_TOKEN`, `_compile_safe_regex`, the `re`
  import). Until C3d, `compile_pattern` raises `RuleValidationError("Patterns aren't available
  yet.")` for every input.
  - Every path that builds a regex condition goes through `compile_pattern`: the codec, the loader
    and the API. So all of them refuse `regex`, whoever the writer is (API, CLI, backfill, or a
    future agent).
  - No path compiles a user pattern with a backtracking engine at any point.
- `rule_regex.py` never imports `re` or `regex`. A test parses the module (`ast`) and fails if it
  does.
  - The same test fails if `app/domain/rules.py` or `app/services/rules.py` import `re` or `regex`.
  - A second test fails on `import re2` anywhere but `rule_regex.py`.
- **C3d** implements ADR 0024: options, empty-match rejection, the cost probe, the first 256
  characters searched, fixed messages, and the no-fallback rule.
  - If `import re2` fails at runtime, `compile_pattern` raises `RuleValidationError("Patterns
    aren't available on this server.")`.
  - C3d adds `google-re2` to `backend/pyproject.toml` + `uv.lock`. It needs network for `uv lock`,
    so it isn't a Codex slice.
- `RuleValidationError` moves to `rule_regex.py`. `rules.py` re-exports it.

### 2.3 `app/domain/rules.py` v2 (C3c)
- `Rule` gains:
  - `author_person_id: UUID`
  - `created_at: datetime`
  - `account_scope: Literal["any", "joint", "own"]`

  Order is `(priority, created_at, id)`. A draft being previewed uses `created_at = datetime.max
  (UTC)` and `id = UUID(int=2**128 - 1)`, so it sorts last among equal priorities, exactly where it
  will sit once saved.
- `RuleTransaction` gains `account_owner_person_id: UUID | None` and `is_split: bool = False`. The
  loader fills `merchant_key` with `merchant.merchant_key`.
- `rule_may_touch(rule, txn) -> bool` (ADR 0026 point 3):
  - `any`: `owner is None or owner == author`
  - `joint`: `owner is None`
  - `own`: `owner == author`

  `match` and `preview` consider, per row, only the rules that may touch it.
- `preview` skips `is_split` rows and rows whose source a rule can't replace (unchanged). A row
  whose category already matches but whose source differs (`plaid` to `rule`) **is** a change. It
  gets source `rule` and `category_rule_id` (C3a semantics).
- `RuleConditions.account_ids` stays in the dataclass, but the codec and the API never produce it
  in Phase 4 (D2). The same goes for `RuleAction.mark_transfer_candidate` (D4).
- `DescriptionCondition.value` is `field(repr=False)`, so a rule's text never reaches a log through
  `repr` (Bulwark 12). Regex mode calls `rule_regex.compile_pattern`.
- New:
  ```python
  @dataclass(frozen=True) class RulePreview:
      changes: tuple[RuleMatch, ...]       # rows where `target` wins and something changes
      skipped_user: int                    # target's conditions match, category_source = 'user'
      skipped_split: int                   # ... and the row is split
      skipped_not_writable: int            # ... and rule_may_touch is False
  def preview_for(target: Rule, others: Iterable[Rule], txns: Iterable[RuleTransaction]) -> RulePreview
  def preview_token(target: Rule, changes: Sequence[RuleMatch]) -> str
  ```
  - `preview_for` evaluates only the `others` that rank before `target`, plus `target` (later rules
    can't beat it).
  - `preview_token` is the SHA-256 hex of canonical JSON (sorted keys, no whitespace) of `{v: 2,
    conditions: conditions_to_json(...), category_id, priority, enabled, account_scope, changes:
    sorted transaction ids}`.
  - The token leaves out the rule's id, so a draft's token equals the saved rule's token when
    nothing else changed.
- Codec (`v: 1`), strict, used by the service and the migration CHECK:
  ```python
  def conditions_to_json(c: RuleConditions) -> dict[str, object]
  def conditions_from_json(obj: Mapping[str, object]) -> RuleConditions
  ```
  - Keys: `v`, `description` (`{mode, value}`), `merchant_key`, `merchant_key_version`,
    `amount_equals`, `amount_min`, `amount_max`, `amount_sign`, `date_from`, `date_through`.
  - Amounts are decimal **strings**. A JSON number is rejected. Dates are ISO dates.
  - Unknown keys are rejected.
  - `merchant_key_version` is required exactly when `merchant_key` is set.
  - The loader skips a rule whose stored version differs from `MERCHANT_KEY_VERSION` and reports it
    as `status = "needs_review"`, so a version bump never silently changes what a rule matches. The
    PR that bumps the version decides whether it ships a rewrite migration.

### 2.4 `app/domain/transfers.py` v2 (C4b)
```python
def match_transfers(candidates, *, rejected_pairs: frozenset[tuple[UUID, UUID]] = frozenset(),
                    rejected_legs: frozenset[UUID] = frozenset()) -> MatchResult
@dataclass(frozen=True) class MatchResult:
    pairs: tuple[ProposedPair, ...]       # same owner (both joint, or one person), auto evidence bar met
    links: tuple[ProposedPair, ...]       # different owners (joint vs owned counts), same evidence bar
    suggested: tuple[SuggestedPair, ...]
    ambiguous: tuple[AmbiguousLeg, ...]
REASON_AMBIGUOUS = "ambiguous"
REASON_LIKELY = "likely"
@dataclass(frozen=True) class ReviewEdge:
    outflow_id: UUID; inflow_id: UUID; reason: str; days_apart: int; hints: int
def review_edges(candidates: Iterable[TransferCandidate], result: MatchResult) -> tuple[ReviewEdge, ...]
def group_edges(edges: Iterable[ReviewEdge]) -> tuple[tuple[ReviewEdge, ...], ...]
```
- **Rejections.** A rejected `(outflow_id, inflow_id)` edge is never created. A leg-rejected row
  leaves the pool entirely, so it isn't even a competitor.
- **Owners.** `owner_person_id` is compared as a value, and `None` (joint) is a value. Joint versus
  owned therefore differs, which fixes the merged code's `both non-null` test.
  - An edge that meets the auto evidence bar goes to `pairs` when the owners are equal, and to
    `links` when they differ.
  - `_suggest_reason` no longer returns `different_owners`. An unhinted cross-owner edge gets its
    hint reason (`no_hint`, `one_sided_hint`, `refund_candidate`), and a protected one gets
    `user_treated_as_spending`.
- **`review_edges`**:
  - `suggested` becomes edges with their reason.
  - Each `AmbiguousLeg` candidate becomes an `ambiguous` edge, oriented outflow to inflow.
  - **Each entry of `pairs` and `links` becomes a `likely` edge** (Gadfly B2): in a person's view
    these are edges that household-wide step 1 didn't take.
  - Edges are deduplicated and sorted by `(outflow_id, inflow_id)` as strings.
- `group_edges` returns connected components, with legs as nodes. Each component is sorted, and
  components are ordered by their smallest edge.
- Unchanged:
  - the hint rules
  - the closer-alternative rule
  - pending rows as competitors only
  - input-order independence
- **Who calls what:**
  - Step 1 (household-wide) writes `pairs` and `links` and ignores the rest.
  - Step 2 (per person) calls `review_edges` and writes nothing but suggestions (Bulwark 8).

### 2.5 `app/domain/recurring_series.py` (new, C5c; `recurring.py` isn't edited)
```python
Direction = Literal["outflow", "inflow"]
UserState = Literal["auto", "confirmed", "dismissed"]
Status = Literal["active", "late", "possibly_cancelled"]
Tier = Literal["bill", "possible"]
NOMINAL_DAYS = {weekly: 7, biweekly: 14, semi-monthly: 15, monthly: 30, quarterly: 91, annual: 365}
CONFIDENCE_STEP = Decimal("0.001")
def stored_confidence(raw: Decimal) -> Decimal        # quantize(CONFIDENCE_STEP, ROUND_HALF_EVEN)
def series_status(cadence: Cadence, next_expected_date: date, as_of: date) -> Status
def series_tier(confidence: Decimal, cadence: Cadence, is_variable: bool, user_state: UserState) -> Tier
@dataclass(frozen=True) class DetectedSeries:      # RecurringSeries plus what the store needs
    series: RecurringSeries; direction: Direction; first_date: date; member_ids: frozenset[UUID]
def detected_from(series: RecurringSeries, posted: Mapping[UUID, date]) -> DetectedSeries
@dataclass(frozen=True) class StoredSeries:
    id: UUID; account_id: UUID; merchant_key: str; direction: Direction; cadence: Cadence
    last_date: date; lapsed: bool; user_state: UserState; label: str | None
    member_ids: frozenset[UUID]
@dataclass(frozen=True) class PlannedSeries:
    stored_id: UUID | None            # None: create
    detected: DetectedSeries | None   # None: kept as is (confirmed/dismissed, not re-detected)
    member_ids: frozenset[UUID]
    takes_state_from: UUID | None     # S1 takeover: copy user_state and label from this stored id
@dataclass(frozen=True) class SeriesPlan:
    upserts: tuple[PlannedSeries, ...]; deletes: tuple[UUID, ...]
def reconcile_series(stored: Sequence[StoredSeries], detected: Sequence[DetectedSeries]) -> SeriesPlan
RowTuple = tuple[str, str, str, str, str]   # (txn id, account id, ISO date, amount, merchant_key)
def detect_rows(rows: Sequence[RowTuple]) -> tuple[DetectedSeries, ...]   # the child process entry
```
- **Types match the merged code** (Gadfly S6):
  - `RecurringSeries` has no `direction`, `first_date` or currency. `detected_from` derives
    `direction` from the sign of `typical_amount` and `first_date` from the members' earliest
    posted date.
  - Ids are `str | UUID` in `recurring.py`. `detect_rows` builds `PostedTransaction`s with `UUID`
    ids, so every id here is a `UUID`.
  - `PostedTransaction.merchant_key` is a required `str`, so rows whose `merchant_key()` is `None`
    are never sent.
- **Confidence** is quantized with `stored_confidence` before storage. `series_tier` is only ever
  called with the stored value, so the tier is the same before and after a round trip.
- `detect_rows` is the only function the C5b child process runs. It takes primitive tuples (ids,
  account ids, dates, amounts as decimal strings, merchant keys) and imports only `app.domain`.
- `reconcile_series` follows ADR 0028 points 1, 2, 5 and 6, including the matching tie rule and the takeover
  rule (S1). Property tests check the invariants:
  - member sets are pairwise disjoint
  - every detected series appears in exactly one upsert
  - no stored `confirmed`, `dismissed` or labelled id is ever in `deletes`
  - the result doesn't depend on input order
- `series_status`:
  - tolerance: 2 days (weekly, biweekly), 3 days (others)
  - `active` while `as_of <= next + tol`
  - `possibly_cancelled` once `as_of > next + NOMINAL_DAYS // 2` (and past `tol`)
  - `late` in between
- `series_tier` follows ADR 0028 point 4: threshold `Decimal("0.60")`, strictly greater for `bill`.

---

## 3. Schema: one ordered migration plan

One migration per slice. Keel hands out the migration token in this order: **M1 (C3b), then M3
(C4c), then M4 (C5b)**. M2 (I1d) has left this plan (Gadfly S4): it's an import follow-up, and Tide
takes the token whenever it's free. The names M1, M3 and M4 stay as they were, so review
references still hold.

This plan fixes an order, not numbers:
- The builder who holds the token numbers the migration at build time from the Alembic head on
  `main` (next free number, with `down_revision` set to that head), after `git merge --ff-only
  origin/main`.
- Numbers other lanes claimed are never reused, and those lanes' migrations may land in between.
- The revision slug is fixed. The numeric prefix isn't.

| Order | Revision slug | Slice | Needs |
|---|---|---|---|
| M1 | `<n>_rules` | C3b | head at `0009_import_run_review_reason` or later |
| M3 | `<n>_transfer_corrections` | C4c | M1 (helpers) |
| M4 | `<n>_recurring_series` | C5b | M1 (helpers), M3 (it replaces M3's re-point function) |

### 3.0 Conventions (every Phase 4 migration)
- The Phase 1 contract's conventions hold:
  - UUID PKs and `timestamptz`
  - text + CHECK enums
  - `household_id NOT NULL` + index + ENABLE/FORCE RLS + `household_isolation`
  - explicit `ON DELETE`
- **Every leg column of a derived row has two composite FKs** (Bulwark 9):
  - `(x_id, x_account_id) → transaction(id, account_id)` (unique since 0005)
  - `(x_account_id, household_id) → account(id, household_id)`
- Every new per-person policy is RESTRICTIVE: it can only narrow isolation.
- **SQL helper functions** (Bulwark 4) are `LANGUAGE sql STABLE SECURITY INVOKER` with a
  SQL-standard body (`RETURN (...)`), schema-qualified names (`public.account`,
  `public.app_person_id()`, `public.app_household_wide()`) and **no `SET` clause**. They bind at
  creation and stay inlinable. EXECUTE is revoked from PUBLIC and granted to `holdfast_app`. A test
  asserts `prosecdef = false` and `proconfig IS NULL` for both.
- **Trigger functions**:
  - `LANGUAGE plpgsql` with `SET search_path = pg_catalog, public, pg_temp`.
  - Every relation qualified (`public."transaction"`, `public.account`, `public.rule`).
  - A trigger that doesn't find the account, rule or transaction row it needs **raises**, never
    passes.
  - Where a trigger must see rows the caller can't (counts and the re-point in 3.3), it follows the
    0002/0005 pattern. It saves `app.household_wide`, sets it `on` for its own statements only, and
    restores it before returning. Those statements are fixed and touch only rows the trigger's
    `NEW`/`OLD` names. Messages carry ids only.
- Every migration:
  - has a working downgrade
  - passes up/down/up as `holdfast_owner`
  - passes the RLS lint
  - is rehearsed on a local restore of the owner's instance dump before it reaches that instance
    (section 7.5)

### 3.1 M1 `<n>_rules` (C3b)
**Helpers** (ADR 0026 point 1):
```sql
CREATE FUNCTION public.app_account_details_visible(p_account_id uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY INVOKER
    RETURN EXISTS (SELECT 1 FROM public.account a WHERE a.id = p_account_id
                   AND (a.visibility = 'shared' OR a.owner_person_id IS NULL
                        OR a.owner_person_id = public.app_person_id() OR public.app_household_wide()));
CREATE FUNCTION public.app_account_writable(p_account_id uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY INVOKER
    RETURN public.app_household_wide() OR EXISTS (SELECT 1 FROM public.account a WHERE a.id = p_account_id
                   AND (a.owner_person_id IS NULL OR a.owner_person_id = public.app_person_id()));
```
These are 0002's `ACCOUNT_DETAIL_VISIBLE` and `ACCOUNT_DETAIL_WRITABLE`, inlined as frozen text
(the migration imports no app code). The `account` subquery stays under account RLS.

**`rule` rework.** The table has no writer today.
- **Upgrade and downgrade both** count every row across all households first, and raise if any
  exist (Bulwark 11). FORCE RLS binds `holdfast_owner` too, and `app.household_wide` doesn't lift
  household isolation. So the count runs between `ALTER TABLE rule NO FORCE ROW LEVEL SECURITY` and
  `FORCE` in the same migration transaction (ADR 0008, Consequences). A count under RLS would
  always be zero.
- The downgrade raises before it drops anything, so it can't destroy author, scope or conditions,
  or re-add `set` (`NOT NULL`) to a non-empty table.

| Column | Type | Notes |
|---|---|---|
| `author_person_id` | uuid NOT NULL | composite FK → `person(id, household_id)` ON DELETE CASCADE; index |
| `name` | text NOT NULL | CHECK 1–80 chars |
| `enabled` | boolean NOT NULL DEFAULT true | |
| `priority` | integer NOT NULL DEFAULT 100 | CHECK 1–10000 (exists, add CHECK) |
| `conditions` | jsonb NOT NULL | renamed from `match`. CHECK `jsonb_typeof = 'object'`, `conditions->>'v' = '1'`, and each of `amount_equals/min/max` either absent or `jsonb_typeof = 'string'` |
| `category_id` | uuid NOT NULL | composite FK → `category(id, household_id)` ON DELETE CASCADE (categories are hidden, not deleted, per ADR 0022) |
| `account_scope` | text NOT NULL DEFAULT 'own' | CHECK IN ('any','joint','own') |
| `created_by` | text | unchanged CHECK ('user','agent'); Phase 4 writes only `user` |
| `last_applied_at` | timestamptz NULL | set by retroactive apply |
| `updated_at` | timestamptz NOT NULL DEFAULT now() | |

- Dropped: `set`, `hit_count`.
- Added: `UNIQUE (id, household_id)`, and an index on `(household_id, priority, created_at, id)`.
- **No `rule_account` table** (Gadfly S5, first-pass S1). Scope alone filters accounts, so an
  account added later is covered by its kind, and Bulwark's surface shrinks by one table and two
  triggers.

Policies:
- `person_read` FOR SELECT: `public.app_household_wide() OR author_person_id =
  public.app_person_id() OR account_scope IN ('any','joint')`
- `person_insert` / `person_update` / `person_delete`: `public.app_household_wide() OR
  author_person_id = public.app_person_id()` (UPDATE as both USING and WITH CHECK)

Triggers:
- `rule_scope_guard` (BEFORE UPDATE OF `author_person_id`): raises `42501` on any change, so an
  author is fixed for a rule's life. With `rule_account` gone, scope changes need no guard.
- `rule_caps` (BEFORE INSERT OR UPDATE OF `enabled`, `conditions`):
  - Takes `pg_advisory_xact_lock(hashtext('holdfast.rule_caps'), hashtext(NEW.household_id::text))`,
    then counts with `app.household_wide` on (3.0), so another person's `own` rules are counted
    too.
  - Raises `23514` past **200 rules per author** (enabled or not), or past **25 enabled rules with
    `conditions->'description'->>'mode' = 'regex'` per household** (ADR 0024).
  - The service maps these to 409 `conflict` with fixed messages.

**`transaction.category_rule_id`** uuid NULL:
- composite FK `(category_rule_id, household_id)` → `rule(id, household_id)` ON DELETE SET NULL
  (category_rule_id)
- CHECK `category_rule_id IS NULL OR category_source = 'rule'`
- partial index on `category_rule_id` WHERE NOT NULL
- Only `LedgerWriter` sets it (add it to `tests/test_ledger_single_writer.py`).

**Visibility rule.** A person sees a rule if they wrote it or it's `any`/`joint`-scoped. Another
person's `own` rule is never visible.

Section 3.2 (M2) is gone: I1d left this plan.

### 3.3 M3 `<n>_transfer_corrections` (C4c)
**`transfer_rejection`**
| Column | Type | Notes |
|---|---|---|
| `id`, `household_id`, `created_at` | | standard |
| `kind` | text NOT NULL | CHECK IN ('pair','leg') |
| `transaction_id`, `transaction_account_id` | uuid NOT NULL | FKs → `transaction(id, account_id)` CASCADE and `(transaction_account_id, household_id)` → `account` CASCADE. For `pair`, this is the outflow |
| `other_id`, `other_account_id` | uuid NULL | the same two FKs; the inflow |
| `rejected_by_person_id` | uuid NULL | composite FK → `person` ON DELETE SET NULL (rejected_by_person_id). NULL means step 0 wrote it (job provenance is in the audit) |

- CHECKs: `(kind = 'leg') = (other_id IS NULL)`, both `other` columns null together, `other_id <>
  transaction_id`.
- `UNIQUE NULLS NOT DISTINCT (household_id, transaction_id, other_id)`; index on `other_id`.
- Read: `app_account_details_visible(transaction_account_id) AND (other_account_id IS NULL OR
  app_account_details_visible(other_account_id))`.
- Insert/delete: the read predicate AND `(app_account_writable(transaction_account_id) OR
  (other_account_id IS NOT NULL AND app_account_writable(other_account_id)))`.
- No UPDATE grant. The re-point trigger deletes and re-inserts.

**`transfer_suggestion`**
| Column | Type | Notes |
|---|---|---|
| `id`, `household_id`, `created_at` | | standard |
| `person_id` | uuid NOT NULL | composite FK → `person` CASCADE |
| `outflow_id`, `outflow_account_id` | uuid NOT NULL | the two leg FKs, CASCADE |
| `inflow_id`, `inflow_account_id` | uuid NOT NULL | the same |
| `reason` | text NOT NULL | CHECK IN ('no_hint','one_sided_hint','refund_candidate','user_treated_as_spending','ambiguous','likely','other_side_marked') |
| `days_apart` | smallint NOT NULL | CHECK 0–5 |
| `hints` | smallint NOT NULL | CHECK 0–6 |
| `job_run_id` | uuid NULL | composite FK → `job_run(id, household_id)` ON DELETE SET NULL (job_run_id) |

- CHECK `outflow_account_id <> inflow_account_id`; `UNIQUE (person_id, outflow_id, inflow_id)`.
- Indexes: `(household_id, person_id)`, `(outflow_id)`, `(inflow_id)`.
- Read: `(person_id = app_person_id() OR app_household_wide()) AND
  app_account_details_visible(outflow_account_id) AND
  app_account_details_visible(inflow_account_id)`.
- Insert/delete: the same predicate. No UPDATE grant.

**`transfer_link`** (a move between owners, ADR 0027 point 2)
| Column | Type | Notes |
|---|---|---|
| `id`, `household_id`, `created_at` | | standard |
| `outflow_id`, `outflow_account_id` | uuid; `outflow_id` NULL after its row is deleted | FK → `transaction(id, account_id)` ON DELETE SET NULL (outflow_id); `(outflow_account_id, household_id)` → `account` CASCADE |
| `inflow_id`, `inflow_account_id` | the same | the same |
| `origin` | text NOT NULL | CHECK IN ('job','person'). `job`: step 1 marked both legs. `person`: a person accepted the edge, or A1 dissolved a pair. Never derived from `job_run_id`, which retention may null |
| `job_run_id` | uuid NULL | composite FK → `job_run` ON DELETE SET NULL (job_run_id) |

- CHECK `outflow_account_id <> inflow_account_id`.
- `UNIQUE (outflow_id)` and `UNIQUE (inflow_id)`. NULLs are distinct, and a leg is in at most one
  link.
- Read: both accounts detail-visible (ADR 0026 point 2).
- Insert/delete: the read predicate AND either account writable (household-wide passes both).
- No UPDATE grant.

**Triggers** (3.0 rules apply to each):
- `account_guard_transfer_owner`, BEFORE UPDATE OF `owner_person_id` ON `account` (Bulwark 6):
  - When the owner changes, it raises `23514` ("account % has transfer pairs: dissolve them
    first") while any transaction on the account, live or superseded, has a `transfer_group_id`.
  - The existence check runs with `app.household_wide` on.
  - A1's service dissolves those pairs first in the same unit of work (5.0), so the trigger is the
    backstop, not the mechanism.
- `transaction_dissolve_orphan`, AFTER DELETE ON `transaction` FOR EACH ROW WHEN
  (`OLD.transfer_group_id IS NOT NULL`):
  - Clears `is_transfer` and `transfer_group_id` on every remaining row of that group, under the
    caller's RLS. This is safe because both legs have one owner (ADR 0027 point 1).
  - It then checks with `app.household_wide` on. If a row of the group still exists, it raises
    `42501`: the delete is refused rather than leaving a partner the caller couldn't clear.
  - Gadfly B3: account cascades, a future "re-import this document" or an import undo.
- `transaction_supersede_repoint`, AFTER UPDATE OF `superseded_by_id` ON `transaction`, when it
  changes from NULL to a value:
  - With `app.household_wide` on, it moves every `transfer_rejection` and `transfer_link` reference
    to `OLD.id` onto `NEW.superseded_by_id`: `INSERT … SELECT … ON CONFLICT DO NOTHING`, then
    `DELETE` the old rows.
  - It deletes `transfer_suggestion` rows that reference `OLD.id`. The next refresh recomputes
    them.
  - The account id doesn't change (the 0005 FK keeps supersession inside one account), so
    visibility of the moved rows doesn't change either.
  - This is how Bulwark 10's "in the same unit of work" holds even for rejections the superseding
    session can't see.

**Visibility rule.**
- A suggestion is visible only to the person it was computed for, and only while both legs are
  visible to them.
- A rejection or a link is visible to anyone who can see both legs.

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
| `typical_amount`, `amount_low`, `amount_high` | NUMERIC(14,2) NOT NULL | signed (outflow negative). CHECK `amount_low <= typical_amount AND typical_amount <= amount_high`. CHECK the sign agrees with `direction` for all three |
| `is_variable` | boolean NOT NULL | |
| `first_date`, `last_date`, `next_expected_date` | date NOT NULL | CHECK `first_date <= last_date AND last_date < next_expected_date` |
| `confidence` | NUMERIC(4,3) NOT NULL | CHECK 0–1; written from `stored_confidence` |
| `lapsed` | boolean NOT NULL | the detector's flag, used only for merging |
| `user_state` | text NOT NULL DEFAULT 'auto' | CHECK IN ('auto','confirmed','dismissed') |
| `label` | text NULL | CHECK 1–80 |
| `last_job_run_id` | uuid NULL | composite FK → `job_run` ON DELETE SET NULL (last_job_run_id) |

- `UNIQUE (id, household_id)`, `UNIQUE (id, account_id)`; index on `(household_id,
  next_expected_date)`.
- Read: `app_account_details_visible(account_id)`.
- Insert/delete: `app_household_wide()`.
- **Update: USING `app_account_writable(account_id)` and WITH CHECK
  `app_account_writable(account_id)`** (Bulwark 13).
- Trigger `recurring_series_guard` (BEFORE UPDATE): unless `app_household_wide()`, any change other
  than `user_state`, `label` and `updated_at` raises `42501`.

**`recurring_member`** `(household_id, series_id, account_id, transaction_id)`:
- PK `(series_id, transaction_id)`, `UNIQUE (transaction_id)`.
- FKs:
  - `(series_id, account_id)` → `recurring_series(id, account_id)` CASCADE
  - `(transaction_id, account_id)` → `transaction(id, account_id)` CASCADE
  - `(account_id, household_id)` → `account(id, household_id)` CASCADE

  So a member is always on its series' account.
- Read: `app_account_details_visible(account_id)`. Write: `app_household_wide()` only.

**Re-point update.** M4 replaces `transaction_supersede_repoint` so it also moves `recurring_member`
rows from `OLD.id` to the successor. Members re-point instead of vanishing, so a series whose rows
are all superseded keeps its id and state (Gadfly B3). The downgrade restores M3's body verbatim.

**Visibility rule.** A series and its members are visible exactly when the series account's
transactions are. Summary-only viewers see none.

### 3.5 Tests every schema slice adds
- Parametrised RLS tests as `holdfast_app`: household A vs B, private vs shared vs summary_only vs
  joint, and unset context.
- Guessed-id composite FK tests: another household's category, account, transaction, and an
  account id that doesn't match the transaction.
- Helper equivalence (ADR 0026 point 1), plus `prosecdef = false` and no `proconfig` for the
  helpers.
- Trigger tests, run on their own with RLS lifted in a rolled-back transaction (the ADR 0008
  pattern), including the "row not found" cases.
- The exact privilege set for each new table.

---

## 4. Who runs what, and when

| Work | Where | Session | Writes through | Idempotence |
|---|---|---|---|---|
| Account owner and visibility (A1) | request | person | `AccountService.change_ownership` → `LedgerWriter.dissolve_account_pairs` + the account UPDATE, in one unit of work | a repeat with the same values is a no-op |
| Rule CRUD | request | author, person-scoped | `RuleService` (rules aren't ledger fields) | n/a |
| Rules at ingest | statement import request today; sync job later | the importer's session | the matcher runs in `anyio.to_thread.run_sync`, then `NewTransaction(category_id, category_source='rule', category_rule_id)` → `LedgerWriter.insert_transaction` | a re-import inserts nothing (fingerprints) |
| Rule preview | request, `anyio.to_thread` | author | none | pure |
| Rule apply (retroactive) | request | author | `LedgerWriter.apply_rule_bulk` (one UPDATE, precedence in WHERE, exact-count check) | preview token; a second apply changes 0 rows |
| Set / release category, mark / unmark, manual pair, accept / reject suggestion | request | person | `LedgerWriter`, plus `transfer_rejection` / `transfer_link` in the same unit of work | each call is a state transition; a repeat is a 409 or a no-op |
| Supersede a row (I4 and later) | importer or sync | importer's | `LedgerWriter.supersede_transaction`, plus the M3/M4 re-point trigger | supersession is one-to-one (0005) |
| Link cleanup, auto-pair, auto-link | `ledger.refresh` steps 0–1 | household-wide | `pair_transfers` → `apply_transfer_pair` / `apply_transfer_link` with `job_run_id` | paired or linked rows leave the pool |
| Transfer suggestions | `ledger.refresh` step 2 | one `scoped_session` per person with a login | `refresh_person_suggestions`: delete and insert that person's rows | replace per person |
| Recurring detection | `ledger.refresh` step 3 | household-wide | `detect_rows` in the `forkserver` pool, then `reconcile_series` under `FOR UPDATE` | reconcile is a pure function of rows + stored series |

### 4.1 `ledger.refresh` steps (ADR 0029 point 3)
- **Steps 0–1** run in one household-wide session from `ctx.factory`, committed together.
  - Step 0, link cleanup. The rule: the job never leaves a mark of its own whose evidence is gone
    or rejected, and it never changes a mark a person made.
    - (a) A link with a NULL leg (its row was deleted): if `origin = 'job'`, unmark the surviving
      leg when it's still `is_transfer`. Delete the link.
    - (b) A `job` link where one leg is no longer `is_transfer` (a person unmarked it, possibly
      without seeing the link): write the `pair` rejection for the edge if it's missing
      (`rejected_by_person_id` NULL), unmark the other leg, and delete the link.
    - (c) Any link whose edge has a `pair` rejection: delete it. For a `job` link, also unmark any
      leg still marked.
    - A `person` link with one leg unmarked and no rejection stays. It's a half-accepted move, and
      step 2 offers the unmarked side (`other_side_marked`).
    - Audits carry `job_run_id`.
  - Step 1 runs `pair_transfers` over all live history and writes `pairs` (same owner) and `links`
    (different owners).
- **Step 2:** one `scoped_session(household, person)` per person with a login, each its own
  transaction:
  - `transfer_pairing.load_candidates` (a read-only loader that builds no writer, so it works in a
    session without a user)
  - `match_transfers` with that person's visible rejections
  - `review_edges`
  - plus one `other_side_marked` edge for each link visible to the person where a leg they can
    write isn't a transfer and the other leg is (first-pass B1: the second person's half of a move
    the first person accepted)
  - replace that person's `transfer_suggestion` rows

  It never writes a pair or a link (Bulwark 8).
- **Step 3** (from C5b) runs in the runner's `ctx.session`, so it commits with the success mark:
  - Load live, posted, non-transfer rows with a non-NULL `merchant_key`, plus the stored series.
  - `detect_rows` in the pool, with a 120 s timeout.
  - Then `SELECT … FROM recurring_series … FOR UPDATE` (and its members), re-run `reconcile_series`
    on the locked state, and write.
- **Connections:** the lock, the runner's session, and one step session at a time make three per
  job. At `MAX_JOBS = 5`, that's 15, inside the pool (5 + 10 overflow). C4d corrects the comment in
  `app/worker/main.py`.

### 4.2 Concurrency rules (Gadfly B5)
- **Rejections and links lock their legs.** Every writer method that inserts or deletes a
  `transfer_rejection` or `transfer_link` first locks, through `_lock_group` (id order), every
  transaction it names that the session can write.
- **Auto writers lock both legs, then re-check.** `apply_transfer_pair` and `apply_transfer_link`
  lock both legs, then re-check under the lock:
  - each leg is live, not pending, not `is_transfer` and has no group
  - `NOT EXISTS` a `pair` rejection for the edge, or a `leg` rejection for either leg

  Postgres READ COMMITTED gives the re-check a fresh snapshot once the lock is granted, so a
  rejection committed while the job waited is seen. A rejection that commits later found the legs
  already paired, and its writer returns 409, because it re-checks under its own lock.
- **Step 3 never overwrites a person's choice.** It reconciles against series locked `FOR UPDATE`
  after detection, not against the snapshot taken before it.
  - Deletes carry `WHERE user_state = 'auto' AND label IS NULL`.
  - Upserts never write `user_state` or `label`, except the takeover rule, which copies values read
    under the lock.
  - A `PATCH /recurring/{id}` holds the row lock for its update. If it arrives after the job's lock,
    it runs after the job commits. If the job deleted that `auto` series, the PATCH returns 404 and
    the UI refetches.
- **Step 2 can be stale, never wrong.** A suggestion computed before an accept or a rejection may
  be re-inserted. The review list hides it, because it re-checks live state on read (5.3).

### 4.3 Triggers for `ledger.refresh` (Keel decision; Gadfly S3)
- **Coalesced per household.** Each of these enqueues key `refresh:auto:<UTC time floored to 2
  minutes, as %Y%m%dT%H%MZ>`, for example `refresh:auto:20261121T1004Z`:
  - a statement upload that inserted at least one row (once per upload request, after commit)
  - an A1 ownership change
  - accept, reject, mark, unmark or a manual pair
  - each committed sync run (Phase 2a)
- **Deferred to the bucket's end.** The run is deferred until the bucket's end plus 5 s (arq
  `_defer_until`).
  - Every enqueue happens after its rows commit, and every enqueue for a bucket happens before the
    bucket ends. So the run starts after every row of every upload in that bucket is committed, and
    no tail check is needed.
  - A first load of 125 statements becomes one run per 2 minutes of uploading, not 125 runs.
  - A test fixes the clock and asserts enqueue time ≤ bucket end < run start.
- **"Check again"** (`POST /ledger/refresh`) enqueues `refresh:manual:<UTC minute, as
  %Y%m%dT%H%MZ>`, for example `refresh:manual:20261121T1005Z`, with no defer. Both formats match
  `IDEMPOTENCY_KEY_PATTERN` (`^[A-Za-z0-9][A-Za-z0-9:._-]*$`), which an ISO time with `+00:00`
  wouldn't (Bulwark 14).
- **Spec:**
  - payload `{household_id}`
  - `RetryPolicy(max_attempts=3, base=30 s)`
  - `timeout=timedelta(seconds=300)`, set explicitly, not by default (Gadfly S2)
  - detection's own timeout is 120 s
- **Redis down after commit:** the write still succeeds. The failure is logged by class name, and
  "Check again" retries.

### 4.4 Rows a person has touched, and rows that change identity
- `category_source = 'user'` (including a cleared category): rules never change it (ADR 0022). It's
  counted in `skipped_user`. "Hand back to automation" (`release`) clears the source, and the next
  ingest-time or retroactive rule may then set it.
- Split rows: rules skip them. The matcher only suggests them.
- A user-set category of kind `transfer` or `excluded` doesn't protect a row from pairing (ADR
  0027 point 4).
- Unmarking:
  - Unmarking a pair writes a `pair` rejection.
  - Unmarking a single leg with a visible link writes a `pair` rejection for the link's edge and
    unmarks every leg of the link the session can write. It doesn't delete the link: step 0 (c)
    does, and for a `job` link it also undoes the job's mark on a leg this person couldn't write.
  - Unmarking a single leg with no visible link writes nothing. Step 0 (b) turns a hidden `job`
    link into a `pair` rejection.
  - "Never a transfer" writes a `leg` rejection.
  - A manual pair or a mark lifts the matching rejection.
- **Supersession** (`LedgerWriter.supersede_transaction(old_id, new_id)`, the only writer of
  `superseded_by_id`, added to the single-writer lint), one unit of work:
  - It locks both rows and the old row's group. The new row must be live and fresh: not a transfer,
    no splits, source not `user`.
  - It moves to the new row:
    - a user category (`category_id`, `category_source = 'user'`)
    - the transfer state: `is_transfer`, and `transfer_group_id` when the amounts are equal.
      Otherwise the pair dissolves into two single legs, with no rejection.
    - the splits, when the amounts are equal. Otherwise it raises `LedgerRuleError("split row
      changed amount")`, and the caller flags the run `needs_review`.
  - It clears transfer state on the old row, then sets `superseded_by_id`. The M3/M4 trigger
    re-points rejections, links and recurring members and deletes stale suggestions.
  - Audit `ledger.superseded {new_id}`.
  - Phase 4 has no caller. I4 ("Plaid wins") must use this method.
- **Deletion:** `transaction_dissolve_orphan` clears a deleted leg's partner (3.3). `unmark_transfer`
  also accepts a group with one surviving leg (Gadfly B3).
- Recurring: detection ignores `is_transfer` rows, and user categories don't matter. `confirmed`
  and `dismissed` stick. Setting a label sets `confirmed` unless the series is `dismissed` (Gadfly
  S1).
- Rules:
  - Disabling or deleting a rule leaves its categories (C3a semantics). Deleting sets
    `category_rule_id` to NULL on those rows (the source stays `rule`).
  - Editing a rule leaves `category_rule_id` in place: "Set by rule: <name>" names the rule that
    set it, even if it has changed since.
  - A disabled rule can be previewed but not applied (409 `conflict`).

### 4.5 Service changes outside the new modules
- `LedgerWriter.apply_category(..., rule_id: UUID | None = None)` sets `category_rule_id` when the
  source is `rule` and clears it otherwise. `set_category` and `release_category` clear it.
- `LedgerWriter.apply_rule_bulk(rule_id, category_id, transaction_ids) -> int`:
  - one `UPDATE ... WHERE id = ANY(:ids) AND superseded_by_id IS NULL AND category_source IS
    DISTINCT FROM 'user' AND NOT EXISTS (split)`, under RLS
  - raises `LedgerRuleError("preview_stale")` unless the row count equals `len(ids)`
  - one audit row, `rule.applied {rule_id}`, with the acting user. **No count** (Bulwark 12): an
    `any` or `own` rule touches the author's private rows, and `audit_log` is household-readable.
    The author reads the count under RLS.
- `LedgerWriter._check_pair` rejects a different `owner_person_id`, where NULL is a value (ADR
  0027).
- `unmark_transfer(transaction_id, *, never: bool = False)` follows 4.4.
  `mark_transfer_pair` / `mark_transfer_single` delete a matching rejection.
- New writer methods:
  - `mark_transfer_link(out_id, in_id)`: a person, both legs visible, marks the writable legs and
    inserts the link
  - `apply_transfer_link(out_id, in_id, *, job_run_id)`
  - `dissolve_account_pairs(account_id)`
  - `supersede_transaction(old_id, new_id)`
- **Provenance** (Bulwark 7): `pair_transfers`, `apply_transfer_pair` and `apply_transfer_link`
  raise unless `user_id` is set, or the session is household-wide **and** `job_run_id` is set.
- `pair_transfers` loads rejections (mapped to the matcher's frozensets) and computes `protected` per
  ADR 0027 point 4.
- `rule.*` audit details carry ids and changed field names only, never names, patterns or amounts
  (Bulwark 12): `rule.created {rule_id}`, `rule.updated {rule_id, fields: [...]}`,
  `rule.deleted {rule_id}`.

---

## 5. API surface

All under `/api`:
- JSON, with errors as `{"error": {"code", "message"}}`
- `X-Holdfast-CSRF: 1` on every non-GET
- money as decimal strings (`MoneyStr`), dates ISO
- thin routers (ADR 0011)

Schema names are the Pydantic class names, so `make api-types` output is predictable. `ApiError`
gains one code: **`preview_stale` (409)**, added to `web/src/api/contracts.ts` in the same PR.

### 5.0 Accounts (A1)
`AccountOut` gains:
- `owner: "me" | "joint" | "other"`
- `owner_person_id: uuid | null`
- `can_edit: bool`
- `can_change_owner: bool`

`ImportBlockOut` gains `account_owner: "me" | "joint" | "other"` and `account_visibility`, so the
import result can offer the change.

| Method + path | Body → response | Errors |
|---|---|---|
| PATCH `/accounts/{id}` | `AccountOwnershipIn {owner?: "me" \| "joint" \| uuid, visibility?: "private" \| "shared" \| "summary_only"}` (at least one) → `AccountOut` | 404 (missing, invisible or not writable), 403 `forbidden` (a joint account claimed by someone without the household-owner role), 422 (unknown person; `visibility` given for a joint account, which is always visible) |

- Who may:
  - The account's owner may change visibility, make the account joint, or give it to another
    person.
  - Any member may change a joint account's visibility, which is always effectively shared.
  - A joint account may be claimed by one person only by the household owner
    (`principal.is_owner`), because a claim takes write access from the other members.
- One unit of work:
  1. Lock the account row.
  2. If the owner changes, `LedgerWriter.dissolve_account_pairs(account_id)`: every pair touching
     the account becomes two single legs (`is_transfer` stays true and no rejection is written),
     audited as `ledger.transfer_dissolved {group_id, reason: "ownership_changed"}`.
  3. The account UPDATE.
  4. Audit `account.ownership_changed {account_id, owner_kind_before, owner_kind_after,
     visibility_before, visibility_after}`, with ids only.
- After commit, enqueue `refresh:auto:<bucket>` (from C4d).
- Links on the account stay: a link is two single legs and is valid between any owners. Once
  C4c lands, `dissolve_account_pairs` writes a `person` link for each pair it dissolves, so the
  counterpart survives (the changer can see and write both legs, because they had one owner).
- `rule` rows need nothing: the engine checks the account's owner when it matches (ADR 0026,
  Consequences).
- Statement import still makes the uploader the owner, with `private` (unchanged). The result view
  shows "Owner: you · private · Change".

### 5.1 Transactions (C6a; transfer fields from C4e; `category_rule_id` from C3e)
`TransactionOut` gains:
| Field | Type | From |
|---|---|---|
| `merchant_key` | `str \| null` | C6a |
| `category_id` | `uuid \| null` | C6a |
| `category_source` | `"user" \| "rule" \| "plaid" \| "ml" \| null` | C6a |
| `has_splits` | `bool` | C6a |
| `can_edit` | `bool` (`app_account_writable(account_id)`) | C6a |
| `category_rule_id` | `uuid \| null` (null unless the reader can see that rule) | C3e |
| `transfer_group_id` | `uuid \| null` | C4e |
| `transfer_counterpart_id` | `uuid \| null`: the other leg of the group or of a link, only when the reader can see it | C4e |

| Method + path | Body → response | Errors |
|---|---|---|
| GET `/transactions` | existing query + `category_id[]`, `uncategorized: bool`, `category_source`, `rule_id` (C3e), `transfer: "only" \| "exclude"` (keeps `exclude_transfers`) → `TransactionPage` | 422 |
| GET `/transactions/{id}` | → `TransactionOut` | 404 |
| PUT `/transactions/{id}/category` | `CategoryChoiceIn {category_id: uuid \| null}` → `TransactionOut` | 404 (missing, invisible or not writable), 409 `conflict` (superseded) |
| POST `/transactions/{id}/category/release` | → `TransactionOut` | 404, 409 |
| POST `/transactions/{id}/transfer/mark` | → `TransactionOut` (single leg) | 404, 409 (already a transfer) |
| POST `/transactions/{id}/transfer/unmark` | `TransferUnmarkIn {never: bool = false}` → `TransactionUnmarkOut {legs: TransactionOut[]}` | 404, 409 |
| POST `/transfers/pairs` | `TransferPairIn {outflow_id, inflow_id}` → `TransferPairOut {group_id, legs: TransactionOut[2]}` | 404, 409 (different owners, amounts or currency; already a transfer) |

The last three are C4e.

### 5.2 Rules (C3e)
```
DescriptionMatch   {mode: "contains" | "starts-with" | "exact" | "regex", value: str (1–128)}
RuleConditionsIO   {description: DescriptionMatch | null, merchant_key: str | null (≤ 64),
                    amount_sign: "outflow" | "inflow" | null, amount_equals: MoneyStr | null,
                    amount_min: MoneyStr | null, amount_max: MoneyStr | null,
                    date_from: date | null, date_through: date | null}      # at least one set
RuleIn             {name: str (1–80), enabled: bool = true, priority: int (1–10000) = 100,
                    conditions: RuleConditionsIO, account_scope: "any" | "joint" | "own",
                    category_id: uuid}
RuleOut            RuleIn fields + {id, author_person_id, is_mine: bool, hit_count: int,
                    status: "active" | "unavailable" | "needs_review",
                    visible_to: "household" | "only_me",
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
- `status` is computed on read:
  - `unavailable`: a `regex` rule that `compile_pattern` refuses on this server
  - `needs_review`: a `merchant_key_version` mismatch
- Rules that aren't `active` are skipped at ingest, in preview and in apply, and counted in one log
  line (`rules.skipped`, count and reason only).
- `hit_count` comes from one grouped query under the reader's RLS, never per rule.

| Method + path | Body → response | Errors |
|---|---|---|
| GET `/rules` | → `RuleList` (every rule the reader can see) | |
| POST `/rules/preview` | `RuleIn` → `RulePreviewOut` (a draft; nothing saved) | 422 |
| POST `/rules` | `RuleCreateIn` → 201 `RuleCreateOut`; with `apply`, create and apply in one transaction | 422, 409 `preview_stale` (nothing saved), 409 `conflict` (200 rules per author, or 25 enabled pattern rules per household) |
| GET `/rules/{id}` | → `RuleOut` | 404 |
| PATCH `/rules/{id}` | partial `RuleIn` → `RuleOut` (never applies) | 404, 403 `forbidden` (visible, not yours), 422, 409 `conflict` (caps) |
| DELETE `/rules/{id}` | → 204 (categories stay) | 404, 403 |
| POST `/rules/{id}/preview` | → `RulePreviewOut` | 404 |
| POST `/rules/{id}/apply` | `RuleApplyIn` → `RuleApplyOut` | 404, 403, 409 `preview_stale`, 409 `conflict` (disabled) |

- Validation (422, fixed messages, never echoing input):
  - an unknown category
  - `amount_min > amount_max`
  - a pattern RE2 or the cost probe rejects (ADR 0024)
  - before C3d, any `mode: "regex"` ("Patterns aren't available yet.")
- **The 422 for patterns covers preview, create and PATCH alike**, because all three build
  conditions through `compile_pattern` (first-pass B3).
- Rate limit: preview and apply share 30 per minute per user (the existing Redis limiter, 429
  `rate_limited`).

### 5.3 Transfers review (C4e)
```
TransferEdgeOut    {suggestion_id: uuid, outflow_id: uuid, inflow_id: uuid,
                    reason: "no_hint" | "one_sided_hint" | "refund_candidate" |
                            "user_treated_as_spending" | "ambiguous" | "likely" |
                            "other_side_marked",
                    days_apart: int, can_pair: bool, can_mark_legs: bool}
TransferGroupOut   {key: str, legs: TransactionOut[], edges: TransferEdgeOut[]}
TransferReviewPage {groups: TransferGroupOut[], next_cursor: str | null}
TransferAcceptOut  {result: "paired" | "linked", legs: TransactionOut[]}
TransferRejectIn   {scope: "pair" | "legs" = "pair"}
```
| Method + path | Body → response | Errors |
|---|---|---|
| GET `/transfers/review?limit=1..50&cursor=` | → `TransferReviewPage` | 422 |
| POST `/transfers/suggestions/{id}/accept` | → `TransferAcceptOut` | 404, 409 (a leg changed) |
| POST `/transfers/suggestions/{id}/reject` | `TransferRejectIn` → 204 | 404 |

- **Review list:**
  - Groups come from `group_edges` over the reader's suggestions that are still live: legs live,
    no matching rejection.
  - **A group is hidden only when every leg the reader can write is already a transfer**
    (first-pass B1). So the second person still sees their half of a move the first person
    accepted.
  - Newest leg first. `key` is the smallest suggestion id.
- **Accept:**
  - `paired` (`mark_transfer_pair`) when `can_pair`: same owner, both legs writable.
  - Otherwise `linked` (`mark_transfer_link`): each writable leg is marked single, and the link is
    inserted if it's missing.
- **Reject:**
  - `pair` writes a pair rejection.
  - `legs` writes a leg rejection for each writable leg ("Never a transfer").

### 5.4 Recurring (C5d)
```
RecurringOut       {id, account_id, currency, label: str | null, merchant_key, direction,
                    cadence, typical_amount: MoneyStr, amount_low: MoneyStr, amount_high: MoneyStr,
                    is_variable, first_date, last_date, next_expected_date,
                    status: "active" | "late" | "possibly_cancelled", tier: "bill" | "possible",
                    user_state: "auto" | "confirmed" | "dismissed", confidence: DecimalStr,
                    member_count: int, can_edit: bool}
RecurringList      {items: RecurringOut[], as_of: date}   # ≤ 1,000, ordered next_expected_date, id
RecurringDetailOut RecurringOut + {members: TransactionOut[] (newest 24, live rows only)}
RecurringPatch     {user_state?: "auto" | "confirmed" | "dismissed", label?: str | null}
```
| Method + path | Body → response | Errors |
|---|---|---|
| GET `/recurring?include_dismissed=false&direction=` | → `RecurringList` | 422 |
| GET `/recurring/{id}` | → `RecurringDetailOut` | 404 |
| PATCH `/recurring/{id}` | `RecurringPatch` → `RecurringOut`. A non-null `label` on an `auto` series also sets `confirmed` | 404, 403 (visible, account not writable), 422 |

`currency` comes from the series' account. `member_count` and members count live rows only
(`superseded_by_id IS NULL`).

### 5.5 Refresh and status (C4d; `recurring_count` from C5d)
| Method + path | Body → response |
|---|---|
| GET `/ledger/status` | → `LedgerStatusOut {refresh: {status: "queued" \| "running" \| "retrying" \| "succeeded" \| "failed", finished_at: datetime \| null} \| null, transfer_review_count: int, recurring_count: int}` (counts under the reader's RLS) |
| POST `/ledger/refresh` | → 202 `RefreshOut {run_id: uuid, status: str, created: bool}`; 503 `server_error` if the queue is unreachable |

### 5.6 Step-up re-auth
None. The re-auth list (security rule: adding an Item, export, package generation, PSBT build) is
about secrets, data leaving the instance, and money movement. Phase 4 endpoints:
- edit labels on the person's own rows
- are bounded by RLS to rows they may write
- are confirmed explicitly where they're bulk (the preview token)

An ownership change moves visibility inside the household and never off the instance. Every write
is audited: `account.*`, `ledger.*`, `rule.*`, `transfer.rejected`, `recurring.updated`.

### 5.7 Pagination
Cursor pagination (opaque `next_cursor`, `limit`) on `/transactions` (existing) and
`/transfers/review`. Rules (≤ 200 per author) and recurring (≤ 1,000) return whole lists.

---

## 6. Screens (Direction 01, semantic tokens, money in mono, 375 px first, 44 px targets)

Only what acceptance needs. No new nav entries: Transactions gains tabs, Settings gains Rules.

0. **Account owner control (A1w).** Transactions → accounts panel, plus the import result view.
   - An account row shows "Yours · private", "Joint" or "<name>'s · shared", and a "Change" action
     when `can_change_owner`.
   - The sheet:
     - owner: me, joint, or another person
     - visibility: private, shared, summary only
     - a one-line consequence ("Everyone in the household will see these transactions")
     - "Transfers on this account become single entries" when the account has pairs
   - The import result shows the owner and visibility of each block's account, with "Change".
1. **Transactions → Activity (changed, W1).**
   - Each row shows a category chip, "Transfer", or "Needs category". Until rules land (C3e, W2),
     every statement row shows "Needs category": W1's value is manual categorisation.
   - Filters gain category, "needs category" and transfers.
   - Tapping a row opens the existing `ResponsiveSheet` (a bottom sheet on mobile) with:
     - a category picker (the grouped tree from `GET /categories`)
     - "Hand back to automation" when `category_source = 'user'`
     - the rule that set the category ("Set by rule: <name>", or "Set by another person's rule")
     - from W3: "Mark as transfer", "Not a transfer", and "Never a transfer" in the overflow menu
   - Read-only rows (`can_edit = false`) show no actions.
   - After a category change, a toast offers "Make a rule for <merchant_key>?".
2. **Rule sheet (W2).**
   - Opened from that toast, from "Make a rule from this transaction" in the row sheet (prefills
     `merchant_key` and `amount_sign`), or from Settings → Rules → New.
   - Fields:
     - category
     - match: merchant, or description contains / starts with / is exactly / pattern
     - money in or out, and an optional amount range
     - **where it applies**: "Joint and my accounts" (`any`), "Joint accounts only" (`joint`),
       "My accounts only" (`own`)
     - advanced: name, priority, dates
   - **The default scope is `own` when the source row's account isn't joint, or the sheet was opened
     from Settings. It's `any` when the source row is on a joint account** (Bulwark 3).
   - **The sheet always says who can see the rule:** "Everyone in your household can see this rule,
     its name and what it matches", or "Only you can see this rule". The line updates as the scope
     changes.
   - "Preview" shows "N past transactions will change", with up to 25 of them and the skipped counts
     on one line.
   - Buttons: "Save and apply to N" (creates with the token) and "Save for new transactions only".
     `preview_stale` re-runs the preview and says the count changed.
3. **Settings → Rules (new, W2).**
   - A list of the rules the person can see: name, category, hit count, status, the enabled switch
     (own rules), and the author for others' rules (read only).
   - Edit, delete, and "Apply to past transactions" (preview first).
4. **Transactions → Transfers to review (new tab, W3).**
   - The tab carries the count from `/ledger/status` as a badge.
   - One card per group:
     - the legs (date, account, amount in mono)
     - a plain reason, such as "Same amount, two days apart, no transfer wording", or for `likely`,
       "Looks like a transfer between your accounts", or for `other_side_marked`, "The other side is
       already marked as a transfer"
   - Single-edge cards: "It's a transfer" and "Not a transfer".
   - Multi-edge cards: one row per candidate with the same two actions. Accepting one hides the rest
     of that card.
   - "Never a transfer" sits in an overflow menu.
   - "Check again" calls `POST /ledger/refresh` and shows the last run time.
5. **Transactions → Recurring (new tab, W4).**
   - Sections: Bills (outflows, tier `bill`), Income (inflows, tier `bill`), Maybe recurring (tier
     `possible`, collapsed), and a filter for dismissed.
   - Row: label or merchant, cadence, typical amount in mono ("about" when variable), next date, and
     a status chip (active, late, possibly cancelled).
   - Sheet: "Yes, this recurs", "Not recurring", rename (a name also confirms), last postings.

Changed pages: Transactions (accounts panel, tabs, row chips, sheet), Import (result view), and
Settings (Rules section). MSW handlers in `web/src/mocks/ledger.ts`, `rules.ts` and `recurring.ts`
mirror section 5, so Prism can build before the backend lands. Each new mock module is registered
in `web/src/mocks/handlers.ts` by the slice that adds it. MSW is for vitest and local dev only: the
acceptance evidence for each screen is its e2e (criterion 5).

---

## 7. Story slices

Kinds:
- **pure:** no database and no pnpm (Codex-suitable through firstchair)
- **db:** Ledger or Tide, needs Postgres
- **web:** Prism

Each slice is one PR. Gates:
- Bulwark gates anything that touches RLS migrations, new write endpoints, jobs or the regex
  engine.
- Ledger gates money paths.
- Prism gates web brand.
- Sounding gates acceptance.

### 7.1 Order
| Wave | Slices (parallel inside a wave) |
|---|---|
| 0 | **C3b** (M1), **A1**, C3c, C4b, C5c |
| 1 | **A1w**, C6a, C3d |
| 2 | W1, **C3e** |
| 3 | W2, **C4c** (M3) |
| 4 | **C4d** |
| 5 | **C4e**, **C5b** (M4) |
| 6 | W3, **C5d** |
| 7 | W4 |
| 8 | C7 (acceptance) |

**Soonest visible:**
- **A1 → A1w** lets the owner make accounts joint or give them to the other person, so the second
  person has data before any transfer slice runs.
- **C3c → C6a → W1** puts categories on Transactions without waiting on C3b's RLS gate (first-pass
  S2). `category_rule_id` arrives with C3e.
- Then C3d + C3e + W2 (rules usable).
- Then C4c + C4d + C4e + W3 (transfers).
- Then C5b + C5d + W4 (recurring).
- Recurring stays after transfers. Otherwise unpaired card payments show as monthly "bills", and a
  person could confirm them, which leaves a hollow confirmed series behind once pairing lands
  (Gadfly S4, rejected in part).

### 7.2 Slices
| ID | Slice | Kind / owner | Depends | Gates | Acceptance evidence |
|---|---|---|---|---|---|
| **C3b** | M1 (rules), helper functions, `Rule` model, `category_rule_id`, `NewTransaction.category_rule_id`, `apply_category(rule_id)`, `apply_rule_bulk` | db · Ledger (token) | — | Bulwark, Ledger | RLS matrix for `rule`; `rule_scope_guard` and `rule_caps` (two sessions racing past a cap); guessed-id FKs; helper equivalence and `prosecdef = false`; bulk apply exact-count and precedence; up/down/up, **including a downgrade that raises on a non-empty table** |
| **A1** | `PATCH /accounts/{id}`, `AccountService`, `LedgerWriter.dissolve_account_pairs`, `AccountOut` / `ImportBlockOut` owner fields | db · Ledger | — | Bulwark, Ledger | Owner, joint and other-person matrix (who may, 404 vs 403); a claim of a joint account needs the household owner; a pair on the account becomes two single legs in the same unit of work and both stay excluded; audit detail has ids only; summary-only viewers get 404 |
| **A1w** | Account owner sheet; the accounts panel and import result show the owner | web · Prism | A1 | Prism, Sounding | vitest; 375 px; **e2e `accounts.e2e.ts`**: upload a synthetic statement, change the owner to joint from the result view, and see it in the accounts panel |
| **C3c** | `merchant.py`, `rule_regex.py` (refuses everything), rules v2 (scope, splits, ordering, `preview_for`, `preview_token`, codec with `merchant_key_version`) | pure · Codex | — | Ledger, Bulwark | Table and property tests; draft token = saved token; order independence; the `ast` import test; `repr(DescriptionCondition)` has no value |
| **C4b** | transfers v2 (rejections, `links`, joint-as-owner, `review_edges` with `likely`, `group_edges`) | pure · Codex | — | Ledger | A rejected edge is never paired, linked or suggested over 500 random pools; joint versus owned gives `links`; a hidden competitor in the household view still gives a `likely` edge in the person's view (Gadfly B2); grouping is order-independent |
| **C5c** | `recurring_series.py` (`series_status`, `series_tier`, `stored_confidence`, `detected_from`, `reconcile_series`, `detect_rows`) | pure · Codex | — | Ledger | Property tests for the ADR 0028 invariants; tie and takeover rules; the lapsed-predecessor merge; the PLAN 1.5-interval boundary table; the tier at 0.6004 equals the tier at 0.600 |
| **C6a** | Transaction category API (5.1, C6a rows), `TransactionOut` fields, filters | db · Ledger | C3c, A1 (shared files) | Bulwark, Ledger | API tests across private/shared/summary_only/joint; `make api-types` diff; superseded → 409 |
| **W1** | Activity tab chips, filters, row sheet with the category picker and release, rule toast stub | web · Prism | C6a (mocks earlier) | Prism, Sounding | vitest; 375 px; read-only rows show no actions; **e2e `activity.e2e.ts`**: upload, set a category, filter "needs category" |
| **C3d** | RE2 in `rule_regex.py`, cost probe, dependency pin, corpus gate, `docs/user/rules.md` | pure + lockfile · Ledger | C3c | Bulwark, Ledger | ADR 0024 point 7 gate (required, on CI); probe rejects the corpus' blow-ups; `import re2` only in one module; a failed `import re2` gives `unavailable`; pip-audit clean |
| **C3e** | `RuleService`, `/api/rules`, ingest-time rules in statement import (matcher in a thread), `preview_stale` code, `category_rule_id` in `TransactionOut` | db · Ledger | C3b, C3c, C6a (C3d for regex) | Bulwark, Ledger | Preview count = applied count; a second apply = 0; scope across two people (`own` invisible to the other; `any` never touches the other's private rows); caps → 409; audit details ids only; a re-import inserts nothing and keeps categories; bounded work (fixed query count per preview/apply, one UPDATE per apply) |
| **W2** | Rule sheet, preview, Settings → Rules, "make a rule" entry points | web · Prism | C3e (mocks earlier) | Prism, Sounding | vitest for the preview → save-and-apply flow and the stale path; scope default by source account; visibility line; 375 px; **e2e `rules.e2e.ts`**: make a rule from a row, preview, save and apply, see the list in Settings |
| **C4c** | M3, transfer models, writer changes (links, rejections, `supersede_transaction`, unmark rules), `pair_transfers` v2 + `load_candidates` | db · Ledger (token) | C3b, C4b, A1 | Bulwark, Ledger | See 7.3 C4c |
| **C4d** | `ledger.refresh` job steps 0–2, `refresh_person_suggestions`, `request_refresh` helper, arq pool in the API, `defer_until` in `enqueue`, enqueue after import and ownership change, 5.5 endpoints | db · Ledger | C4c | Bulwark, Ledger | See 7.3 C4d |
| **C4e** | Transfer endpoints (5.1 transfer rows, 5.3), `TransactionOut` transfer fields, enqueue after edits | db · Ledger | C4d, C6a | Bulwark, Ledger | Accept `paired` vs `linked`; the second person still sees their half; reject `pair` vs `legs`; 409 on a changed leg; the guard test still forbids `pair_transfers` / `apply_transfer_*` in `app/api/` |
| **W3** | Transfers to review tab, badge, sheet transfer actions | web · Prism | C4e | Prism, Sounding | vitest for accept / reject / multi-edge cards; 375 px; **e2e `transfers.e2e.ts`**: upload synthetic checking and card statements, see a card payment excluded, accept one suggestion, unmark it |
| **C5b** | M4, recurring store service, job step 3 with the `forkserver` pool | db · Ledger (token) | C5c, C4d | Bulwark, Ledger | See 7.3 C5b |
| **C5d** | Recurring endpoints (5.4), `recurring_count` | db · Ledger | C5b | Bulwark, Ledger | A label confirms; dismissed hidden by default; members are live rows only; 403 for a visible, non-writable series |
| **W4** | Recurring tab | web · Prism | C5d | Prism, Sounding | Sections and statuses render at 375 px; **e2e `recurring.e2e.ts`**: upload a synthetic statement with a monthly bill, refresh, confirm it, rename it |
| **C7** | Eval harness, real-data run, Phase 4 acceptance (`docs/phases/PHASE-4.md`) | Sounding (Ledger for the real-data loader) | all | Sounding | Section 0, sections 7.4 and 7.5 |

e2e tests run against the real API on a throwaway local stack (its own compose project name and
ports, never the dev stack's), seeded by uploading the synthetic statements in
`backend/tests/fixtures/statements/` through the API. They follow `web/e2e/first-run.e2e.ts`. CI
doesn't run e2e yet. Sounding runs them at acceptance, and each web slice's author runs its own
before review. Wiring them into CI is a Harbor follow-up.

### 7.3 Brief text each slice carries (Bulwark and Gadfly items)
Keel copies these into the slice's brief. Each is required for that slice's approval.

- **C3b:**
  - Helpers exactly as 3.0 and 3.1: `SECURITY INVOKER`, a SQL-standard body, qualified names, no
    `SET`, and a `prosecdef = false` test (Bulwark 4).
  - Trigger functions set `search_path` and qualify relations, and a trigger that doesn't find its
    row rejects (Bulwark 4).
  - The downgrade counts and raises before it drops anything (Bulwark 11).
  - `rule_scope_guard` fires on `author_person_id` (Bulwark 11).
  - `rule_caps` with the advisory lock (ADR 0024 cap).
- **C3c:**
  - Delete the C3a `re` validator. Until C3d, `compile_pattern` raises `RuleValidationError("Patterns
    aren't available yet.")` for every input (Bulwark 2).
  - The `ast` test: `rule_regex.py`, `app/domain/rules.py` and `app/services/rules.py` import
    neither `re` nor `regex` (Bulwark 2).
  - `DescriptionCondition.value` is `field(repr=False)` (Bulwark 12).
- **C3d:**
  - ADR 0024 points 2, 4b, 5 and 7 as written: no fallback, the cost probe at 10 µs mean,
    `MAX_SUBJECT_LENGTH = 256`, and the gate (each corpus pattern either rejected by
    `compile_pattern` or 50,000 searches of 1,024-character subjects in under 1 s on CI, with the
    three DFA-blow-up shapes in the corpus) (Bulwark 1).
  - Record the CI numbers in the PR.
- **C3e:**
  - The ingest matcher runs in `anyio.to_thread.run_sync` (Bulwark 1).
  - The 25 enabled `regex` rules per household → 409 (Bulwark 1).
  - A rule from a row on a non-joint account defaults to `own`, and `RuleOut.visible_to` drives the
    sheet's line (Bulwark 3).
  - `rule.*` audit details are ids and changed field names only, and `rule.applied` carries no
    count (Bulwark 12).
- **C4b:**
  - Step 2's results never become pairs. `pairs` and `links` in a person's view are `likely` edges
    (Bulwark 8, Gadfly B2).
- **C4c:**
  - `account_guard_transfer_owner` refuses an owner change while any row on the account has a
    `transfer_group_id`. A1's dissolve runs first (Bulwark 6).
  - Provenance: raise unless `user_id` is set, or the session is household-wide and `job_run_id` is
    set, in `pair_transfers`, `apply_transfer_pair` and `apply_transfer_link` (Bulwark 7).
  - Every derived-row leg column has the `(…_account_id, household_id) → account(id,
    household_id)` FK (Bulwark 9).
  - `supersede_transaction` re-points rejections (and links) in the same unit of work through
    `transaction_supersede_repoint` (Bulwark 10).
  - `transaction_dissolve_orphan` (Gadfly B3). The auto writers' locked re-check of rejections
    (4.2, Gadfly B5). Tests:
    - unmark, then rerun: never re-paired
    - delete one leg, then unmark the other: succeeds (no orphan is left)
    - unmark, then supersede, then rerun: nothing re-paired
    - a rejection committed while the job holds no lock is honoured
    - a cross-owner edge in the household view is linked, and either person can undo their leg
    - a hidden `job` link's unmark becomes a `pair` rejection at step 0, and the job undoes its
      own mark on the other leg
    - a half-accepted `person` link gives the second person an `other_side_marked` suggestion
    - `dissolve_account_pairs` writes a `person` link per dissolved pair
- **C4d:**
  - Step 2 never writes pairs or links. Record the leftover inference in ADR 0026 (done here)
    (Bulwark 8).
  - Idempotency keys exactly as 4.3. The manual key matches `IDEMPOTENCY_KEY_PATTERN`, as in
    `refresh:manual:20261121T1005Z` (Bulwark 14).
  - Coalescing: enqueue time ≤ bucket end < run start.
  - The worker comment on connections. The transfer-pairing guard test allows only
    `app/jobs/handlers/ledger_refresh.py`. The household-wide guard's allowlist gains that file
    (the tightening itself is G1, Keel's guard story).
  - Tests:
    - enqueue twice = one run
    - every audit row carries `job_run_id`
    - person A never sees a suggestion shaped by B's private rows
    - a hidden competitor still gives A a `likely` suggestion
- **C5b:**
  - `ProcessPoolExecutor(max_workers=1, mp_context=get_context("forkserver"))`, preloading only
    `app.domain.recurring_series` (Bulwark 5).
  - On timeout: terminate the worker process (on CPython 3.12 through the executor's process table,
    wrapped in one helper with its own test), `shutdown(wait=False, cancel_futures=True)`, and
    create a new pool **before** failing the attempt.
  - The child receives only `RowTuple`s (ids, account ids, dates, amounts, merchant keys).
  - Child exceptions are logged by class name only.
  - The pool lives in `RunnerDeps` and reaches the handler through `JobContext`, created at worker
    startup and closed at shutdown.
  - `recurring_series` UPDATE has both USING and WITH CHECK (Bulwark 13).
  - Step 3 locks and re-plans (4.2, Gadfly B5). Tests:
    - confirm and dismiss survive a rerun
    - a confirm made while detection runs survives, using a child blocked on an event, with no
      timing involved
    - supersede every member of a confirmed series, then refresh: same id, still confirmed
    - a timeout terminates the child and the next run works
    - the event loop serves another coroutine while the child is blocked

Timing budgets (Gadfly S7): the 2 s preview at 50k rows, the 30 s job at 50k rows and two people,
and the 7 s detection are `@pytest.mark.bench` tests. They're excluded from the required run,
recorded by Sounding in C7, and wired into a non-required CI job by a Harbor follow-up. Required
tests assert bounded work instead. **The ADR 0024 regex gate stays required on CI** (Bulwark 1): it
has better than 6× headroom (section 11).

### 7.4 C7 synthetic evidence (first-pass B4)
- **Generator:** `backend/tests/eval/generator.py` (C7, Sounding). It builds one household with two
  people and these accounts:
  - joint checking (joint)
  - A's card (private)
  - B's card (shared)
  - A's savings (private)
  - B's checking (private)

  It covers 24 months of statement-style rows (no Plaid PFC) and plants:
  - at least 30 bills across all six cadences, including annual and variable utilities
  - payroll for both people
  - card payments from joint checking to each card (cross-owner: these must be links)
  - A's checking to A's savings (same owner: pairs)
  - a hinted move from B's private checking to A's private savings (private to private: a link)
  - unhinted transfers (suggestions)
  - card purchases, refunds and ad-hoc purchases from a synthetic merchant vocabulary

  The truth per row: category slug, transfer edge, bill series.
- **Seeds:**
  - Tuning seeds 0–9 are in the repo.
  - The holdout is five seeds that Sounding draws with `secrets.randbits(32)` at acceptance and
    records in `PHASE-4.md` after the run. Builders never see them first.
- **Labelling simulation:** `backend/tests/eval/simulate.py`.
  1. Import months 1–6.
  2. Visit uncategorised eligible rows in posted-date order. Set each one's true category (one
     label), and accept the "make a rule" offer: prefilled `merchant_key` and `amount_sign`, the
     default scope from section 6, item 2.
  3. Save with apply only when the preview's sample has at most one row whose truth differs from
     the label. Otherwise save for new rows only.
  4. Stop at 200 labels.
  5. Import months 7–24 through the statement path (rules at ingest), then run one refresh.
- **Measures:** exactly section 0 criteria 1–4. The run also reports:
  - share of Plaid-sourced rows (0)
  - links, pairs and suggestions per person
  - false series per tier
  - the PR #52 and PR #58 detector limits by category
- **Follow-up trigger:** if bill-tier recovery is under 80% or false bill-tier series are over 5%,
  Keel schedules a detector follow-up.

### 7.5 Real-data run (Keel decision; Gadfly B4)
Holdfast agents (Sounding, with Ledger for the loader) run it locally under ADR 0019, on the owner's
machine, and report counts only. The owner's instance is on `main` at `0009`, and a dump was taken
first, so nothing waits on the owner.

1. **Before each Phase 4 migration reaches the instance:**
   - Take a fresh dump to the private directory.
   - Restore it into a throwaway local stack: its own compose project, non-dev ports, and a data
     directory inside the private directory.
   - Run that migration up, down and up there. Only then does Keel upgrade the instance.
2. **Measure on the restored copy, never the live instance,** so agent labels never become the
   owner's choices. The live instance only gets Phase 4's code and migrations.
3. **Ground truth stays in the private directory** (never the repo, a PR, an issue or a log). From
   the owner's statements, the agent builds:
   - a hand list of recurring charges and income
   - a hand list of card payments, with both legs where both accounts are imported
   - 200 randomly drawn rows labelled for precision, kept apart from the 200 simulation labels
4. **Run the section 7.4 simulation on the copy**, with the agent as the labeller, then one
   refresh.
5. **Report in `PHASE-4.md`**, counts and rates only:
   - eligible rows, coverage and precision
   - pairs, links, and suggestions open per person
   - card payments with both legs present, how many were excluded with no click, and how many were
     excluded or suggested
   - series per tier
   - hand-list bill recovery (either tier, and bill tier)
   - false series per tier
   - migration rehearsal results

   The thresholds are criteria 1–4, R variant. A miss blocks the phase tag like a synthetic miss.
6. **Before anything is pushed,** the agent runs the private `scan_rev.py` check over the branch.
   `PHASE-4.md` never holds a name, merchant, account digit or amount from the owner's data.

### 7.6 File ownership per run
| Slice | Owns (creates or edits) |
|---|---|
| C3b | `backend/migrations/versions/<n>_rules.py`, `backend/app/models/transactions.py`, `backend/app/models/__init__.py`, `backend/app/services/ledger_service.py`, `backend/tests/models/test_rls_rules.py`, `backend/tests/models/test_migration_rules.py`, `backend/tests/ledger/test_ledger_service.py`, `backend/tests/test_ledger_single_writer.py` |
| A1 | `backend/app/api/routes_accounts.py` (new), `backend/app/schemas/accounts.py` (new), `backend/app/services/accounts.py` (new), `backend/app/schemas/ledger.py` (`AccountOut` fields), `backend/app/services/ledger.py` (account fields), `backend/app/schemas/imports.py`, `backend/app/services/imports.py` (block owner fields), `backend/app/services/ledger_service.py` (`dissolve_account_pairs`), `backend/tests/api/test_account_ownership.py`, `backend/tests/ledger/test_ledger_service.py`, `web/src/api/schema.d.ts` (generated) |
| A1w | `web/src/pages/transactions/AccountsPanel.tsx`, `web/src/pages/transactions/AccountOwnerSheet.tsx` (new), `web/src/pages/import/ResultView.tsx`, `web/src/api/ledger.ts`, `web/src/api/imports.ts`, `web/src/mocks/ledger.ts`, `web/src/mocks/imports.ts`, `web/e2e/accounts.e2e.ts` |
| C3c | `backend/app/domain/{merchant,rule_regex,rules}.py`, `backend/tests/domain/{test_merchant,test_rule_regex,test_rules,test_regex_imports}.py` |
| C4b | `backend/app/domain/transfers.py`, `backend/tests/domain/test_transfers.py` |
| C5c | `backend/app/domain/recurring_series.py`, `backend/tests/domain/test_recurring_series.py` |
| C6a | `backend/app/api/routes_ledger.py`, `backend/app/schemas/ledger.py`, `backend/app/services/ledger.py`, `backend/tests/api/test_ledger*.py`, `web/src/api/schema.d.ts` |
| W1 | `web/src/pages/transactions/**` (except A1w's files), `web/src/mocks/ledger.ts`, `web/src/api/ledger.ts`, `web/e2e/activity.e2e.ts` |
| C3d | `backend/app/domain/rule_regex.py`, `backend/pyproject.toml`, `backend/uv.lock`, `backend/tests/domain/test_rule_regex.py`, `backend/tests/domain/regex_corpus.py` (new), `docs/user/rules.md` |
| C3e | `backend/app/services/rules.py`, `backend/app/api/routes_rules.py`, `backend/app/schemas/rules.py`, `backend/app/services/statement_import.py`, `backend/app/services/errors.py`, `backend/app/schemas/ledger.py` + `backend/app/services/ledger.py` (`category_rule_id`), `web/src/api/contracts.ts` (error code only), `web/src/api/schema.d.ts`, `backend/tests/api/test_rules*.py`, `backend/tests/services/test_rules*.py`, `backend/tests/bench/test_rules_bench.py` |
| W2 | `web/src/pages/settings/Rules*.tsx`, `web/src/features/rules/**` (new), `web/src/mocks/rules.ts`, `web/src/mocks/handlers.ts`, `web/src/api/rules.ts`, `web/src/app/routes.tsx`, `web/e2e/rules.e2e.ts` |
| C4c | `backend/migrations/versions/<n>_transfer_corrections.py`, `backend/app/models/transfers.py` (new), `backend/app/models/__init__.py`, `backend/app/services/{ledger_service,transfer_pairing}.py`, `backend/tests/models/test_rls_transfers.py`, `backend/tests/models/test_transfer_triggers.py`, `backend/tests/ledger/test_ledger_service.py`, `backend/tests/ledger/test_transfer_pairing.py`, `backend/tests/test_ledger_single_writer.py` |
| C4d | `backend/app/jobs/handlers/ledger_refresh.py`, `backend/app/services/transfer_suggestions.py` (new), `backend/app/services/refresh.py` (new: `request_refresh`), `backend/app/services/jobs.py` + `backend/app/jobs/queue.py` (`defer_until`), `backend/app/api/deps.py`, `backend/app/services/imports.py` + `backend/app/services/accounts.py` (enqueue), `backend/app/api/routes_ledger_status.py`, `backend/app/schemas/ledger_status.py`, `backend/app/worker/main.py` (comment), `backend/tests/ledger/test_transfer_pairing_guard.py`, `backend/tests/models/test_household_wide_guard.py` (allowlist only), `backend/tests/jobs/test_ledger_refresh*.py` |
| C4e | `backend/app/services/transfers.py` (new, request side), `backend/app/api/routes_transfers.py`, `backend/app/schemas/transfers.py`, `backend/app/api/routes_ledger.py` (transfer endpoints), `backend/app/schemas/ledger.py` + `backend/app/services/ledger.py` (transfer fields), `web/src/api/schema.d.ts`, `backend/tests/api/test_transfers*.py` |
| W3 | `web/src/pages/transactions/Transfers*.tsx`, `web/src/mocks/ledger.ts`, `web/src/api/transfers.ts`, `web/e2e/transfers.e2e.ts` |
| C5b | `backend/migrations/versions/<n>_recurring_series.py`, `backend/app/models/recurring.py` (new), `backend/app/models/__init__.py`, `backend/app/services/recurring.py` (store side), `backend/app/services/recurring_pool.py` (new), `backend/app/jobs/handlers/ledger_refresh.py` (step 3), `backend/app/jobs/runner.py` + `backend/app/jobs/registry.py` (`cpu_pool`), `backend/app/worker/main.py` (pool lifecycle), `backend/tests/models/test_rls_recurring.py`, `backend/tests/jobs/test_ledger_refresh_recurring.py`, `backend/tests/bench/test_recurring_bench.py` |
| C5d | `backend/app/api/routes_recurring.py`, `backend/app/schemas/recurring.py`, `backend/app/services/recurring.py` (read side), `backend/app/api/routes_ledger_status.py` (`recurring_count`), `web/src/api/schema.d.ts`, `backend/tests/api/test_recurring*.py` |
| W4 | `web/src/pages/transactions/Recurring*.tsx`, `web/src/mocks/recurring.ts`, `web/src/mocks/handlers.ts`, `web/src/api/recurring.ts`, `web/e2e/recurring.e2e.ts` |
| C7 | `backend/tests/eval/**` (new), `docs/phases/PHASE-4.md` |

Shared files: `backend/app/models/__init__.py`, `web/src/api/schema.d.ts`, `web/src/app/routes.tsx`
and `web/src/mocks/handlers.ts`. Add only what the slice needs and say so in the PR. Resolve
generated `schema.d.ts` conflicts by rerunning `make api-types`, never by hand.

---

## 8. Reviewer findings folded in

| Source | Finding / precondition | Answer |
|---|---|---|
| PR #53 Bulwark | No user regex saved or run until a linear-time engine or killable subprocess is chosen | ADR 0024: RE2. `compile_pattern` refuses everything until C3d. No `re` fallback |
| PR #53 Bulwark | A rule's category and accounts must belong to the author's household and visible accounts | Composite FK on `rule.category_id`. No account lists. `rule_may_touch` by scope |
| PR #53 Bulwark | Preview and apply in the author's person-scoped session | Section 4; ADR 0029 point 2 |
| PR #53 Bulwark | Write only through `LedgerWriter` | `apply_rule_bulk`, `apply_category(rule_id)`; the single-writer lint covers `category_rule_id` and `superseded_by_id` |
| PR #53 Bulwark | The timing test includes the two slow patterns | C3d corpus, plus the three DFA blow-up shapes (ADR 0024 point 7) |
| `rules.py` docstring | Split parents, transfer legs, where `transfer_candidate` lives | D4, D5 |
| PR #50 Ledger/Bulwark/Keel | A pair-level "not a transfer" record, honoured | `transfer_rejection` (3.3); `match_transfers(rejected_pairs=...)` (2.4); locked re-check (4.2) |
| PR #50 | `job_run_id` required whenever `user_id` is None | 4.5 provenance; ADR 0027 point 6 |
| PR #50 | Store suggested/ambiguous per person with RLS; never a household-wide report over an API | Per-person passes; `transfer_suggestion.person_id` + both-legs policy (ADR 0026 point 4) |
| PR #50 | Either owner can dissolve a pair touching their leg | Same-owner groups only; cross-owner moves are links of single legs (ADR 0027) |
| PR #50 follow-up | Tighten the household-wide grep guard to any use of the setting name outside `db_context` and migrations | G1 (Keel). The allowed files after Phase 4: `app/db_context.py`, `backend/migrations/**`, `app/jobs/handlers/ledger_refresh.py` |
| `transfer_pairing.py` | NOT SAFE TO SCHEDULE until rejection exists | C4d is ordered after C4c; the guard test is narrowed to one handler, not removed |
| PR #52 Ledger | Consumers must require confidence above 0.60 (annual pairs) | `tier = possible` at ≤ 0.60 on the stored value (ADR 0028 point 4) |
| PR #52 | A regular weekly shop returned as a variable weekly series | Fast-cadence variable series are `possible` unless confirmed |
| PR #52 | Typical amount can be a value never charged | The API returns the band; the UI says "about" for variable series |
| PR #52 | Resumed schedules form a duplicate lapsed series | `reconcile_series` merge rule |
| PR #52 | CPU-heavy, off the event loop | `forkserver` pool with a killing timeout (ADR 0029 point 3.3) |
| PR #52 | Bills hidden when ad-hoc in-range purchases share the merchant; variable weekly pay with a missed cycle dropped | Detector limits, not wiring. C7 reports them by category, and a follow-up comes per 7.4's trigger |
| PR #58 | An annual pair's typical amount should be the newer charge; annual pairs that change by about 20% are missed; 11 ad-hoc purchases admitted as members | Detector limits. C7 reports each one (count of affected truth bills), and they fall under the same 7.4 follow-up trigger. Wiring doesn't depend on them |
| PR #55 Keel | Freeze `review_reason` in `import_run_guard` (own migration) | I1d, outside this plan (Tide, own token) |
| ADR 0016 | Wire `arq_pool` into the API when the first API-triggered job lands | C4d |
| Bulwark on PR #59 | Items 1–14 | Section 7.3 per slice; section 11 |

---

## 9. Owner decisions (each has a default, so nothing is blocked)

1. **Rules are visible to everyone in the household** unless limited to your own accounts. Rules
   you make from your own accounts' rows start limited. Default: yes (ADR 0026).
2. **A move between two people's accounts** is recorded as two linked single transfers, marked
   automatically when the wording says so, and each person can undo their side. Default: yes (ADR
   0027).
3. **Only the household owner can take a joint account for one person.** Default: yes (D14).

The real-data checks are no longer an owner item. Holdfast agents run them locally (section 7.5).

---

## 10. Out of Phase 4 (recorded, not built)
- the ML classifier and nightly retrain (cut from Q1)
- rule actions for tax tag, rename and transfer marking
- per-rule account lists, and a per-account opt-out from another person's rule
- undoing a retroactive apply (every value an apply overwrites is re-derivable: user categories are
  never overwritten, Plaid categories come from the stored PFC, and rule categories come from rules)
- splits UI and API (the C6 remainder, Phase 5 if needed)
- agent-authored rules (Phase 8: created disabled, a person enables)
- the `any_of` literal-list mode (ADR 0024 exit)
- a household time-zone setting for `as_of`
- recurring series across accounts (a reissued card splits a series)
- an account delete endpoint (when one is built, it must unmark the other side of each of the
  account's links first, because the link row cascades away with the account)
- the `v_recurring` view for the agent (Phase 8)
- recurring price-increase alerts (Phase 5, B4)

Phase 4 doesn't touch secrets handling, `household_setting`, ADR 0015, or anything in the Plaid
lane (its key entry and slot-tombstone migration are separate stories). I4 must call
`LedgerWriter.supersede_transaction` (4.4). Phase 4's ADRs are 0024 and 0026–0029. 0023 and 0025
belong to the Plaid lane.

---

## 11. Review responses

### 11.1 Gadfly, first pass (`reviews/PHASE-4-gadfly.md`)
| Item | Response | Where |
|---|---|---|
| B1 cross-owner moves fail criterion 2 and double-count | **Accepted.** Cross-owner edges that meet the auto bar are linked automatically: both legs marked, plus a `transfer_link`. Joint versus owned counts as different owners. A hidden link's unmark becomes a `pair` rejection at step 0, so no `leg` rejection is written by accident. A card is hidden only when every leg the reader can write is a transfer. The leak is stated and accepted. The C7 holdout includes joint checking paying a personal card, and a private-to-private move | 2.4, 3.3, 4.1, 4.4, 5.3, ADR 0026, ADR 0027, 7.4 |
| B2 supersession drops people's corrections | **Accepted.** `supersede_transaction` moves the user category, transfer state and splits, and a trigger re-points rejections, links and members in the same unit of work. Test: unmark, then supersede, then refresh, and nothing is re-paired | 3.3, 3.4, 4.4, 7.3 C4c |
| B3 the regex gate is in the API only | **Accepted.** `compile_pattern` refuses every input until C3d, so the codec, loader and API all refuse. The `ast` import test. The 422 covers preview, create and PATCH. "Skipped and flagged" is `RuleOut.status` | 2.2, 5.2, ADR 0024 point 2 |
| B4 C7 has no evidence plan | **Accepted.** Generator module, seed custody, labelling policy, denominators, precision as slug equality, Plaid share 0, owners in the holdout, bill-tier recovery, false-series caps | 0, 7.4 |
| S1 rule visibility leaks and naming | **Accepted in part.** A rule from a private row defaults to `own`. `any` reads "Joint and my accounts". Account lists are removed, so "future accounts" is no longer an issue. **Rejected:** a per-account opt-out from another person's rule. That person can set the row (a user choice wins) or make a higher-priority rule. Recorded in section 10 | 2.3, 3.1, 6 (item 2), 10 |
| S2 slice order and sizes | **Accepted.** C6a depends on C3c, not C3b. "Needs category" is stated plainly. C4c is split into C4c/C4e and C5b into C5b/C5d | 6 (item 1), 7.1, 7.2 |
| S3 missing preconditions | **Accepted.** The PR #58 row is added. The guard's allowed files are named | 8 |
| S4 `merchant_key` unversioned for rules | **Accepted.** `merchant_key_version` is in the conditions JSON, and a mismatch is `needs_review`. Whether to ship a rewrite migration is decided in the bump PR | 2.3, 5.2 |
| S5 retroactive apply keeps no history | **Rejected.** Every value an apply overwrites is re-derivable (section 10), and the audit row names the rule. An apply record is a new RLS table for an undo that Phase 4 doesn't ship | 10 |
| S6 contradictions with the spec | **Decided.** Savings: ADR 0022 rule 1 applies to income and spending, and rule 3 reads `savings`-kind outflows whatever their `is_transfer`. Pairing never changes categories, so a paired move into savings still counts as saving (ADR 0027 Consequences). Rules vs transfers: the deviation from PLAN 8.2 is recorded (ADR 0029). Grouping by account: kept and recorded (ADR 0028 Consequences, section 10) | ADRs 0027–0029 |
| S7 builder guesses | **All decided.** PATCH keeps `category_rule_id`. Same category but a new source counts as a change. A disabled rule can't be applied (409). Refresh is coalesced (4.3). "Check again" uses a 1-minute bucket. Caps: 200 per author including disabled, 25 enabled patterns per household | 2.3, 4.3, 4.4, 5.2 |
| RE2 exit | **Accepted.** Harbor owns the watch. Triggers: a Python bump with no wheel, a `pip-audit` finding with no fix, or 18 months with no release. The exit is regex rules showing `unavailable`, plus an `any_of` literal-list mode | ADR 0024 Consequences |

### 11.2 Gadfly, second pass (`reviews/PHASE-4-contract-gadfly.md`)
| Item | Response | Where |
|---|---|---|
| B1 no ownership producer; owner changes break the invariant | **Accepted (Keel decision).** A1 is added before every transfer slice. `PATCH /accounts/{id}` dissolves pairs in the same unit of work, and the import result offers the change. `account_guard_transfer_owner` is the DB backstop. Phase 4 isn't declared single-owner | 5.0, 6 (item 0), 3.3, 7.1 |
| B2 the per-person pass drops confident pairs | **Accepted.** `pairs` and `links` in a person's view become `likely` suggestions. Step 2 never writes pairs | 2.4, 3.3, 4.1, 7.3 C4b |
| B3 deleting or superseding a paired leg corrupts the partner and series | **Accepted.** `transaction_dissolve_orphan`, `unmark_transfer` with one survivor, the re-point trigger for members, live-only member listings. Both tests named | 3.3, 3.4, 4.4, 7.3 |
| B4 acceptance can pass without real data | **Accepted (Keel decision).** Agents run the real-data check locally on a restore of the owner's instance, with counts only. The upgrade dependency is dropped (the instance is at `0009` with a dump). Criterion 4 is rewritten so it can fail. One e2e per screen against the real API | 0, 7.2, 7.5 |
| B5 the job overwrites choices made while it runs | **Accepted.** `FOR UPDATE` and a re-plan after detection, guarded deletes, upserts that never write user fields, and the rejection re-check under the leg lock | 4.2, ADR 0028, ADR 0029 |
| S1 series identity holes | **Accepted.** A label implies `confirmed`. The tie rule prefers user-stated series, and the takeover rule moves state to the detection | 2.5, ADR 0028 points 1–2 |
| S2 the process pool can't kill a task | **Accepted, in Bulwark 5's form:** a `forkserver` `ProcessPoolExecutor`, the worker terminated and the pool recreated on timeout. The spec timeout is explicit (300 s). The path is `app/worker/main.py` | 4.3, 7.3 C5b |
| S3 refresh fan-out and staleness | **Accepted (Keel decision).** 2-minute buckets deferred to the bucket's end, with no tail check needed (4.3 proves it). Transfer edits and ownership changes enqueue the same key | 4.3 |
| S4 migration path too long | **Accepted in part.** M2 leaves the plan. **Rejected:** merging M3 and M4, because one migration per slice keeps each RLS review whole and keeps Bulwark's items attached to their slices. **Rejected:** recurring before transfers (7.1, with the reason) | 3, 7.1 |
| S5 cut account scope | **Accepted in part.** `rule_account`, its triggers and its 422s are cut. **Rejected:** cutting `own`. Bulwark 3 needs it so a rule made from a private row stays private | 3.1, 2.3 |
| S6 types don't match the code | **Accepted.** `DetectedSeries` / `detected_from`, `UUID` ids, rows without a `merchant_key` never sent, `stored_confidence` and the tier from the stored value, the named `load_candidates` loader owned by C4c, the connection comment, and `handlers.ts` ownership | 2.5, 4.1, 7.6 |
| S7 wall-clock limits flaky in CI | **Accepted, except the ADR 0024 gate,** which Bulwark requires on CI. It keeps better than 6× headroom: accepted corpus patterns took 0.04–0.15 s per 50,000 searches locally against a 1 s gate | 7.3, ADR 0024 |

### 11.3 Bulwark (review of PR #59 at `0fab87f`)
| Item | Response | Where |
|---|---|---|
| 1 RE2 time budget | **Accepted and re-measured** (ADR 0024 Context). Point 7 now requires each corpus pattern, the three blow-up shapes included, to be rejected by `compile_pattern` or to run 50,000 searches of 1,024-character subjects in under 1 s on CI. The three shapes take 12–28 s, so only rejection passes them: that's the point of 4b. Point 4b's cost probe is at a 10 µs mean, and **adds class-derived probe subjects** to the fixed set, because a fixed set alone accepted `[XY]*X[XYZ]{20}W` at 1.1 µs while it costs 335 µs per search on its own alphabet. The cap is 25. `MAX_SUBJECT_LENGTH = 256` is adopted. Ingest runs in `anyio.to_thread` | 2.2, 4, ADR 0024 |
| 2 no live `re` path in C3c | **Accepted as written** | 2.2, ADR 0024 point 2 |
| 3 `own` default and stated visibility | **Accepted.** The ADR 0026 Consequences state that `any`/`joint` rules, name and conditions included, are household-readable | 6 (item 2), ADR 0026 |
| 4 helper and trigger function form | **Accepted.** The SQL is written out in 3.1 | 3.0, 3.1 |
| 5 the process pool child | **Accepted** | 7.3 C5b |
| 6 `account_guard_transfer_owner`; ADR 0027 line 19 | **Accepted.** A1 dissolves first. The trigger checks live and superseded rows, which is stricter. ADR 0027 point 1 is corrected and the leak accepted | 3.3, 5.0, ADR 0027 |
| 7 provenance wording | **Accepted**, for `apply_transfer_link` too | 4.5 |
| 8 step 2 never writes pairs; leftover inference | **Accepted** | 2.4, ADR 0026 point 4 |
| 9 derived-row FKs include `household_id` | **Accepted**, for every leg column of every derived table | 3.0, 3.3, 3.4 |
| 10 supersede re-points rejections | **Accepted.** It's a trigger, so it works even for rejections the superseding session can't see. It follows the existing 0005 supersession-guard pattern | 3.3 |
| 11 M1 downgrade; `rule_scope_guard` on author | **Accepted** | 3.1 |
| 12 `repr=False`; audit details; `rule.applied {count}` | **Accepted.** Decision: no count for any scope, because `any` rules also touch the author's private rows | 2.3, 4.5 |
| 13 `recurring_series` UPDATE policy | **Accepted** | 3.4 |
| 14 manual key format | **Accepted:** `refresh:manual:20261121T1005Z` and `refresh:auto:20261121T1004Z` | 4.3 |
