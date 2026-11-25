# Phase 4: wiring contract (rules, transfers, recurring)

Spec: PLAN 8.2–8.4, 11, 13 Phase 4 (Section 20 wins), ROADMAP E4 (C3–C7), ADRs 0008, 0011, 0016,
0017, 0019, 0022, and the Phase 4 ADRs 0024 and 0026–0029. This file is binding for every Phase 4
story. A builder who must deviate says so in the PR body and asks Lattice for an amendment. The
amendment lands in this file before the code merges.

Revision 2 (2026-11-21) answers Gadfly's two critiques (`docs/phases/reviews/PHASE-4-gadfly.md`,
`PHASE-4-contract-gadfly.md`) and Bulwark's review of PR #59. Revision 3 (the same day) answers
Bulwark's second review and Gadfly's round 2 at `f651389`. Revision 4 answers Bulwark's third
review at `cc7949d` and aligns with the Phase 2 contract's round 3: one supersede verb pair
(`PHASE-2-contract.md` §3.3) and the shared migration and file order (PHASE-2 §4). Revision 5
answers Gadfly round 3 at `fcb3a4e`. Revision 6 answers Gadfly round 4 at `038a875` and carries
Bulwark's round-5 slice notes. Section 11
lists every item and where it was closed.

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
     can see, is excluded or is offered to at least one person who can write a leg. Once that
     person accepts it, every other person who writes a leg still counted is offered that leg (a
     leg offer, 5.3). The run also reports the planted transfers no single person can see both legs
     of: unhinted moves between two private accounts, the stated residual in ADR 0027.
   - (c) No planted purchase is excluded.
   - (d) After the scripted review (7.4 step 6), the spend total over the holdout equals the
     truth's spend total plus the planted residual moves from (b), exactly (`Decimal` equality).
     The residual is planted in a fixed, reported amount, so a missed or a wrong exclusion anywhere
     else breaks the equality. The same total is also reported after step 5, before any click.
   - For R, "planted" means the agent's hand list of card payments (section 7.5). (a) becomes ≥ 90%
     with no click, (b) stays at 100%, and (d) is S only (R has no exact truth).
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
| D8 | "Not a transfer" | `transfer_rejection` (`pair` and `leg`). Unmarking a pair, or a single leg with a visible link, writes a `pair` rejection. Declining a leg offer becomes one at the next refresh. Only "Never a transfer" writes a `leg` rejection | ADR 0027 |
| D9 | Suggestions and leg offers | Computed per person in that person's session, stored with `person_id`. Step 2 never writes pairs: a confident edge in a person's view becomes a `likely` suggestion. The second person's half of an accepted move is a leg offer that names only their own leg, created household-wide at step 0 | ADR 0026 |
| D10 | Recurring identity | Stable ids, reconciled by member overlap under a row lock. `confirmed` and `dismissed` stick. A label implies `confirmed`. Status is computed at read time. Two tiers, `bill` and `possible` | ADR 0028 |
| D11 | Jobs | One `ledger.refresh` job, coalesced per household into 2-minute buckets: link cleanup and auto-pair, then per-person suggestions, then recurring in a `forkserver` process pool | ADR 0029 |
| D12 | Step-up re-auth | None of the Phase 4 endpoints (section 5.6) | here |
| D13 | `merchant_key` | Pure, versioned `app/domain/merchant.py`, computed when read, not stored. Rules store the version they were written against | here |
| D14 | Account ownership | `PATCH /accounts/{id}` (A1) makes an account joint, lets the household owner claim a joint account, and changes visibility. Handing an account to another person isn't in Phase 4 (ADR 0008: reassignment is household-wide). A1 refuses an owner change while the account has pairs. From C4c it dissolves them in the same unit of work, and a DB trigger refuses an owner change while pairs remain. The joint-claim role check lives in the service only | ADR 0027 |
| D15 | Deleted and superseded legs | Superseding (PHASE-2 §3.3) moves category by rank, splits, transfer state, tax, receipt and notes in `LedgerWriter`. The trigger re-points rejections, links and recurring members in both directions. A deleted pair leg's partner is unpaired, whoever made the pair | ADR 0027, 0028 |

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
    def search(self, text: str, clock: RegexClock | None = None,
               *, rule_id: UUID | None = None) -> bool: ...
MAX_PATTERN_LENGTH = 128
MAX_SUBJECT_LENGTH = 256          # ADR 0024 point 5 (was 1,024; measured, see the ADR)
PROBE_LIMIT_MICROSECONDS = 10     # ADR 0024 point 4b, per probe family
MAX_CLASS_ATOMS = 16              # ADR 0024 point 4b
REGEX_BUDGET_SECONDS = 10         # ADR 0024 point 9: preview, apply, clear
INGEST_REGEX_US_PER_ROW = 500     # int microseconds, no float. ADR 0024 point 9: ingest, checked every 1,000 rows
MAX_PROBE_FAMILIES = 160          # save-time work cap. Hitting it rejects, never accepts
MAX_REPEAT_BUDGET = 8             # TOTAL counted repetition: sum along a sequence, max across `|`, nesting multiplies, `{n,}` counts n. Over it: PATTERN_TOO_COMPLEX
MAX_CLASS_POSITIONS = 24          # class/dot/branching-group positions, written-out copies included
MAX_PROGRAM_SIZE = 2000           # RE2 compiled program size (`programsize`) ceiling
def compile_pattern(pattern: str) -> CompiledPattern   # save path: RE2 + cost probe; raises RuleValidationError
def compile_stored_pattern(pattern: str) -> CompiledPattern  # loader path: RE2 only, no probe
```
- Class positions: every `.`, class or branching group in sequence counts 1, times its `{n}`, over
  the same calculator. `MAX_CLASS_POSITIONS = 24`, refused with PATTERN_TOO_COMPLEX. Literals are free,
  and a count on a single literal or a pure-literal group costs nothing against either budget
  (programsize bounds it). Pure-literal patterns skip the
  timing probe. The code's rule: no `.`, no class or class escape (`\d`, `\pL`), no `|`, no `*`, `+`
  or `?`, and no `{n}` (plain `(?:...)` groups, anchors and escaped literals are allowed). Inline
  flags are refused (PATTERN_UNSUPPORTED): only `(?:` and `(?P<name>` open a group, so there is one
  case mode. Subjects and patterns go through NFKC (`rules.normalize_for_rules`, rule matching
  only: fingerprints and merchant keys keep `normalize_description`). The probe adds literal-prefix + dense-trigger families.
- Repetition budget: `[W]{8}[W]{8}` is 16 and refused, `\d{3}-\d{4}` is 7 and valid. The probe fails
  closed: escape-only classes are sampled from their decoded range ends and midpoint, and a class
  with no sample refuses the pattern.
- Structural limits run before the probe (ADR 0024 follow-up). `MAX_PROGRAM_SIZE = 2000` is
  13x the benign corpus peak (153), leaves `\p{L}+` (1,202) valid, and a 16-fold
  `\p{Greek}|\p{Cyrillic}` group (1,617) is under it, so the repetition cap is the primary control
  and the probe refuses that one.
  The probe remains for patterns inside both limits.
- `stored` is not a constructor argument. The loader reaches `compile_stored_pattern` only through
  `DescriptionCondition._from_store`, via `conditions_from_json(obj, stored=True)`. A test fails if
  `app/api/` or `app/services/` mention either.
- `search(text, clock, rule_id)` times the call into `RegexClock` (integer nanoseconds). Over budget
  it raises `RegexBudgetExceeded`, and with no `rule_id` it still raises (fail closed).
- `compile_stored_pattern` is only for rows that already passed the probe: the loader
  (`conditions_from_json(obj, stored=True)`, which calls `DescriptionCondition._from_store`) uses it so that loading is deterministic (ADR 0024
  point 4b). Create, edit and preview paths use `compile_pattern`. A test fails if any other module
  references it.
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
- **Run-time budget** (ADR 0024 point 9, Bulwark round 3). The probe can't be the only bound.
  - `CompiledPattern.search` adds its elapsed time to a caller-supplied `RegexClock`.
  - The pure matcher raises `RegexBudgetExceeded(rule_id)` past a budget. It names the rule with the
    most regex time.
  - Preview, apply and clear stop at `REGEX_BUDGET_SECONDS` and return 503 `rules_too_slow`, with
    nothing written.
  - Ingest checks every 1,000 rows. Above `INGEST_REGEX_US_PER_ROW`, it skips `regex` rules for the
    rest of that import.
  - Timing decides only whether a run stops, never what a completed run matches. Apply and clear
    recompute and compare the token, so a stopped run can never write a different count.

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
- **C3c removes `RuleConditions.account_ids`** (Bulwark): no Phase 4 path may filter by it, and a
  field nobody can set is a field somebody will set by accident. Per-rule account lists, if they
  ever come, return with their own table (section 10). `RuleAction.mark_transfer_candidate` stays
  in the dataclass, but the codec and the API never produce it (D4).
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
    links: tuple[ProposedPair, ...]       # different owners (joint vs owned counts), a hint on each leg
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
  - An edge that meets the auto evidence bar goes to `pairs` when the owners are equal.
  - **For `links` (different owners), each leg must carry a hint in its own description: transfer
    or payment wording, or the other account's mask. Evidence on one leg never marks another
    person's leg** (Bulwark). In the merged code's terms, a link needs `n_out > 0 and n_in > 0`. The
    one-sided mask exception that lets a pair through doesn't apply. A cross-owner edge that meets
    the pair bar but not this one is a `one_sided_hint` suggestion.
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
    newest_member_id: UUID | None     # the live member with the latest posted date (R2-S2)
    all_members_transfers: bool       # every live member is is_transfer (Gadfly round 2)
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
- `reconcile_series` follows ADR 0028 points 1, 2, 5 and 6. That includes:
  - the matching tie rule and the takeover rule (S1)
  - **the newest-member rule** (Gadfly R2-S2): when a stored series' members split across
    detections, its id and state follow the detection that holds `newest_member_id`, not the one
    with the largest overlap. So a bill whose recent rows were superseded by Plaid rows under a new
    merchant key keeps its confirmation on the live schedule.
  - **the all-transfers rule:** a stored series with `all_members_transfers` is deleted, whatever
    its state, because its rows turned out to be transfers.

  Property tests check the invariants:
  - member sets are pairwise disjoint
  - every detected series appears in exactly one upsert
  - no stored `confirmed`, `dismissed` or labelled id is ever in `deletes`, unless
    `all_members_transfers`
  - the result doesn't depend on input order

  A table test: supersede the newest 12 of 30 members under a new key. The confirmed id follows
  the 12.
- `series_status`:
  - tolerance: 2 days (weekly, biweekly), 3 days (others)
  - `active` while `as_of <= next + tol`
  - `possibly_cancelled` once `as_of > next + NOMINAL_DAYS // 2` (and past `tol`)
  - `late` in between
- `series_tier` follows ADR 0028 point 4: threshold `Decimal("0.60")`, strictly greater for `bill`.

---

## 3. Schema: one ordered migration plan

One migration per slice. The migration token order is shared with the Phase 2 contract
(`PHASE-2-contract.md` §4) and fixed:

**`0010_list_household_ids` (#31, merged) → `<n>_connector_link` (P4a, Phase 2) → M1 `<n>_rules`
(C3b) → M2 `<n>_import_run_reason_final` (I1d, Tide) → M3 `<n>_transfer_corrections` (C4c) → M4
`<n>_recurring_series` (C5b).**

- A builder may write a migration before holding the token, but numbers it and rebases only once
  they hold it.
- **Skip rule:** if the next holder has no open PR **with both verdicts** one window after the
  token frees, the following slice may take it once. Keel notes the skip in SPRINTS.
- Other shared files have fixed orders too:
  - `backend/app/services/ledger_service.py`: P2a → C3b → C4c → C5b. P2a depends only on P1's
    types, so it can merge early.
  - `backend/app/services/statement_import.py`: P4a (resolver) → P2b (matcher call) → C3e (inline
    rules)
  - inline rules in `IngestService` (`services/ingest.py`): P2b, then C3e

This plan fixes an order, not numbers:
- The builder who holds the token numbers the migration at build time from the Alembic head on
  `main` (next free number, with `down_revision` set to that head), after `git merge --ff-only
  origin/main`.
- Numbers other lanes claimed are never reused, and those lanes' migrations may land in between.
- The revision slug is fixed. The numeric prefix isn't.

| Order | Revision slug | Slice | Needs |
|---|---|---|---|
| M1 | `<n>_rules` | C3b | P4a's migration (token order); P2a merged (shared `ledger_service.py`) |
| M2 | `<n>_import_run_reason_final` | I1d (Tide) | M1 (token order) |
| M3 | `<n>_transfer_corrections` | C4c | M1 (helpers), M2 (token order) |
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
  - Where a trigger must see rows the caller can't (the counts in 3.1 and 3.3, and the supersede re-point), it follows the
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

### 3.2 M2 `<n>_import_run_reason_final` (I1d, Tide; PR #55 follow-up)
`CREATE OR REPLACE FUNCTION import_run_guard()` with one more frozen column: once
`OLD.reconcile_status <> 'pending'`, `NEW.review_reason IS DISTINCT FROM OLD.review_reason` raises
`23514` ("reconciliation of import run % is final"). The downgrade restores the 0005 body
verbatim. RLS is unchanged. Tests: set at the pending → needs_review update (allowed), changed
after (rejected), on accepted (rejected). It's listed here only for the token order. Tide owns it.

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
- SELECT USING: `app_account_details_visible(transaction_account_id) AND (other_account_id IS NULL
  OR app_account_details_visible(other_account_id))`.
- INSERT WITH CHECK and DELETE USING: the read predicate AND
  `(app_account_writable(transaction_account_id) OR (other_account_id IS NOT NULL AND
  app_account_writable(other_account_id)))`.
- No UPDATE grant. `transaction_supersede_repoint` deletes and re-inserts.

**`transfer_suggestion`**
| Column | Type | Notes |
|---|---|---|
| `id`, `household_id`, `created_at` | | standard |
| `person_id` | uuid NOT NULL | composite FK → `person` CASCADE |
| `outflow_id`, `outflow_account_id` | uuid NOT NULL | the two leg FKs, CASCADE |
| `inflow_id`, `inflow_account_id` | uuid NOT NULL | the same |
| `reason` | text NOT NULL | CHECK IN ('no_hint','one_sided_hint','refund_candidate','user_treated_as_spending','ambiguous','likely') |
| `days_apart` | smallint NOT NULL | CHECK 0–5 |
| `hints` | smallint NOT NULL | CHECK 0–6 |
| `job_run_id` | uuid NULL | composite FK → `job_run(id, household_id)` ON DELETE SET NULL (job_run_id) |

- CHECK `outflow_account_id <> inflow_account_id`; `UNIQUE (person_id, outflow_id, inflow_id)`.
- Indexes: `(household_id, person_id)`, `(outflow_id)`, `(inflow_id)`.
- SELECT USING: `(person_id = app_person_id() OR app_household_wide()) AND
  app_account_details_visible(outflow_account_id) AND
  app_account_details_visible(inflow_account_id)`.
- INSERT WITH CHECK and DELETE USING: the same predicate. No UPDATE grant.

**`transfer_link`** (a move between owners, ADR 0027 point 2)
| Column | Type | Notes |
|---|---|---|
| `id`, `household_id`, `created_at` | | standard |
| `outflow_id`, `outflow_account_id` | uuid; `outflow_id` NULL after its row is deleted | FK → `transaction(id, account_id)` ON DELETE SET NULL (outflow_id); `(outflow_account_id, household_id)` → `account` CASCADE |
| `inflow_id`, `inflow_account_id` | the same | the same |
| `origin` | text NOT NULL | CHECK IN ('job','person'). `job`: step 1 marked both legs. `person`: a person accepted the edge, a person marked a leg of a `job` link, or A1 dissolved a pair. Never derived from `job_run_id`, which retention may null |
| `job_run_id` | uuid NULL | composite FK → `job_run` ON DELETE SET NULL (job_run_id) |

- CHECK `outflow_account_id <> inflow_account_id`.
- `UNIQUE (outflow_id)` and `UNIQUE (inflow_id)`. NULLs are distinct, and a leg is in at most one
  link.
- SELECT USING: both accounts detail-visible (ADR 0026 point 2).
- INSERT WITH CHECK: the read predicate AND either account writable (household-wide passes both).
- **DELETE USING: `app_household_wide()` only** (Bulwark). Only step 0 and
  `transaction_supersede_repoint` remove links.
- UPDATE USING and WITH CHECK: the read predicate AND either account writable. The guard trigger
  `transfer_link_guard` (BEFORE UPDATE, `search_path` pinned per 3.0) is accepted by Bulwark on
  these terms:
  - In a non-household-wide session it compares every column. Every column except `origin` must
    be unchanged (`ROW(NEW.*) minus origin = ROW(OLD.*) minus origin`, written out column by
    column), and `origin` may change only from `job` to `person`, never back. Anything else raises
    `42501`.
  - **Residual, recorded** (Bulwark ruling): a person who can't see the link can't convert it.
    Their UPDATE matches zero rows, silently. So if the other side unmarks later, step 0(b) can
    still undo that person's re-mark. It's rare and reversible: they mark the leg again.
  - A person who marks a leg of a `job` link converts the link to `person` in the same unit of
    work, so step 0(b) never unmarks a person's mark.
  - Bulwark's note said "delete and insert". That's impossible for a person once DELETE is
    household-wide only, so this single guarded column change is used instead.

**`transfer_leg_offer`** (the second person's half of an accepted move, Gadfly R2-B4)
| Column | Type | Notes |
|---|---|---|
| `id`, `household_id`, `created_at` | | standard |
| `person_id` | uuid NOT NULL | composite FK → `person` CASCADE |
| `transaction_id`, `transaction_account_id` | uuid NOT NULL | the two leg FKs, CASCADE. The reader's own leg, the only leg named |
| `state` | text NOT NULL | CHECK IN ('open','declined') |
| `job_run_id` | uuid NULL | composite FK → `job_run` ON DELETE SET NULL (job_run_id) |

- `UNIQUE (person_id, transaction_id)`.
- SELECT USING, INSERT WITH CHECK and DELETE USING: `(person_id = app_person_id() OR
  app_household_wide()) AND app_account_writable(transaction_account_id)`. No UPDATE grant: a
  decline deletes the `open` row and inserts a `declined` one.
- The row never names the other leg, its account or the link. The link is found by the leg
  (`transfer_link` has `UNIQUE (outflow_id)` and `UNIQUE (inflow_id)`), household-wide, by step 0.

**Triggers** (3.0 rules apply to each):
- `account_guard_transfer_owner`, BEFORE UPDATE OF `owner_person_id` ON `account` (Bulwark 6):
  - When the owner changes, it raises `23514` ("account % has transfer pairs: dissolve them
    first") while any transaction on the account, live or superseded, has a `transfer_group_id`.
  - The existence check runs with `app.household_wide` on.
  - From C4c on, A1's service dissolves those pairs first in the same unit of work (5.0), so the
    trigger is the backstop, not the mechanism.
- `transaction_dissolve_orphan`, AFTER DELETE ON `transaction` FOR EACH ROW WHEN
  (`OLD.transfer_group_id IS NOT NULL`):
  - Clears `is_transfer` and `transfer_group_id` on every remaining row of that group, under the
    caller's RLS. This is safe because both legs have one owner (ADR 0027 point 1).
  - It then checks with `app.household_wide` on. **If any row still has `transfer_group_id =
    OLD.transfer_group_id`**, it raises `42501`: the delete is refused rather than leaving a
    partner the caller couldn't clear.
  - It covers deletes outside the writer: account cascades, a future "re-import this document", an
    import undo.
  - A winner's transfer state is cleared by `unsupersede_transaction` before `remove_external`
    deletes it (PHASE-2 §3.3). So the trigger fires only for a leg that never had a loser. If that
    leg's partner is on an account the session can't write, the `42501` stands, and un-adopt
    reports `409 unadopt_blocked`.
  - **One rule for every lost leg** (Gadfly R2-S1). The survivor of a deleted pair leg is unpaired,
    whoever made the pair. That's the Phase 2 rule: no rejection, and the next refresh may pair it
    again. Step 0(a) applies the same rule to links of either origin.
- `transaction_supersede_repoint`, AFTER UPDATE OF `superseded_by_id` ON `transaction`, whenever
  the value changes. **This section is normative for the trigger.** PHASE-2 §3.3's "Trigger
  effects" is informative (Bulwark, on #62). It runs in the same statement as the writer's update, following the 3.0
  household-wide pattern, so it moves rows the superseding session can't see (Bulwark 10):
  - **NULL → value:** move `transfer_rejection` and `transfer_link` references from the row (`old`)
    to `NEW.superseded_by_id` (`INSERT … ON CONFLICT DO NOTHING`, then `DELETE`). Delete
    `transfer_suggestion` and `transfer_leg_offer` rows that reference `old`.
  - **value → NULL** (`unsupersede_transaction`): move the same references from
    `OLD.superseded_by_id` back onto the row's own id, the same way. Delete suggestions and leg
    offers that reference either id.
  - The account id never changes (the 0005 FK keeps supersession inside one account), so visibility
    of the moved rows doesn't change.
  - The M3 downgrade drops the function, both branches.

**Visibility rule.**
- A suggestion is visible only to the person it was computed for, and only while both legs are
  visible to them.
- A leg offer is visible only to its person, and only while they can write its leg. It names
  nothing else.
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

**Re-point update.** M4 replaces `transaction_supersede_repoint` so that `recurring_member` rows
also move, in **both** branches. A member row is deleted instead when its destination is already in
that series. Members move instead of vanishing, so a series whose rows are all superseded keeps its
id and state (Gadfly B3). The M4 downgrade restores M3's body, both branches.

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
| Account owner and visibility (A1) | request | person | `AccountService.change_ownership`: account `FOR NO KEY UPDATE` (4.2), then a 409 on pairs (before C4c) or `LedgerWriter.dissolve_account_pairs` (from C4c), then the account UPDATE, in one unit of work | a repeat with the same values is a no-op |
| Rule CRUD | request | author, person-scoped | `RuleService` (rules aren't ledger fields) | n/a |
| Rules at ingest | statement import request today; sync job later | the importer's session | the matcher runs in `anyio.to_thread.run_sync`, then `NewTransaction(category_id, category_source='rule', category_rule_id)` → `LedgerWriter.insert_transaction` | a re-import inserts nothing (fingerprints) |
| Rule preview | request, `anyio.to_thread` | author | none | pure |
| Rule apply (retroactive) | request | author | `LedgerWriter.apply_rule_bulk` (one UPDATE, precedence in WHERE, exact-count check) | preview token; a second apply changes 0 rows |
| Set / release category, mark / unmark, manual pair, accept / reject suggestion | request | person | `LedgerWriter`, plus `transfer_rejection` / `transfer_link` in the same unit of work | each call is a state transition; a repeat is a 409 or a no-op |
| Supersede a row | `IngestService`, statement import, `remove_external`, un-adopt (PHASE-2 §3.3) | the caller's | `LedgerWriter.supersede_transaction` / `unsupersede_transaction` (P2a), called by the ADR 0035 matcher (P2m) through P2b; Phase 4's `transaction_supersede_repoint` moves the derived rows | supersession is one-to-one (0005) |
| Link cleanup, auto-pair, auto-link | `ledger.refresh` steps 0–1 | household-wide | `pair_transfers` → `apply_transfer_pair` / `apply_transfer_link` with `job_run_id` | paired or linked rows leave the pool |
| Transfer suggestions | `ledger.refresh` step 2 | one `scoped_session` per person with a login | `refresh_person_suggestions`: delete and insert that person's rows | replace per person |
| Recurring detection | `ledger.refresh` step 3 | household-wide | `detect_rows` in the `forkserver` pool, then `reconcile_series` under `FOR UPDATE` | reconcile is a pure function of rows + stored series |

### 4.1 `ledger.refresh` steps (ADR 0029 point 3)
- **Steps 0–1** run in one household-wide session from `ctx.factory`, committed together.
  - Step 0, link cleanup. The rules:
    - A mark whose counterpart row is gone is cleared, whoever made it. That's the same rule as a
      pair (3.3).
    - The job never keeps a mark of its own whose evidence was rejected.
    - The job never changes a mark a person made for any other reason.

    The cases:
    - (a) A link with a NULL leg (its row was deleted): unmark the surviving leg when it's still
      `is_transfer`, then delete the link. Either origin.
    - (b) A `job` link where one leg is no longer `is_transfer` (a person unmarked it, possibly
      without seeing the link): write the `pair` rejection for the edge if it's missing
      (`rejected_by_person_id` NULL), unmark the other leg, and delete the link.
    - (c) Any link whose edge has a `pair` rejection: delete it. For a `job` link, also unmark any
      leg still marked.
    - (d) A `declined` leg offer whose link has `origin = 'person'`: write the `pair` rejection for
      its link's edge (with `rejected_by_person_id` set to the person who declined), delete the
      link, and delete that leg's offers. The other leg stays as its writer left it.
      - A `declined` row on a `job` link (Bulwark) gets its `pair` rejection written the same way,
        and then falls under (c), so the job's own mark on the other leg is undone.
    - (e) A `person` link with one leg unmarked and no rejection stays: it's a half-accepted move.
      For each person with a login who can write the unmarked leg, keep or insert an `open` leg
      offer, unless they declined it (Gadfly R2-B4).
      - That means the leg's owner, or every person for a joint account.
      - This runs here, household-wide, because the person can't see the link.
      - `open` offers whose leg is now a transfer, or whose link is gone, are deleted.
    - Audits carry `job_run_id`.
  - Step 1 runs `pair_transfers` over all live history and writes `pairs` (same owner) and `links`
    (different owners, a hint on each leg, 2.4).
- **Step 2:** one `scoped_session(household, person)` per person with a login, each its own
  transaction:
  - `transfer_pairing.load_candidates` (a read-only loader that builds no writer, so it works in a
    session without a user)
  - `match_transfers` with that person's visible rejections
  - `review_edges`
  - replace that person's `transfer_suggestion` rows

  It never writes a pair, a link or a leg offer (Bulwark 8).
  - An offer row (step 0(e)) names only the person's own leg. They learn nothing about the other
    leg beyond "another household member marked the other side" (ADR 0026 point 4(b)).
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

  The re-check is a separate statement, run after `_lock_group` returns. Postgres READ COMMITTED
  gives each new statement a fresh snapshot, so a rejection that committed while the job waited for
  the lock is seen (Gadfly round 2). A rejection that commits later finds the legs already paired,
  and its writer returns 409, because it re-checks under its own lock.
- **Owner locks before leg locks** (Bulwark):
  - Before `_lock_group`, every pair and link writer, manual and automatic, takes `SELECT … FOR
    SHARE` on both legs' account rows, in id order.
  - `_check_pair` reads the owners from those locked rows.
  - A1 takes `FOR NO KEY UPDATE` on the account first. That still conflicts with the pair writers' `FOR SHARE`, but not with FK checks' `FOR KEY SHARE`. So a pair can't commit against an owner change in flight.
  - A person's session can lock only accounts it may write: account RLS applies its UPDATE policy
    to row locks. A pair needs both rows locked, or it raises not-found. A person's link may lock
    just their own account, because a link is valid between any owners.
  - Lock order everywhere is accounts, then transactions, so these locks can't deadlock against
    A1's.
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
  - The healing re-push in `services.jobs.enqueue` (a duplicate while `queued`) carries the same
    `_defer_until`. Every caller passes it, and the bucket fixes it (Gadfly R2-S5).
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
  - Marking a leg of a `job` link converts the link to `person` (3.3).
  - Accepting a leg offer marks the person's leg single, which completes the link, and deletes the
    offer. Declining it leaves the leg as it is and records `declined`. Step 0(d) turns that into
    a `pair` rejection.
- **Supersession is PHASE-2 §3.3**, the single definition: `supersede_transaction(old_id, new_id)`
  and its reverse `unsupersede_transaction(old_id)`.
  - P2a builds both verbs. They're the only writers of `superseded_by_id`, and P2a owns the
    single-writer lint entry. They move category by rank, splits, transfer state, tax, receipt and
    notes, in one unit of work, with no per-row audit.
  - Phase 4 adds:
    - **C3b:** `category_rule_id` travels with the category in both verbs, and in `post_pending`
      (PHASE-2 §3.3), which also moves the category by rank when a posted Plaid row replaces a
      pending one. Without that, a posted row would show source `rule` with no rule id, and Clear
      and "Set by rule" would both miss it (Gadfly R3-B5).
    - **C4c (M3):** `transaction_supersede_repoint` moves `transfer_rejection` and `transfer_link`
      rows in both directions and deletes stale suggestions and leg offers (3.3).
    - **C5b (M4):** the same trigger also moves `recurring_member` rows (3.4).
  - Because the trigger is household-wide, the derived rows move whoever calls the verb. The
    statement import matcher and un-adopt stay in the person session, as PHASE-2 §3.3 has them.
  - Un-adopt's `unsupersede_transaction` clears the winner's transfer state before
    `remove_external` deletes it. If that can't be done (a partner on an account the session can't
    write), the `transaction_dissolve_orphan` refusal makes un-adopt return `409
    unadopt_blocked`.
- **Deletion:**
  - `transaction_dissolve_orphan` unpairs a deleted leg's partner (3.3). There's no rejection, and
    the next refresh may pair it again.
  - `remove_external` calls `unsupersede_transaction` first, so a winner that had a loser is
    deleted with no transfer state (PHASE-2 §3.3).
  - `unmark_transfer` also accepts a group with one surviving leg (Gadfly B3).
  - Step 0(a) gives links the same rule.
- Recurring: detection ignores `is_transfer` rows, and user categories don't matter. `confirmed`
  and `dismissed` stick. Setting a label sets `confirmed` unless the series is `dismissed` (Gadfly
  S1).
- Rules:
  - Disabling a rule leaves its categories (C3a semantics).
  - **Deleting a rule** (Gadfly R3-B3) returns `409 rule_has_categories` while any live row carries
    its `category_rule_id`. Otherwise delete would orphan those rows: `category_rule_id` becomes
    NULL with the source still `rule`, and neither Clear nor a filter could find them again.
    - The sheet offers **"Clear N and delete"**. It runs the clear (with its token) and the delete
      in one transaction.
    - "Keep categories and delete" passes `keep_categories=true`, which accepts the orphaning
      knowingly.
    - Superseded rows don't block a delete. They're history, and the FK sets their rule id to NULL.
    - **But clear-and-delete also resets them** (Gadfly R4-B1). `supersede_transaction` copies the
      category to the winner and leaves the loser's, so a superseded row can carry the rule's id.
      If it kept its category, un-adopt's `unsupersede_transaction` step 5 would bring it back
      live with source `rule` and a NULL rule id: the orphan this rule exists to prevent.
      - So the clear runs one more `UPDATE` over superseded rows carrying the rule's id, the ones
        the author can write. It has no count check, isn't included in N, and is audited under
        the same `rule.cleared`.
      - `keep_categories=true` leaves superseded rows alone too, knowingly.
  - Editing a rule leaves `category_rule_id` in place: "Set by rule: <name>" names the rule that
    set it, even if it has changed since.
  - A disabled rule can be previewed but not applied (409 `conflict`).
  - **Regex run-time budget** (ADR 0024 point 9):
    - Preview, apply and clear stop at 10 s of regex time with 503 `rules_too_slow`, and nothing is
      written.
    - Ingest skips `regex` rules for the rest of an import once regex time passes 0.5 ms per row
      (checked every 1,000 rows). The import result then carries `rule_notice =
      "patterns_skipped_slow"`, shown as "Some pattern rules were skipped because they were too
      slow".
    - One audit row, `rule.too_slow {rule_id}`, names the costliest rule, and one log line carries
      counts only.
    - A later retroactive apply catches the skipped rows up.
  - **"Clear this rule's categories"** (Gadfly R2-S3) undoes a bad retroactive apply with no new
    table, because `category_rule_id` already records which rule set each row.
    - A preview returns the count and a token over the sorted ids of live rows `WHERE
      category_rule_id = :rule AND category_source = 'rule' AND superseded_by_id IS NULL` that
      the author may write.
    - The clear then sets `category_id`, `category_source` and `category_rule_id` to NULL on exactly
      those rows, with an exact-count check.
    - Rows a person set since (`user`) and rows another rule took over are untouched.
    - A Plaid category the apply had replaced isn't restored. That's 0% of statement data, and the
      next sync's `modified` re-derives it from the stored PFC.

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
- `LedgerWriter.clear_rule_categories(rule_id, transaction_ids) -> int` (C3b): the 4.4 clear, as
  one `UPDATE … WHERE id = ANY(:ids) AND category_rule_id = :rule AND category_source = 'rule' AND
  superseded_by_id IS NULL`,
  with an exact-count check (`preview_stale`) and one audit row, `rule.cleared {rule_id}`.
- `unmark_transfer(transaction_id, *, never: bool = False)` follows 4.4.
  `mark_transfer_pair` / `mark_transfer_single` delete a matching rejection. `mark_transfer_single`
  converts a `job` link on that leg to `person`.
- New writer methods, all C4c:
  - `mark_transfer_link(out_id, in_id)`: a person, both legs visible, marks the writable legs and
    inserts the link
  - `apply_transfer_link(out_id, in_id, *, job_run_id)`
  - `dissolve_account_pairs(account_id)`: called by A1's service from C4c on
  - not `supersede_transaction` or `unsupersede_transaction`: P2a owns both verbs and the
    single-writer lint entry for `superseded_by_id`
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
gains two codes, **`preview_stale` (409)** and **`rules_too_slow` (503)**, added to
`web/src/api/contracts.ts` in the same PR (C3e).

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
| PATCH `/accounts/{id}` | `AccountOwnershipIn {owner?: "me" \| "joint", visibility?: "private" \| "shared" \| "summary_only"}` (at least one) → `AccountOut` | 404 (missing, invisible or not writable), 403 `forbidden` (a joint account claimed by someone without the household-owner role), 409 `conflict` (before C4c only: the account has transfer pairs), 422 (`visibility` given for a joint account) |

- **No hand-over to another person in Phase 4** (Bulwark). `ACCOUNT_WRITABLE` (0002) lets a
  person session write only rows whose `owner_person_id` is NULL or theirs, so `WITH CHECK` refuses
  a new owner who is someone else. ADR 0008 makes reassignment household-wide. `owner` is therefore
  `"me" | "joint"` only. `app/services/accounts.py` never sets `household_wide`. The household-wide
  guard test (G1, #61) fails if it does.
- Who may:
  - **The account's owner may change visibility or make the account joint.**
  - A joint account may be claimed (`owner: "me"`) only by the household owner
    (`principal.is_owner`), because a claim takes write access from the other members. **This
    check lives in the service only.** The database lets any member claim a joint account
    (`account_guard_ownership` allows it for `OLD.owner_person_id IS NULL`, ADR 0008). That's a
    stated residual: only the API is a claim path.
  - A claimed account starts `shared` unless `visibility` is given.
  - **A claim returns 409 `conflict` while another person has an unfinished import run**
    (`pending` or `needs_review`) on the account (Gadfly R3-B4). After a claim, no single person
    would pass `IMPORT_RUN_WRITE` for that run: it needs both `d.person_id = app_person_id()` and a
    writable account. The other person finishes or rejects their run first.
    - The 409 has one fixed message, "An import on this account isn't finished yet", and no
      person, run or document id (Bulwark).
    - The check runs under the claimer's RLS, which works because `document` has no per-person
      read policy today. If documents gain per-person read policies, the check must move into a
      database-side check that fails closed. The documents slice that adds those policies must
      say so.
    - **Residual, stated** (Gadfly round 4): the account lock is `FOR NO KEY UPDATE`, which doesn't
      conflict with the `FOR KEY SHARE` that the FK check takes when an import run is inserted. So
      an upload that starts after the 409 check, and commits after the claim, can still be
      stranded. It's rare: it needs the other person uploading to that account during the claim.
      It's recoverable: the household owner can make the account joint again, and the run becomes
      acceptable.
  - A joint account has no visibility of its own. ACCOUNT_VISIBLE shows every joint account to
    every member, so `visibility` on a joint account is refused with 422. Nobody "changes a joint
    account's visibility".
- One unit of work:
  1. Lock the account row. A1 takes `FOR NO KEY UPDATE` on the account first. That still conflicts with the pair writers' `FOR SHARE`, but not with FK checks' `FOR KEY SHARE`. (4.2)
  2. If the owner changes:
     - Before C4c, A1 refuses with 409 when any row on the account has a `transfer_group_id`. No
       code path can create a pair before C4c (the matcher is guarded and no endpoint pairs), so
       this is a backstop that keeps A1 out of `ledger_service.py` (file order, section 3).
     - From C4c on, A1 calls `LedgerWriter.dissolve_account_pairs(account_id)`. Every pair touching
       the account becomes two single legs (`is_transfer` stays true, no rejection), audited as
       `ledger.transfer_dissolved {group_id, reason: "ownership_changed"}`.
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
- **Joint is the recommended change** (Gadfly R2-B1). A joint account stays importable by everyone,
  because `ACCOUNT_DETAIL_WRITABLE` and `IMPORT_RUN_WRITE` both allow joint accounts. A household
  with one bookkeeper never locks themselves out, because nothing in Phase 4 can make an account
  someone else's.

### 5.1 Transactions (C6a; transfer fields from C4e; `category_rule_id` from C3e)
`TransactionOut` gains:
| Field | Type | From |
|---|---|---|
| `merchant_key` | `str \| null` | C6a |
| `category_id` | `uuid \| null` | C6a |
| `category_source` | `"user" \| "rule" \| "plaid" \| "ml" \| null` | C6a |
| `has_splits` | `bool` | C6a |
| `can_edit` | `bool`. C6a computes it inline from the account row it already joins (`owner_person_id IS NULL OR owner_person_id = principal.person_id`), because `app_account_writable` doesn't exist until M1. C3b switches it to the helper and adds an equivalence test | C6a, then C3b |
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
RuleClearPreviewOut {clear_count: int, sample: TransactionOut[] (≤ 25), preview_token: str}
RuleClearIn        {preview_token: str}
RuleClearOut       {cleared_count: int}
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
| POST `/rules/preview` | `RuleIn` → `RulePreviewOut` (a draft; nothing saved) | 422, 503 `rules_too_slow` |
| POST `/rules` | `RuleCreateIn` → 201 `RuleCreateOut`; with `apply`, create and apply in one transaction | 422, 409 `preview_stale` (nothing saved), 409 `conflict` (200 rules per author, or 25 enabled pattern rules per household) |
| GET `/rules/{id}` | → `RuleOut` | 404 |
| PATCH `/rules/{id}` | partial `RuleIn` → `RuleOut` (never applies) | 404, 403 `forbidden` (visible, not yours), 422, 409 `conflict` (caps) |
| DELETE `/rules/{id}?keep_categories=false&clear_token=` | → 204. With `clear_token`, the clear runs first in the same transaction, live rows by token plus the superseded rows carrying the rule's id (4.4). With `keep_categories=true`, the categories stay and lose their rule id | 404, 403, 409 `rule_has_categories` (live rows carry the rule's id, neither option given), 409 `preview_stale` (the clear token is stale) |
| POST `/rules/{id}/preview` | → `RulePreviewOut` | 404 |
| POST `/rules/{id}/apply` | `RuleApplyIn` → `RuleApplyOut` | 404, 403, 409 `preview_stale`, 409 `conflict` (disabled), 503 `rules_too_slow` |
| POST `/rules/{id}/clear/preview` | → `RuleClearPreviewOut` (4.4 "Clear this rule's categories") | 404, 403, 503 `rules_too_slow` |
| POST `/rules/{id}/clear` | `RuleClearIn` → `RuleClearOut` | 404, 403, 409 `preview_stale`, 503 `rules_too_slow` |

- Validation (422, fixed messages, never echoing input):
  - an unknown category
  - `amount_min > amount_max`
  - a pattern RE2 or the cost probe rejects (ADR 0024)
  - before C3d, any `mode: "regex"` ("Patterns aren't available yet.")
- **The 422 for patterns covers preview, create and PATCH alike**, because all three build
  conditions through `compile_pattern` (first-pass B3).
- Rate limit: preview, apply, clear preview and clear share one limiter, 30 per minute per user
  (the existing Redis limiter, 429 `rate_limited`).
- **The upload response** (statement import) gains `rule_notice: "patterns_skipped_slow" | null`
  (ADR 0024 point 9). The import result view shows it as "Some pattern rules were skipped because
  they were too slow".

### 5.3 Transfers review (C4e)
```
TransferEdgeOut    {suggestion_id: uuid, outflow_id: uuid, inflow_id: uuid,
                    reason: "no_hint" | "one_sided_hint" | "refund_candidate" |
                            "user_treated_as_spending" | "ambiguous" | "likely",
                    days_apart: int, can_pair: bool, can_mark_legs: bool}
TransferGroupOut   {key: str, legs: TransactionOut[], edges: TransferEdgeOut[]}
TransferOfferOut   {offer_id: uuid, leg: TransactionOut}      # the reader's own leg only
TransferReviewPage {groups: TransferGroupOut[], offers: TransferOfferOut[], next_cursor: str | null}
TransferAcceptOut  {result: "paired" | "linked", legs: TransactionOut[]}
TransferRejectIn   {scope: "pair" | "legs" = "pair"}
```
| Method + path | Body → response | Errors |
|---|---|---|
| GET `/transfers/review?limit=1..50&cursor=` | → `TransferReviewPage` | 422 |
| POST `/transfers/suggestions/{id}/accept` | → `TransferAcceptOut` | 404, 409 (a leg changed) |
| POST `/transfers/suggestions/{id}/reject` | `TransferRejectIn` → 204 | 404 |
| POST `/transfers/offers/{id}/accept` | → `TransactionOut` (the leg, now a single-leg transfer) | 404, 409 (the leg changed) |
| POST `/transfers/offers/{id}/decline` | → 204 (records `declined`; step 0(d) writes the rejection). Requires an existing `open` offer before writing `declined` | 404 (no `open` offer) |

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
- **Leg offers:** the page lists the reader's `open` offers. Each shows only the reader's own leg.
  `transfer_review_count` counts groups plus open offers.

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
     - owner: "Just me" or "Joint", with **Joint recommended** for a shared account
     - what Joint lets each person do: "Everyone in the household sees these transactions, can
       categorise them, and can import its statements"
     - visibility, for an account that's yours: private, shared, summary only, with a one-line
       consequence ("Others will see these transactions but can't change them")
     - "Transfers on this account become single entries" when the account has pairs
     - for the household owner claiming a joint account: "Only you will be able to change these
       transactions or import its statements"
   - There's no "give to another person" choice in Phase 4 (5.0).
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
   - Edit, delete, "Apply to past transactions" (preview first), and "Clear this rule's
     categories" (preview first, then "Clear N"; 4.4).
   - Delete with categories in place asks "Clear N and delete" or "Keep categories and delete".
   - The clear copy: "These N rows become uncategorised. Rows this rule took from another rule
     aren't given back that rule's category." Released rows keep their category until a rule
     changes it (`release_category`), so they aren't restored either.
4. **Transactions → Transfers to review (new tab, W3).**
   - The tab carries the count from `/ledger/status` as a badge.
   - One card per group:
     - the legs (date, account, amount in mono)
     - a plain reason, such as "Same amount, two days apart, no transfer wording", or for `likely`,
       "Looks like a transfer between your accounts"
   - Single-edge cards: "It's a transfer" and "Not a transfer".
   - Multi-edge cards: one row per candidate with the same two actions. Accepting one hides the rest
     of that card.
   - "Never a transfer" sits in an overflow menu.
   - **Leg offers** are cards with one leg, the reader's own, and the line "Another household member
     marked the other side of this as a transfer". The actions are "Yes, it's a transfer" and "No".
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
**First path, before anything else** (Gadfly round 2): **C3c → C6a → W1 → live redeploy.**
- That's three PRs, and none adds a Phase 4 migration (`category_rule_id` arrives with C3e).
  The redeploy still applies whatever revisions `main` holds by then, from any lane, rehearsed
  first (7.5 step 1).
- The result is manual categorisation on the owner's real data on the Activity tab.
- C6a depends on C3c only, so neither A1 nor C3b's RLS gate can delay it.

| Wave | Slices (parallel inside a wave) | Then |
|---|---|---|
| 0 | C3c, C4b, C5c (pure); **A1** | |
| 1 | **C6a**, C3d, A1w; **P4a** (Phase 2, holds the token before M1); **C3b** (M1) once P4a and P2a have merged | |
| 2 | **W1** | live redeploy (every pending revision on `main`, rehearsed) |
| 3 | **C3e** (after C3b, P4a, P2b) | live redeploy (A1, A1w and M1 by now) |
| 4 | W2 | live redeploy: rules usable on real data |
| 5 | **C4c** (M3, after M2) | |
| 6 | **C4d** | |
| 7 | **C4e**, **C5b** (M4) | live redeploy |
| 8 | W3, **C5d** | live redeploy: transfers on real data |
| 9 | W4 | live redeploy: recurring on real data |
| 10 | C7 (acceptance) | |

**Standing rule: Keel redeploys the live instance after each wave merges** (Gadfly R2-B3).
- Take a dump to the private directory first, then migrate.
- Before every redeploy, compare `alembic current` on the instance with `main`'s head, and
  rehearse **every pending revision**, whatever lane it came from (7.5 step 1).
- Each redeploy is recorded in `docs/planning/SPRINTS.md`, starting from the "Checkpoint
  2026-11-21 evening" entry.
- So the owner sees each screen as it lands, not at phase acceptance.
- Waves with no "Then" entry still redeploy if anything user-visible merged. The table marks the
  ones that change what the owner can do.

**Ordering notes:**
- **A1 → A1w** lets the owner make accounts joint, so the second person has data before any
  transfer slice runs.
- Then C3d + C3e + W2 (rules usable), C4c + C4d + C4e + W3 (transfers), and C5b + C5d + W4
  (recurring).
- **Recurring stays after transfers.** Otherwise unpaired card payments show as monthly "bills" a
  person could confirm. ADR 0028's all-transfers rule now deletes such a series once pairing lands,
  so the swap would be safe. Keel may swap C5b ahead of C4c if transfers slip, as long as C5b then
  carries the C4d job skeleton (steps 0–2 empty).
- **C6a and A1 share files but not a dependency.**
  - The shared files:
    - `backend/app/schemas/ledger.py`: A1 adds `AccountOut` fields only; C6a adds `TransactionOut`
      and filter params only.
    - `backend/app/services/ledger.py`: A1 changes the `accounts()` row mapping only; C6a changes
      `transactions()` filters and mapping only.
    - `web/src/api/schema.d.ts`: generated.
  - When both are open, C6a merges first. A1 rebases (additive hunks in different classes and
    functions) and reruns `make api-types`.
- **A1 and C3b share nothing.** A1 no longer edits `ledger_service.py` or its tests (5.0), which
  answers Gadfly R2-S4.
- **`ledger_service.py`:** P2a → C3b → C4c → C5b, the Phase 2 order. C4d doesn't edit it, because
  the M3 trigger handles suggestions and offers.

### 7.2 Slices
| ID | Slice | Kind / owner | Depends | Gates | Acceptance evidence |
|---|---|---|---|---|---|
| **C3b** | M1 (rules), helper functions, `Rule` model, `category_rule_id`, `NewTransaction.category_rule_id`, `apply_category(rule_id)`, `apply_rule_bulk`, `clear_rule_categories`, `category_rule_id` in both supersede verbs | db · Ledger (token) | P4a (token), P2a | Bulwark, Ledger | RLS matrix for `rule`; `rule_scope_guard` and `rule_caps` (two sessions racing past a cap); guessed-id FKs; helper equivalence and `prosecdef = false`; bulk apply exact-count and precedence; up/down/up, **including a downgrade that raises on a non-empty table** |
| **A1** | `PATCH /accounts/{id}` (`me` / `joint`, visibility), `AccountService`, `AccountOut` / `ImportBlockOut` owner fields; 409 while the account has pairs | db · Ledger | — | Bulwark, Ledger | See 7.3 A1 |
| **A1w** | Account owner sheet; the accounts panel and import result show the owner | web · Prism | A1 | Prism, Sounding | vitest; 375 px; **e2e `accounts.e2e.ts`**: upload a synthetic statement, change the owner to joint from the result view, and see it in the accounts panel |
| **C3c** | `merchant.py`, `rule_regex.py` (refuses everything), rules v2 (scope, splits, ordering, `preview_for`, `preview_token`, codec with `merchant_key_version`) | pure · Codex | — | Ledger, Bulwark | Table and property tests; draft token = saved token; order independence; the `ast` import test; `repr(DescriptionCondition)` has no value |
| **C4b** | transfers v2 (rejections, `links`, joint-as-owner, `review_edges` with `likely`, `group_edges`) | pure · Codex | — | Ledger | A rejected edge is never paired, linked or suggested over 500 random pools; joint versus owned gives `links`; a hidden competitor in the household view still gives a `likely` edge in the person's view (Gadfly B2); grouping is order-independent |
| **C5c** | `recurring_series.py` (`series_status`, `series_tier`, `stored_confidence`, `detected_from`, `reconcile_series`, `detect_rows`) | pure · Codex | — | Ledger | Property tests for the ADR 0028 invariants; tie and takeover rules; the lapsed-predecessor merge; the PLAN 1.5-interval boundary table; the tier at 0.6004 equals the tier at 0.600 |
| **C6a** | Transaction category API (5.1, C6a rows), `TransactionOut` fields, filters | db · Ledger | C3c only (A1 shares files, 7.1) | Bulwark, Ledger | API tests across private/shared/summary_only/joint; `make api-types` diff; superseded → 409 |
| **W1** | Activity tab chips, filters, row sheet with the category picker and release, rule toast stub | web · Prism | C6a (mocks earlier) | Prism, Sounding | vitest; 375 px; read-only rows show no actions; **e2e `activity.e2e.ts`**: upload, set a category, filter "needs category" |
| **C3d** | RE2 in `rule_regex.py`, cost probe, dependency pin, corpus gate, `docs/user/rules.md` | pure + lockfile · Ledger | C3c | Bulwark, Ledger | ADR 0024 point 7 gate (required, on CI); probe rejects the corpus' blow-ups; `import re2` only in one module; a failed `import re2` gives `unavailable`; pip-audit clean |
| **C3e** | `RuleService`, `/api/rules` (including clear), ingest-time rules in statement import and in `IngestService` (matcher in a thread), `preview_stale` code, `category_rule_id` in `TransactionOut` | db · Ledger | C3b, C3c, C6a, P4a (`statement_import.py`), P2b (`IngestService`); C3d for regex | Bulwark, Ledger | Preview count = applied count; a second apply = 0; clear preview count = cleared count; a Plaid row matching a rule gets `category_source = 'rule'`; scope across two people (`own` invisible to the other; `any` never touches the other's private rows); caps → 409; audit details ids only; a re-import inserts nothing and keeps categories; bounded work (fixed query count per preview/apply, one UPDATE per apply) |
| **W2** | Rule sheet, preview, Settings → Rules, "make a rule" entry points | web · Prism | C3e (mocks earlier) | Prism, Sounding | vitest for the preview → save-and-apply flow and the stale path; scope default by source account; visibility line; 375 px; **e2e `rules.e2e.ts`**: make a rule from a row, preview, save and apply, see the list in Settings |
| **C4c** | M3, transfer models, writer changes (links, rejections, leg offers, owner locks, unmark rules, `transaction_supersede_repoint`), `pair_transfers` v2 + `load_candidates`, A1's switch to `dissolve_account_pairs` | db · Ledger (token) | C3b, C4b, A1, M2 (token) | Bulwark, Ledger | See 7.3 C4c |
| **C4d** | `ledger.refresh` job steps 0–2 (including leg offers), `refresh_person_suggestions`, `request_refresh` helper, arq pool in the API, `defer_until` in `enqueue` (and its re-push), enqueue after import and ownership change, 5.5 endpoints | db · Ledger | C4c | Bulwark, Ledger | See 7.3 C4d |
| **C4e** | Transfer endpoints (5.1 transfer rows, 5.3, including leg offers), `TransactionOut` transfer fields, enqueue after edits | db · Ledger | C4d, C6a | Bulwark, Ledger | Accept `paired` vs `linked`; the second person still sees their half; an offer's response holds only the reader's leg; reject `pair` vs `legs`; 409 on a changed leg; the guard test still forbids `pair_transfers` / `apply_transfer_*` in `app/api/` |
| **W3** | Transfers to review tab, badge, sheet transfer actions | web · Prism | C4e | Prism, Sounding | vitest for accept / reject / multi-edge cards; 375 px; **e2e `transfers.e2e.ts`**: upload synthetic checking and card statements, see a card payment excluded, accept one suggestion, unmark it |
| **C5b** | M4, recurring store service, job step 3 with the `forkserver` pool, the M4 re-point update | db · Ledger (token) | C5c, C4d | Bulwark, Ledger | See 7.3 C5b |
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
  - `rule_caps` with the advisory lock (ADR 0024 cap). The household-wide count lets one person
    learn how many `own` pattern rules another has, through the 409. That's recorded as an
    accepted leftover in ADR 0026 point 4 (Bulwark).
  - `clear_rule_categories` (4.5), with `AND superseded_by_id IS NULL` as in `apply_rule_bulk`.
  - `category_rule_id` travels with the category in `supersede_transaction`,
    `unsupersede_transaction` and `post_pending` (PHASE-2 §3.3). Tests:
    - supersede then unsupersede a rule-categorised row: `category_rule_id` is on the live row each
      time
    - `post_pending` over a rule-categorised pending row: the posted row has the rule id
  - Switch C6a's inline `can_edit` to `app_account_writable`, with an equivalence test (5.1). The
    test covers own-private, own-shared, own-summary_only, joint, and another person's shared
    account (false), each against `app_account_writable` (Bulwark round 5).
- **C6a** (Bulwark round 5):
  - Compute `can_edit` exactly as `owner_person_id IS NULL OR owner_person_id =
    principal.person_id`.
  - Never read `visibility`.
  - `can_edit` is false when the principal has no person.
- **A1:**
  - `owner` is `"me" | "joint"` only. Any other value is a 422 from the schema (Bulwark).
  - `app/services/accounts.py` never sets or mentions `household_wide`.
  - The joint-claim check (`principal.is_owner`) is service-only. Say so in the code comment and
    the PR; the database allows any member (ADR 0008).
  - `visibility` on a joint account is a 422.
  - A1 takes `FOR NO KEY UPDATE` on the account first. That still conflicts with the pair writers' `FOR SHARE`, but not with FK checks' `FOR KEY SHARE`. Return 409 while any row on it has a `transfer_group_id`
    (C4c replaces that with the dissolve).
  - Tests:
    - the owner makes an account joint
    - the household owner claims a joint account, and a non-owner claim is a 403
    - `owner: <uuid>` is a 422
    - summary-only viewers and other people's private accounts are a 404
    - **after `joint`, both people can upload a statement to it, and each person accepts their
      own `needs_review` run**
    - a claim while the other person has a `pending` or `needs_review` run on the account is a
      409 with the fixed message and no ids
    - audit detail has ids only
- **C3c:**
  - Delete the C3a `re` validator. Until C3d, `compile_pattern` raises `RuleValidationError("Patterns
    aren't available yet.")` for every input (Bulwark 2).
  - The `ast` test: `rule_regex.py`, `app/domain/rules.py` and `app/services/rules.py` import
    neither `re` nor `regex` (Bulwark 2).
  - `DescriptionCondition.value` is `field(repr=False)` (Bulwark 12).
  - Remove `RuleConditions.account_ids` (Bulwark).
- **C3d:**
  - ADR 0024 points 2, 4b, 5, 7 and 8 as written (Bulwark 1 and its round-2 text):
    - no fallback
    - `MAX_SUBJECT_LENGTH = 256`
    - the cost probe: a family for every class atom, including negated, escape and POSIX classes
      and `.`; per-literal mixed families; more than 16 atoms rejected; the 10 µs limit per family
    - the gate: each corpus pattern either rejected by `compile_pattern` or 50,000 searches of
      1,024-character subjects in under 1 s on CI, over subjects independent of the probe
  - The corpus includes:
    - the three DFA blow-up shapes
    - Bulwark's three round-2 evaders (`(?:[J][K][V][Q])?[XY]*X[XYZ]{20}W`, `\d*[0-4]\d{20}#`,
      `[^5-9]\d{20}#`)
    - Lattice's two (`\w*Q\w{20}#`, `[[:digit:]]*1[[:digit:]]{20}#`)
    - Bulwark's four round-3 evaders (`(?:0123456789ABCDEFGHIJKLMNOPRSTUVW)?\w*Q\w{20}#`,
      `\w*QJ\w{20}#`, `[A-Z]*XZ[A-Z]{20}#`, `.*QJ.{20}#`)

    Each must be rejected.
  - Probe families for adjacent literal runs of 2 to 3 characters, mixed with each atom's alphabet.
  - No 32-literal cap. Above 32 distinct literals, use 8 subjects per literal.
  - Record the CI numbers in the PR.
- **C3e:**
  - Rule DELETE: `409 rule_has_categories`, "Clear N and delete" in one transaction, and
    `keep_categories=true` (4.4).
    - **Order** (Bulwark round 5): 404, then 403, then `rule_has_categories`. A non-author never
      reaches the count.
    - The 409 and the "Clear N" count are computed in the author's person-scoped session, never
      household-wide.
    - Rows the author can no longer see don't block the delete. The FK orphans them, and that's
      accepted.
    - Clear-and-delete also resets superseded rows that carry the rule's id (Gadfly R4-B1).
    - Tests:
      - deleting a rule with live rows is refused
      - clear-and-delete leaves no row *the author can see* with source `rule` and a NULL rule id
      - superseded rows don't block the delete
      - **supersede, clear-and-delete, unsupersede: the old row is uncategorised**
  - The ingest matcher runs in `anyio.to_thread.run_sync` (Bulwark 1).
  - The 25 enabled `regex` rules per household → 409 (Bulwark 1).
  - **Budget per ADR 0024 point 9** (Bulwark round 3). Tests:
    - a probe-passing evader (`\w*QJ\w{20}#`, loaded through the codec with the probe bypassed
      in the test) over crafted rows trips the budget, and nothing is written
    - the import completes with the notice
    - apply after a stopped preview writes nothing different
  - Clear preview and clear share the 30-per-minute limiter.
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
  - `transaction_supersede_repoint`, both branches (3.3): rejections and links move, and
    suggestions and offers are deleted, household-wide in the same statement (Bulwark 10;
    PHASE-2 §3.3 owns the verbs).
  - Links need a hint on each leg (2.4, Bulwark).
  - Owner locks (4.2, Bulwark): `FOR SHARE` on both legs' accounts, in id order, before
    `_lock_group`. `_check_pair` reads owners from the locked rows.
  - `transfer_link`: DELETE USING is `app_household_wide()` only. `transfer_link_guard` allows a
    person only `origin` `job` → `person`, which a mark of a `job` link's leg performs (Bulwark).
  - `transaction_dissolve_orphan` raises if any row still has `transfer_group_id =
    OLD.transfer_group_id` (Bulwark; Gadfly B3).
  - State INSERT as WITH CHECK and DELETE as USING for `transfer_rejection`, `transfer_link`,
    `transfer_suggestion` and `transfer_leg_offer` (Bulwark).
  - Switch A1 from the 409 to `dissolve_account_pairs`.
  - The auto writers' locked re-check of rejections is a separate statement after `_lock_group`
    (4.2, Gadfly B5). Tests:
    - unmark, then rerun: never re-paired
    - delete one leg, then unmark the other: succeeds (no orphan is left)
    - unmark, then supersede, then rerun: nothing re-paired
    - a rejection committed while the job holds no lock is honoured
    - a cross-owner edge in the household view is linked, and either person can undo their leg
    - a hidden `job` link's unmark becomes a `pair` rejection at step 0, and the job undoes its
      own mark on the other leg
    - `dissolve_account_pairs` writes a `person` link per dissolved pair
    - **an owner change and a job pairing on that account, interleaved: no cross-owner group**
      (Bulwark)
    - a cross-owner edge hinted on one leg only isn't linked (Bulwark)
    - a person marks a leg of a `job` link, the other person unmarks theirs, then step 0 runs: the
      first person's mark stays
    - supersede: a rejected pair isn't re-paired on the winner, and a link follows its leg to the
      winner
    - **supersede then unsupersede: rejections and links are back on the old row**
    - supersede deletes the old row's suggestions and offers
    - `transfer_link_guard` refuses `person` → `job`, and any change to a leg or account column,
      from a person session (Bulwark)
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
    - **B accepts a move from B's private checking to A's shared card, and A gets a leg offer that
      names only A's leg.** A's decline becomes a `pair` rejection at the next step 0 (Gadfly
      R2-B4)
    - a deleted leg's surviving link partner is unmarked, for both origins (Gadfly R2-S1)
    - the healing re-push carries `_defer_until`
    - a declined offer on a `person` link becomes a rejection; one on a `job` link also undoes the
      job's mark on the other leg; decline without an `open` offer is a 404
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
    - the M4 trigger: a dismissed series stays dismissed after its members are superseded
    - **unsupersede: members back on the old row; series id and state unchanged**
    - a series whose members all became transfers is deleted
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
  - unhinted transfers between accounts at least one person can see both of (suggestions)
  - an unhinted move from B's private checking to A's shared card, so B is offered it, and A gets a
    leg offer once B accepts
  - **the residual:** exactly two unhinted moves between B's private checking and A's private
    savings. No single person can see both legs. Their amounts are recorded, and criterion 2(d)
    adds them back.
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
     Record criterion 2's numbers here, with no clicks.
  6. **Scripted review** (Gadfly R2-B2). It's deterministic, in person order (A, then B), oldest
     card first:
     - Each person accepts every review card and leg offer whose truth is a transfer, picking the
       true edge on a multi-edge card, and rejects (`scope = "pair"`) or declines the rest.
     - Then one refresh, and one more pass of the same policy for anything new.
     - Criteria 2(b) and 2(d) are measured after this step.
- **Measures:** exactly section 0 criteria 1–4. The run also reports:
  - share of Plaid-sourced rows (0)
  - links, pairs and suggestions per person
  - false series per tier
  - the PR #52 and PR #58 detector limits by category
- **Follow-up trigger:** if bill-tier recovery is under 80% or false bill-tier series are over 5%,
  Keel schedules a detector follow-up.

### 7.5 Real-data run (Keel decision; Gadfly B4)
Holdfast agents (Sounding, with Ledger for the loader) run it locally under ADR 0019, on the owner's
machine, and report counts only.

**Record** (Gadfly R2-B3, R3-B1): `docs/planning/SPRINTS.md`, "Checkpoint 2026-11-21 evening",
committed with this contract. It records:
- **The owner's go-ahead,** in their words, dated 2026-11-21: "hook up every connector with our data
  so i can view it on my phone". The owner also confirmed that this laptop is the long-term host.
- **Upgrade 1:** dump taken (the private backups directory), 0008 and 0009 applied, `alembic
  current` = `0009_import_run_review_reason (head)`, counts unchanged, tailnet 200.
- **Upgrade 2:** after #32, with a dump, 0010 applied, `alembic current` =
  `0010_list_household_ids (head)`, counts unchanged, tailnet 200. A re-run over all 125 PDFs
  inserted 0 rows.

The standing redeploy rule (7.1) rests on that go-ahead. If the owner withdraws it, each redeploy
that carries a migration becomes an owner-queue item.

1. **Before every redeploy of the instance** (Gadfly R3-B2):
   - Take a fresh dump to the private directory.
   - Compare `alembic current` on the instance with `main`'s head. Every revision in between is
     pending, from Phase 4 or any other lane (`0010`, P4a's `connector_link`, and so on).
   - Restore the dump into a throwaway local stack: its own compose project, non-dev ports, and a
     data directory inside the private directory.
   - Run the pending revisions up, down and up there, and check row counts. Only then does Keel
     upgrade the instance.
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
| C3b | `backend/migrations/versions/<n>_rules.py`, `backend/app/services/ledger.py` (`can_edit` to the helper), `backend/app/models/transactions.py`, `backend/app/models/__init__.py`, `backend/app/services/ledger_service.py`, `backend/tests/models/test_rls_rules.py`, `backend/tests/models/test_migration_rules.py`, `backend/tests/ledger/test_ledger_service.py`, `backend/tests/test_ledger_single_writer.py` |
| A1 | `backend/app/api/routes_accounts.py` (new), `backend/app/schemas/accounts.py` (new), `backend/app/services/accounts.py` (new), `backend/app/schemas/ledger.py` (`AccountOut` fields), `backend/app/services/ledger.py` (account fields), `backend/app/schemas/imports.py`, `backend/app/services/imports.py` (block owner fields), `backend/tests/api/test_account_ownership.py`, `web/src/api/schema.d.ts` (generated). Not `ledger_service.py` (file order, section 3) |
| A1w | `web/src/pages/transactions/AccountsPanel.tsx`, `web/src/pages/transactions/AccountOwnerSheet.tsx` (new), `web/src/pages/import/ResultView.tsx`, `web/src/api/accounts.ts` (new), `web/src/api/imports.ts`, `web/src/mocks/accounts.ts` (new), `web/src/mocks/imports.ts`, `web/src/mocks/handlers.ts`, `web/e2e/accounts.e2e.ts`. Not `api/ledger.ts` or `mocks/ledger.ts`, which are W1's |
| C3c | `backend/app/domain/{merchant,rule_regex,rules}.py`, `backend/tests/domain/{test_merchant,test_rule_regex,test_rules,test_regex_imports}.py` |
| C4b | `backend/app/domain/transfers.py`, `backend/tests/domain/test_transfers.py` |
| C5c | `backend/app/domain/recurring_series.py`, `backend/tests/domain/test_recurring_series.py` |
| C6a | `backend/app/api/routes_ledger.py`, `backend/app/schemas/ledger.py`, `backend/app/services/ledger.py`, `backend/tests/api/test_ledger*.py`, `web/src/api/schema.d.ts` |
| W1 | `web/src/pages/transactions/**` (except A1w's files), `web/src/mocks/ledger.ts`, `web/src/api/ledger.ts`, `web/e2e/activity.e2e.ts` |
| C3d | `backend/app/domain/rule_regex.py`, `backend/pyproject.toml`, `backend/uv.lock`, `backend/tests/domain/test_rule_regex.py`, `backend/tests/domain/regex_corpus.py` (new), `docs/user/rules.md` |
| C3e | `backend/app/services/rules.py`, `backend/app/api/routes_rules.py`, `backend/app/schemas/rules.py`, `backend/app/services/statement_import.py` (after P4a), `backend/app/services/ingest.py` (inline rules, after P2b), `backend/app/schemas/imports.py` + `backend/app/services/imports.py` (`rule_notice`), `backend/app/services/errors.py`, `backend/app/schemas/ledger.py` + `backend/app/services/ledger.py` (`category_rule_id`), `web/src/api/contracts.ts` (error code only), `web/src/api/schema.d.ts`, `backend/tests/api/test_rules*.py`, `backend/tests/services/test_rules*.py`, `backend/tests/bench/test_rules_bench.py` |
| W2 | `web/src/pages/settings/Rules*.tsx`, `web/src/features/rules/**` (new), `web/src/mocks/rules.ts`, `web/src/mocks/handlers.ts`, `web/src/api/rules.ts`, `web/src/app/routes.tsx`, `web/e2e/rules.e2e.ts` |
| C4c | `backend/migrations/versions/<n>_transfer_corrections.py`, `backend/app/models/transfers.py` (new), `backend/app/models/__init__.py`, `backend/app/services/{ledger_service,transfer_pairing}.py`, `backend/app/services/accounts.py` (the dissolve switch), `backend/tests/api/test_account_ownership.py`, `backend/tests/models/test_rls_transfers.py`, `backend/tests/models/test_transfer_triggers.py`, `backend/tests/ledger/test_ledger_service.py`, `backend/tests/ledger/test_transfer_pairing.py`, `backend/tests/test_ledger_single_writer.py` |
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
| PR #55 Keel | Freeze `review_reason` in `import_run_guard` (own migration) | M2 (3.2), I1d, Tide; in the shared token order |
| ADR 0016 | Wire `arq_pool` into the API when the first API-triggered job lands | C4d |
| Bulwark on PR #59 | Items 1–14, then round 2 | Section 7.3 per slice; section 11 |
| Phase 2 contract §3.3 and §4 (round 3) | One verb pair (`supersede_transaction`, `unsupersede_transaction`, P2a, §3.3), and the shared migration-token and file order (§4) | D15, 3, 3.3, 3.4, 4.4, 7.1, 7.6. Phase 4 owns the re-point trigger (M3, M4) and `category_rule_id` in both verbs (C3b) |

---

## 9. Owner decisions (each has a default, so nothing is blocked)

1. **Rules are visible to everyone in the household** unless limited to your own accounts. Rules
   you make from your own accounts' rows start limited. Default: yes (ADR 0026).
2. **A move between two people's accounts** is recorded as two linked single transfers, marked
   automatically when the wording says so, and each person can undo their side. Default: yes (ADR
   0027).
3. **Only the household owner can take a joint account for one person**, and nobody can hand an
   account to someone else in Phase 4. Joint is the recommended setting for a shared account.
   Default: yes (D14).

The real-data checks are no longer an owner item. Holdfast agents run them locally (section 7.5).

---

## 10. Out of Phase 4 (recorded, not built)
- the ML classifier and nightly retrain (cut from Q1)
- rule actions for tax tag, rename and transfer marking
- per-rule account lists, and a per-account opt-out from another person's rule
- **handing an account to another person:** this needs a narrow owner-run function with its own
  ADR (ADR 0008: reassignment is household-wide). That ADR must also give the giver a way to keep
  importing the account's statements (Gadfly R2-B1).
- a full undo record for retroactive apply (the "clear this rule's categories" action in 4.4 is the
  Phase 4 undo)
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
lane (its key entry and slot-tombstone migration are separate stories). It adds steps to the Phase
2 supersede verbs (`category_rule_id`, C3b) and owns their re-point trigger (M3, M4). It follows the
shared token and file order (section 3; PHASE-2 §4). Un-adopt stays in the person session, as PHASE-2 §3.3
has it. Phase 4's ADRs are 0024 and 0026–0029. 0023 and
0025 belong to the Plaid lane.

---

## 11. Review responses

### 11.1 Gadfly, first pass (`reviews/PHASE-4-gadfly.md`)
| Item | Response | Where |
|---|---|---|
| B1 cross-owner moves fail criterion 2 and double-count | **Accepted.** Cross-owner edges that meet the auto bar are linked automatically: both legs marked, plus a `transfer_link`. Joint versus owned counts as different owners. A hidden link's unmark becomes a `pair` rejection at step 0, so no `leg` rejection is written by accident. A card is hidden only when every leg the reader can write is a transfer. The leak is stated and accepted. The C7 holdout includes joint checking paying a personal card, and a private-to-private move | 2.4, 3.3, 4.1, 4.4, 5.3, ADR 0026, ADR 0027, 7.4 |
| B2 supersession drops people's corrections | **Accepted.** The writer half is PHASE-2 §3.3 (`supersede_transaction` / `unsupersede_transaction`, P2a): category by rank, splits, transfer state, tax, receipt and notes. The trigger half is Phase 4's (`transaction_supersede_repoint`, both directions). Tests: a rejected pair isn't re-paired on the winner, and supersede then unsupersede restores the old row's rows | 3.3, 3.4, 4.4, 7.3 C4c, C5b |
| B3 the regex gate is in the API only | **Accepted.** `compile_pattern` refuses every input until C3d, so the codec, loader and API all refuse. The `ast` import test. The 422 covers preview, create and PATCH. "Skipped and flagged" is `RuleOut.status` | 2.2, 5.2, ADR 0024 point 2 |
| B4 C7 has no evidence plan | **Accepted.** Generator module, seed custody, labelling policy, denominators, precision as slug equality, Plaid share 0, owners in the holdout, bill-tier recovery, false-series caps | 0, 7.4 |
| S1 rule visibility leaks and naming | **Accepted in part.** A rule from a private row defaults to `own`. `any` reads "Joint and my accounts". Account lists are removed, so "future accounts" is no longer an issue. **Rejected:** a per-account opt-out from another person's rule. That person can set the row (a user choice wins) or make a higher-priority rule. Recorded in section 10 | 2.3, 3.1, 6 (item 2), 10 |
| S2 slice order and sizes | **Accepted.** C6a depends on C3c, not C3b. "Needs category" is stated plainly. C4c is split into C4c/C4e and C5b into C5b/C5d | 6 (item 1), 7.1, 7.2 |
| S3 missing preconditions | **Accepted.** The PR #58 row is added. The guard's allowed files are named | 8 |
| S4 `merchant_key` unversioned for rules | **Accepted.** `merchant_key_version` is in the conditions JSON, and a mismatch is `needs_review`. Whether to ship a rewrite migration is decided in the bump PR | 2.3, 5.2 |
| S5 retroactive apply keeps no history | **Rejected in Revision 2, reversed in Revision 3** (Gadfly R2-S3): "re-derivable" was false for rows that were uncategorised. There's still no apply-record table, but "Clear this rule's categories" is added | 4.4, 5.2, 10 |
| S6 contradictions with the spec | **Decided.** Savings: ADR 0022 rule 1 applies to income and spending, and rule 3 reads `savings`-kind outflows whatever their `is_transfer`. Pairing never changes categories, so a paired move into savings still counts as saving (ADR 0027 Consequences). Rules vs transfers: the deviation from PLAN 8.2 is recorded (ADR 0029). Grouping by account: kept and recorded (ADR 0028 Consequences, section 10) | ADRs 0027–0029 |
| S7 builder guesses | **All decided.** PATCH keeps `category_rule_id`. Same category but a new source counts as a change. A disabled rule can't be applied (409). Refresh is coalesced (4.3). "Check again" uses a 1-minute bucket. Caps: 200 per author including disabled, 25 enabled patterns per household | 2.3, 4.3, 4.4, 5.2 |
| RE2 exit | **Accepted.** Harbor owns the watch. Triggers: a Python bump with no wheel, a `pip-audit` finding with no fix, or 18 months with no release. The exit is regex rules showing `unavailable`, plus an `any_of` literal-list mode | ADR 0024 Consequences |

### 11.2 Gadfly, second pass (`reviews/PHASE-4-contract-gadfly.md`)
| Item | Response | Where |
|---|---|---|
| B1 no ownership producer; owner changes break the invariant | **Accepted (Keel decision).** A1 is added before every transfer slice. `PATCH /accounts/{id}` (`me` / `joint` since Revision 3) refuses, and from C4c dissolves, pairs in the same unit of work, and the import result offers the change. `account_guard_transfer_owner` is the DB backstop. Phase 4 isn't declared single-owner | 5.0, 6 (item 0), 3.3, 7.1 |
| B2 the per-person pass drops confident pairs | **Accepted.** `pairs` and `links` in a person's view become `likely` suggestions. Step 2 never writes pairs | 2.4, 3.3, 4.1, 7.3 C4b |
| B3 deleting or superseding a paired leg corrupts the partner and series | **Accepted.** `transaction_dissolve_orphan`, `unmark_transfer` with one survivor, member moves in the M4 trigger (writer half: PHASE-2 §3.3), live-only member listings. Both tests named | 3.3, 3.4, 4.4, 7.3 |
| B4 acceptance can pass without real data | **Accepted (Keel decision).** Agents run the real-data check locally on a restore of the owner's instance, with counts only. The upgrade dependency is dropped (the instance is at `0009` with a dump). Criterion 4 is rewritten so it can fail. One e2e per screen against the real API | 0, 7.2, 7.5 |
| B5 the job overwrites choices made while it runs | **Accepted.** `FOR UPDATE` and a re-plan after detection, guarded deletes, upserts that never write user fields, and the rejection re-check under the leg lock | 4.2, ADR 0028, ADR 0029 |
| S1 series identity holes | **Accepted.** A label implies `confirmed`. The tie rule prefers user-stated series, and the takeover rule moves state to the detection | 2.5, ADR 0028 points 1–2 |
| S2 the process pool can't kill a task | **Accepted, in Bulwark 5's form:** a `forkserver` `ProcessPoolExecutor`, the worker terminated and the pool recreated on timeout. The spec timeout is explicit (300 s). The path is `app/worker/main.py` | 4.3, 7.3 C5b |
| S3 refresh fan-out and staleness | **Accepted (Keel decision).** 2-minute buckets deferred to the bucket's end, with no tail check needed (4.3 proves it). Transfer edits and ownership changes enqueue the same key | 4.3 |
| S4 migration path too long | **Accepted in part.** Revision 2 took M2 out. Revision 3 puts it back in the shared token order (PHASE-2 §4), still owned by Tide. **Rejected:** merging M3 and M4, because one migration per slice keeps each RLS review whole and keeps Bulwark's items attached to their slices. **Rejected:** recurring before transfers (7.1, with the reason) | 3, 7.1 |
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
| 10 supersede re-points rejections | **Accepted.** The writer half is PHASE-2 §3.3. The trigger half is Phase 4's `transaction_supersede_repoint`: household-wide, in the same statement, both directions, so it moves rows the session can't see. Revision 3's session rule is withdrawn | 3.3, 4.4 |
| 11 M1 downgrade; `rule_scope_guard` on author | **Accepted** | 3.1 |
| 12 `repr=False`; audit details; `rule.applied {count}` | **Accepted.** Decision: no count for any scope, because `any` rules also touch the author's private rows | 2.3, 4.5 |
| 13 `recurring_series` UPDATE policy | **Accepted** | 3.4 |
| 14 manual key format | **Accepted:** `refresh:manual:20261121T1005Z` and `refresh:auto:20261121T1004Z` | 4.3 |

### 11.4 Bulwark, round 2 (review of `f651389`)
| Item | Response | Where |
|---|---|---|
| Must-fix 1, the cost probe is evadable | **Accepted, with the exact text for points 4b, 7 and 8, plus two measured amendments.** (1) Bulwark's mixed family (half the atom's alphabet, half *all* literals) still accepted `\w*Q\w{20}#` (128 µs per search on crafted subjects) and `[[:digit:]]*1[[:digit:]]{20}#` (65 µs), because a terminator literal like `#` breaks the chain. So the mixed subjects are drawn per literal. (2) The limit applies to each family's mean, so decoy atoms can't dilute a costly family. Re-measured: Bulwark's three evaders are rejected at 68, 64 and 71 µs (worst family), and a 13-decoy variant at 72 µs | ADR 0024 points 4b, 7, 8; 2.2; 7.3 C3d |
| Must-fix 2, A1 can't hand an account to another person | **Accepted (Keel chose option a).** `owner` is `"me" \| "joint"`. "Who may" is replaced. Section 10 gains the hand-over line. 7.1 says "joint". `accounts.py` never gains `household_wide` | 5.0, 6 item 0, 7.1, 7.3 A1, 10 |
| Must-fix 3, a cross-owner link from one person's evidence | **Accepted as written** | 2.4, 4.1, ADR 0027 point 2 |
| Must-fix 4, the owner-change race | **Accepted as written.** One stated limit: a person's session can't lock another person's account row (account RLS applies to row locks). A pair needs both rows locked; a person's link locks only their own, because links don't depend on owners | 4.2, 7.3 C4c, ADR 0027 point 7 |
| C4c notes | **Accepted.** DELETE on `transfer_link` is household-wide only. **Deviation:** "delete and insert" can't run in a person session once DELETE is household-wide, so the `job` → `person` conversion is a single guarded `origin` UPDATE (`transfer_link_guard`). The orphan trigger's wording is changed. INSERT is WITH CHECK and DELETE is USING for all four tables | 3.3, 7.3 C4c |
| A1 notes | **Accepted.** The service-only claim check is stated as a residual. The 422 for `visibility` on a joint account is kept, and "any member may change a joint account's visibility" is removed | 5.0 |
| C3b and C3c notes | **Accepted.** The cap-count leftover is recorded in ADR 0026 point 4. `RuleConditions.account_ids` is removed | 2.3, 7.3, ADR 0026 |

### 11.5 Gadfly, round 2 (`reviews/PHASE-4-contract-gadfly.md`, "Round 2")
| Item | Response | Where |
|---|---|---|
| R2-B1 giving an account away locks the uploader out | **Closed by Bulwark must-fix 2.** There's no hand-over in Phase 4. The sheet recommends Joint and says what Joint lets each person do. The follow-up is recorded with the hand-over ADR | 5.0, 6 item 0, 10 |
| R2-B2 criterion 2(d) can't pass | **Accepted.** Step 6 is a deterministic scripted reviewer. A fixed, reported residual is planted, so (d) is an exact equality that any missed or wrong exclusion breaks. It's also reported before any click | 0, 7.4 |
| R2-B3 the go-ahead isn't recorded | **Accepted (Keel's record).** The 2026-11-21 upgrade is cited from the sprint log, with a standing redeploy-after-each-wave rule under the owner's full-autonomy decision | 7.1, 7.5 |
| R2-B4 a half-accepted private move never reaches the second person | **Accepted, with a different shape than proposed.** Gadfly's fix relaxed the suggestion policy to one visible leg, but a suggestion row names both legs and both accounts. A new `transfer_leg_offer` row names only the reader's own leg. Step 0 creates it household-wide, so the person never reads the link. Decline becomes a `pair` rejection at the next step 0 | 3.3, 4.1, 5.3, 6 item 4, 7.3 C4d |
| R2-S1 the delete trigger and links disagree | **Accepted.** One rule: the survivor of a deleted leg is unpaired or unmarked, whoever made the mark. That's the Phase 2 rule | 3.3, 4.1, ADR 0027 point 9 |
| R2-S2 Plaid supersession splits confirmed series | **Accepted.** The newest-member rule, with a table test (12 of 30 under a new key) | 2.5, ADR 0028 point 1 |
| R2-S3 the undo rejection is unsound | **Accepted.** "Clear this rule's categories" is a preview plus a token, with no table | 4.4, 4.5, 5.2, 6 item 3 |
| R2-S4 C3b and A1 share files | **Accepted.** A1 no longer edits `ledger_service.py` or its tests | 5.0, 7.1, 7.6 |
| R2-S5 small contradictions | **All accepted:** the joint-visibility wording, the service-only claim residual, `_defer_until` on the re-push | 5.0, 4.3 |
| Earlier-blocker note: the re-check must be a separate statement | **Accepted** | 4.2 |
| Recurring before transfers | **Accepted as a line in ADR 0028** (the all-transfers rule). The order is kept; Keel may swap it (7.1) | 2.5, 7.1, ADR 0028 |
| Shortest path to one working screen | **Accepted.** C3c → C6a → W1 → live redeploy comes first. C6a doesn't depend on A1, and the shared files and their split are named | 7.1 |

### 11.6 Bulwark, round 3 (review of `cc7949d`)
| Item | Response | Where |
|---|---|---|
| Must-fix 1, the run-time budget | **Accepted as written.** Point 8's bound sentences are replaced, and point 9 ("Run-time budget, fail closed") is added. The 503 `rules_too_slow` code, the `rule_notice` field and the C3e tests are in. Re-measured, with the C3d probe additions (adjacent literal runs, no literal cap), Bulwark's four new evaders are also rejected at save: 111, 78, 65 and 79 µs worst family. The budget stays the hard stop regardless, because a trigger can be any sub-pattern | ADR 0024 points 8–9; 2.2; 4.4; 5.2; 7.3 C3d, C3e |
| Must-fix 2, the lock mode | **Accepted as written:** `FOR NO KEY UPDATE`, everywhere A1 locked `FOR UPDATE` | 4 (A1 row), 4.2, 5.0, 7.3 A1, ADR 0027 point 7 |
| Ruling, `transfer_link_guard` | **Recorded:** a full column comparison, `job` → `person` only, `search_path` pinned. The residual is recorded: the zero-row silent UPDATE, rare and reversible | 3.3, ADR 0027 point 2 |
| Slice notes | **All accepted:** C3d corpus and families; C4d step 0(d) only on `person` links, `job`-link declines under (c), decline needs an `open` offer; `clear_rule_categories` filters `superseded_by_id`; the shared limiter; the C4c guard test | 4.1, 4.4, 4.5, 5.2, 5.3, 7.3 |
| Cross-contract, PHASE-2 §3.3 round 3 | **Applied, items 1–11 of the supersede-clause note.** The trigger returns in M3 with both branches, and M4 moves members in both. Revision 3's statement that un-adopt's reverse must run household-wide from C4c on is **withdrawn**. The household-wide trigger moves the derived rows whoever calls, and `unsupersede_transaction` clears the winner's transfer state before `remove_external`, with `409 unadopt_blocked` otherwise. Phase 4 adds one thing to the Phase 2 text: the trigger also deletes `transfer_leg_offer` rows, a Phase 4 table | D15, 3, 3.3, 3.4, 4, 4.4, 4.5, 7.1, 7.3, 7.6, 11 |

### 11.7 Cross-contract follow-ups for `PHASE-2-contract.md` (Keel edits PHASE-2)
1. **`post_pending` carries `category_rule_id`** with the category, as the supersede verbs do. C3b
   builds it (4.4, 7.3 C3b). PHASE-2 §3.3 should say so (Gadfly R3-B5).
2. **PHASE-2 §3.3 "Trigger effects" becomes informative**, and PHASE-4 §3.3
   (`transaction_supersede_repoint`) is normative (Bulwark, on #62).
3. **The trigger also deletes `transfer_leg_offer` rows**, a Phase 4 table, in both branches.
   PHASE-2's informative text can say so.
4. **The shared orders live in PHASE-2 §4.** This contract cites §4 for them and §3.3 only for the
   verbs.

### 11.8 Gadfly, round 3 (`reviews/PHASE-4-contract-gadfly.md`, "Round 3")
| Item | Response | Where |
|---|---|---|
| R3-B1 the upgrade record isn't committed | **Accepted.** `docs/planning/SPRINTS.md` "Checkpoint 2026-11-21 evening" is added in this PR. It holds the owner's words and date, both dumps (by name, in the private backups directory), `alembic current` after each upgrade, row-count parity, the tailnet checks and the idempotent re-run. 7.5 and the 7.1 standing rule cite it | 7.1, 7.5, SPRINTS |
| R3-B2 the shortest path needs a migration | **Accepted, both parts.** (a) C6a computes `can_edit` inline, and C3b switches it to the helper with an equivalence test. (b) Every redeploy compares `alembic current` with `main`'s head and rehearses every pending revision from any lane. "No migration" is gone from wave 2 | 5.1, 7.1, 7.3 C3b, 7.5 step 1 |
| R3-B3 deleting a rule orphans its categories | **Accepted.** `409 rule_has_categories`, "Clear N and delete" in one transaction, and an explicit `keep_categories=true`. The clear copy is corrected | 4.4, 5.2, 6 item 3, 7.3 C3e |
| R3-B4 a claim strands the other person's runs | **Accepted.** A claim is a 409 while another person has a `pending` or `needs_review` run on the account. The A1 test reads "each person accepts their own run" | 5.0, 7.3 A1 |
| R3-B5 gaps between the contracts | **Accepted.** `post_pending` carries `category_rule_id` (C3b, with a test, and listed for PHASE-2 in 11.7). The shared order is cited as PHASE-2 §4. P4a is in wave 1 ahead of C3b | 3, 4.4, 7.1, 7.3 C3b, 11.7 |

### 11.9 Gadfly, round 4 (`reviews/PHASE-4-contract-gadfly.md`, "Round 4"), and Bulwark round 5
| Item | Response | Where |
|---|---|---|
| R4-B1 un-adopt revives a cleared category | **Accepted.** Clear-and-delete also resets superseded rows that carry the rule's id: one more UPDATE, no count check, outside N, audited under `rule.cleared`. Test: supersede, clear-and-delete, unsupersede, and the old row is uncategorised | 4.4, 5.2, 7.3 C3e |
| Upgrade 2's dump name | **Filled in:** `holdfast-pre-0010-20261121T224905.dump` | SPRINTS |
| The claim lock residual | **Recorded:** an upload that starts after the 409 check can still be stranded. It's rare, and making the account joint again recovers it | 5.0 |
| Bulwark round 5 slice notes | **Pasted into the briefs:** C6a's `can_edit` predicate; C3b's equivalence cases; C3e's delete order and author-session count; A1's fixed message and the documents-slice condition | 5.0, 7.3 |
