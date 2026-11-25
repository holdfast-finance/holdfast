# Phase 5 contract: budgets, metrics, forecast and alerts (with the Dashboard and the PWA shell)

Sources (Section 20 wins where they conflict):
- PLAN 5, 8.5–8.8, 11, 11.1 and 13 (Phase 5), and Section 20 (20.2 differentiator 5, 20.3)
- ROADMAP E5 (B1–B6) and the round-table rulings (`docs/planning/roundtable/04`, `05`, `08` C2)
- ADRs 0007, 0008, 0011, 0016, 0022, 0026–0029
- the Phase 5 ADRs, written with this contract (status Proposed until it merges):
  - **0039**: metrics read through security-invoker views, not materialized views
  - **0040**: forecast f1 and the honest backtest
  - **0041**: alerts are evaluated per person, email only, with minimal content

This file is binding for every Phase 5 story. A builder who must deviate says so in the PR body
and asks Lattice for an amendment. The amendment lands here before the code merges. Anything
marked **OQ-n** is an open question for Keel (section 11). Each one has a stated default, so no
story is blocked on it.

**Revision 1** (2026-11-24): first draft, unblocking B1, B2 and B4.

**Status of what Phase 5 builds on, at `origin/main` `2eae764`:**
- Merged: the `budget` and `budget_line` tables (Phase 1, no writer); `balance_snapshot`,
  `liability`, `import_run`; splits with the deferred balance trigger; `category` with `kind`,
  `counts_toward_spending` and `is_essential`; `app/domain/money.py`;
  `app/domain/recurring{,_series}.py`; F2 jobs.
- Not merged yet, and Phase 5 depends on them:
  - **C3b** (M1: `app_account_details_visible`, `app_account_writable`)
  - **C4c/C4d** (`transfer_link`, the `ledger.refresh` job)
  - **C5b/C5d** (`recurring_series`, `recurring_member`, the recurring pool, `GET /recurring`)
  - **P5b** (the Plaid sync job, for the B6 sync-cycle test)

---

## 0. Phase 5 acceptance (every criterion can fail)

Sounding runs these at B6 on synthetic data and the Plaid sandbox fixtures (Q1 finish line, ROADMAP).
Each result goes into `docs/phases/PHASE-5.md` with exact commands and counts.

1. **Budget within one sync cycle.**
   - With a `FakeConnector` sync run (P5b), insert one transaction in a budgeted category on a
     visible account. The sync commits.
   - The next `GET /budgets/{id}/status` must show that line's `actual` changed by exactly
     `-amount` (Decimal equality), with no other job run in between.
   - After the chained `ledger.refresh` → `insights.refresh`, a `budget_threshold` event exists when
     the line crosses `alert_pct`.
   - The same check is repeated with a statement upload.
2. **Forecast beats the baseline, and the bands are honest.** Run `forecast.backtest` over 12
   monthly origins on the B6 synthetic forecast world (§7.4).
   - It must give `wape < baseline_wape` at both 30 and 90 days.
   - It must give `0.70 ≤ coverage ≤ 0.90` at both horizons.
   - The Forecast page shows WAPE, P10–P90 coverage, floored MAPE and the baseline WAPE. The e2e
     checks that the text is present. This is how the PLAN Phase 5 line "MAPE is displayed" is met
     honestly.
3. **Alerts fire in tests.** For each of the nine kinds (§5.1):
   - one test fires it exactly once
   - a second evaluation fires nothing (dedupe or cooldown)
   - with email on, the recording transport receives one email whose body matches none of these:
     an amount pattern, a merchant name, an account name, a mask

   A large charge on Alex's `private` account never creates an event for Sam.
4. **Views respect RLS and `summary_only`.** The parametrised matrix (§3.6) passes on every `v_*`
   view:
   - household A vs B
   - `private` / `shared` / `summary_only` / joint
   - unset context gives zero rows

   For a `summary_only` reader:
   - net worth includes the account's anchored balance
   - cash flow, category spend and budgets exclude its transactions
   - `hidden_detail_accounts` counts it
5. **Money invariants hold (property tests).** Over random ledgers:
   - no transfer leg or `transfer`/`excluded`-kind line is in any metric
   - a split parent counts exactly once, through its splits
   - two currencies never share a sum (two groups, or `CurrencyMismatchError`)
   - the float lint is clean on every new module
6. **The PWA shell is installable and caches no data.** Against a production build (`vite build`
   + `vite preview`):
   - CDP `Page.getInstallabilityErrors` returns `[]` (Lighthouse 12 dropped its PWA audit, OQ-14)
   - after visiting every Phase 5 page, `caches` holds no request whose path starts with `/api`
   - offline, a reload shows the shell, and API calls fail visibly
   - the config test asserts there is no `/api` route (§8.1)
7. **Every screen works against the real API.** Each B5 page has one Playwright test. It drives the
   real backend on a seeded throwaway stack (its own compose project and ports, never the dev
   stack's), with MSW off. The Dashboard runs at 375 px with no horizontal scroll.

A non-gating real-data report is optional. If Keel runs one, it goes on the owner's instance
(ADR 0019): backtest WAPE and coverage, plus counts of budgets, alerts and views, as numbers only.

---

## 1. Decisions at a glance

| # | Question | Decision | Record |
|---|---|---|---|
| D1 | Matviews | **None.** Plain `v_*` views `WITH (security_invoker = true)`, computed when read. No view-refresh job. A rollup *table* is the specified fallback if the benchmark fails (B2r, not scheduled) | ADR 0039 |
| D2 | Where the money rules live | `v_ledger_lines` only: `COALESCE(split, parent)`, superseded rows out, `is_transfer` and `transfer`/`excluded` kinds out, and the classification into `income`/`spending`/`saving` | ADR 0039, 0022 |
| D3 | Pending rows | Counted in actuals (budgets, cash flow), and exposed as `is_pending`. Excluded from balances' roll-forward and from forecast and backtest flows | here |
| D4 | Hidden accounts (`is_hidden`) | Excluded from every Phase 5 metric (OQ-3) | here |
| D5 | Balances | Anchors (`balance_snapshot.current`, plus closing balances of reconciled or accepted statements) plus roll-forward of live posted rows | ADR 0039 |
| D6 | Budget periods | `monthly` = calendar month, `annual` = calendar year, `biweekly` = 14-day blocks anchored at `budget.start_date`. Period math is in `app/domain/budgets.py` | here |
| D7 | Rollover | Envelope only (CHECK). Both signs carry: an overspent envelope starts the next period short (OQ-4). Linear: `available(P) = Σ_{k≤P} planned_k − Σ_{k<P} actual_k` | here |
| D8 | Planned amounts over time | `budget_line_amount(effective_from, amount)` versions. Past periods are immutable, so rollover never rewrites history | here |
| D9 | Zero-based | Not a DB constraint. Status reports `to_assign = Σ income planned − Σ spending planned − Σ saving planned`. `0` is balanced | here |
| D10 | Budgets per household | Any number of budgets, **at most one active** (partial unique). Any member edits. Each reader sees actuals from the rows they can see (OQ-2) | here |
| D11 | Forecast target | Liquid position (`depository` + `credit`) per currency, total only (OQ-9). Computed on request. Only the backtest is stored | ADR 0040 |
| D12 | Forecast bands | Empirical quantiles of the rolling-origin cumulative residual error. No ETS | ADR 0040 |
| D13 | Alerts | Evaluated per person in that person's session. In-app always, email opt-in. **No ntfy** (needs an ADR, OQ-7) | ADR 0041 |
| D14 | Email content | `minimal` by default (count plus link). `full` needs a fresh re-auth | ADR 0041 |
| D15 | Time zone | `as_of` = the UTC date, and cron runs in UTC (OQ-5) | here |
| D16 | PWA | `vite-plugin-pwa` `generateSW`, precaching the app shell only. `runtimeCaching` is empty. `navigateFallbackDenylist` covers `/api`. Disabled in dev (MSW owns the dev SW) | here |
| D17 | Cut from Phase 5 (ROADMAP "Cut from Q1") | What-if, irregular-income mode (OQ-6), retirement Monte Carlo, debt payoff timeline, z-score outliers, `v_budget_status` and `v_recurring` for the agent (Phase 8), credit alerts (Phase 6), agent findings on the Dashboard | ROADMAP |

---

## 2. Cross-cutting invariants (every slice; reviewers check these)

1. **Decimal everywhere.**
   - `Decimal` in Python, constructed from `str`.
   - `NUMERIC(14,2)` for money, `NUMERIC(5,4)` for ratios in [0, 1], `NUMERIC(9,4)` for unbounded
     ratios (WAPE).
   - Strings in JSON (`MoneyStr`, `DecimalStr`), and `decimal.js` in TS.
   - Quantize with `ROUND_HALF_EVEN` only at the API edge: money to cents, ratios to 4 places,
     runway months to 1 place. No `float` anywhere, tests included.
2. **`COALESCE(splits, parent)`.** A transaction with splits counts once per split, with the split's
   amount and category. Without splits it counts once with its own. Only `v_ledger_lines` does this
   (D2). Totals that ignore categories (balances, forecast flows) may use the parent amount, because
   splits sum exactly to it (deferred trigger, ADR 0008).
3. **Transfers are out of every income, spending and saving metric.** That covers both
   `transaction.is_transfer` and categories of kind `transfer` or `excluded` (ADR 0022 rule 1).
   - Balances include transfers, because money really moved.
   - The forecast drops only liquid-to-liquid legs (ADR 0040).
4. **Superseded rows never count** (`superseded_by_id IS NULL` everywhere).
5. **Never sum across currencies.**
   - Every view groups by `currency` (taken from the account).
   - Every service result is for one currency, and carries `currency` and `currencies`.
   - Python sums go through `app.domain.money.sum_money`, or assert a single currency, and raise
     `CurrencyMismatchError` otherwise.
6. **Sign convention.**
   - Outflow negative, inflow positive.
   - A budget line's `actual` is reported as a positive magnitude for spending and saving lines
     (`-Σ amount`), and as `Σ amount` for income lines. Refunds reduce spending.
   - `balance_snapshot.current` is negative for debt. The debt API reports `owed` as a positive
     magnitude.
7. **Visibility is defined once.** The base-table RLS (ADR 0008), plus the M1 helpers for new
   per-person tables (ADR 0026). Views are security-invoker. No code path sets
   `household_wide=True` in Phase 5: the jobs run per person (`scoped_session(household, person)`).
8. **Layering (ADR 0011).**
   - Routers stay thin.
   - Services own sessions, audit and every write.
   - `app/domain/{budgets,metrics,forecast,alerts}.py` are pure: no I/O, no clock (`as_of` is an
     argument), deterministic, and independent of input order.
   - Each service exposes a **session-taking core** (`budget_status(session, …)`,
     `metrics_summary(session, …)`, `forecast_for(session, …)`). The request wrapper and the
     `insights.refresh` job both call it, so the job and the API can't disagree.
9. **Audit.** Every write is audited with ids and changed field names only, never amounts,
   names, merchants or thresholds (Bulwark 12 in Phase 4). The audited actions:
   - `budget.created`, `budget.updated {fields}`, `budget.line_set {line_id}`,
     `budget.line_archived {line_id}`
   - `alert.rule_updated {kind, fields}`, `alert.email_updated {fields}`, `alert.email_sent {count}`
   - `forecast.backtest_requested`
10. **API conventions (Phase 4 §5).**
    - Everything is under `/api`, as JSON.
    - Errors are `{"error": {"code", "message"}}`.
    - Every non-GET sends `X-Holdfast-CSRF: 1`.
    - Dates are ISO.
    - Schema names are the Pydantic class names.
    - Run `make api-types` in the same PR.

---

## 3. Schema: migrations and token order

### 3.0 Token order and conventions
The shared order continues the Phase 2 and Phase 4 orders (PHASE-2 §4, PHASE-4 §3):

**… → M3 `<n>_transfer_corrections` (C4c) → M4 `<n>_recurring_series` (C5b) → M5
`<n>_budgets_v2` (B1b) → M6 `<n>_metric_views` (B2b) → M7 `<n>_forecast_backtest` (B3b) → M8
`<n>_alerts` (B4b).**

- The rules are as Phase 4:
  - the holder numbers the migration from `main`'s head at build time
  - the slug is fixed
  - the Phase 4 skip rule applies
- M5 and M6 depend on no Phase 4 table. If C5b slips by more than one window, Keel may let M5 and
  M6 take the token ahead of M4 under the skip rule (OQ-16).
- M8 uses the M1 helper, so C3b must have merged before it.
- Conventions (Phase 1 contract and PHASE-4 §3.0) hold:
  - UUID PKs, `timestamptz`, text + CHECK enums
  - `household_id NOT NULL` + index + ENABLE/FORCE RLS + `household_isolation`
  - composite FKs `(x_id, household_id)`, explicit `ON DELETE`
  - RESTRICTIVE per-person policies
  - up/down/up as `holdfast_owner`
  - the exact privilege set tested per table
- **Views.** Each one is:
  - `CREATE VIEW public.v_<name> WITH (security_invoker = true) AS …`, with schema-qualified
    relations
  - `REVOKE ALL ON public.v_<name> FROM PUBLIC; REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON
    public.v_<name> FROM holdfast_app; GRANT SELECT ON public.v_<name> TO holdfast_app;`

  The downgrade drops views in reverse dependency order.
- **Empty-table guard** (PHASE-4 §3.1 pattern). M5 changes `budget`/`budget_line`, which have no
  writer yet. Upgrade and downgrade both count rows across every household between `NO FORCE ROW
  LEVEL SECURITY` and `FORCE` in the same transaction, and raise if any exist.

### 3.1 M5 `<n>_budgets_v2` (B1b)

**`budget`** (exists; changes):
| Column | Change |
|---|---|
| `currency` | add `text NOT NULL`, CHECK `currency ~ '^[A-Z]{3}$'`. No default: the service supplies it (§6.1) |
| `name` | add CHECK 1–80 chars |
| `rollover` | add CHECK `rollover = false OR method = 'envelope'` |
| `start_date` | add CHECK `period <> 'monthly' OR extract(day FROM start_date) = 1`, and CHECK `period <> 'annual' OR (extract(month FROM start_date) = 1 AND extract(day FROM start_date) = 1)` |
| `updated_at` | add `timestamptz NOT NULL DEFAULT now()` |
| index | `CREATE UNIQUE INDEX uq_budget_one_active ON budget (household_id) WHERE active` |

`period`, `method` and `start_date` are fixed after creation. The service refuses changes, and a
`budget_guard` trigger (BEFORE UPDATE OF those columns) raises `23514`.

**`budget_line`** (exists; changes):
- drop `amount`, which moves to the versions table
- add `archived_from date NULL`: the line has no planned amount from that period on
- add `created_at`
- keep `UNIQUE (budget_id, category_id)` and `alert_pct` (Ratio, default 0.8)
- add `UNIQUE (id, household_id)` (the parent key)

**`budget_line_amount`** (new):
| Column | Type | Notes |
|---|---|---|
| `household_id` | uuid NOT NULL | standard |
| `budget_line_id` | uuid NOT NULL | composite FK → `budget_line(id, household_id)` ON DELETE CASCADE |
| `effective_from` | date NOT NULL | a period start (the service enforces it) |
| `amount` | NUMERIC(14,2) NOT NULL | CHECK `>= 0`. A planned magnitude whatever the kind (existing `budget_line` comment) |
| `created_at` | timestamptz | standard |

- PK `(budget_line_id, effective_from)`; index `household_id`.
- Policy: `household_isolation` only. Budgets are household data (ADR 0008 table). Per-person
  budgets are OQ-2.

**`v_ledger_lines`** (the money rules; D2). Normative SQL:
```sql
CREATE VIEW public.v_ledger_lines WITH (security_invoker = true) AS
SELECT * FROM (
  SELECT t.household_id, t.account_id, a.currency, t.id AS transaction_id, s.id AS split_id,
         t.posted_date, t.is_pending,
         c.id AS category_id, c.parent_id AS category_parent_id, c.kind AS category_kind,
         COALESCE(c.is_essential, false) AS is_essential,
         CASE
           WHEN t.is_transfer OR c.kind IN ('transfer', 'excluded') THEN 'excluded'
           WHEN c.id IS NULL OR c.slug = 'uncategorized.needs-review'
             THEN CASE WHEN COALESCE(s.amount, t.amount) < 0 THEN 'spending' ELSE 'income' END
           WHEN c.kind = 'income' THEN 'income'
           WHEN c.kind = 'savings' THEN 'saving'
           WHEN c.kind = 'expense' AND c.counts_toward_spending THEN 'spending'
           ELSE 'excluded'
         END AS bucket,
         (c.id IS NOT NULL AND c.slug <> 'uncategorized.needs-review') AS reviewed,
         COALESCE(s.amount, t.amount) AS amount
  FROM public."transaction" t
  JOIN public.account a ON a.id = t.account_id AND a.household_id = t.household_id
  LEFT JOIN public.transaction_split s ON s.transaction_id = t.id
  LEFT JOIN public.category c
         ON c.id = CASE WHEN s.id IS NULL THEN t.category_id ELSE s.category_id END
  WHERE t.superseded_by_id IS NULL AND NOT a.is_hidden
) l
WHERE l.bucket <> 'excluded';
```
- An uncategorised split (category NULL) is classified by sign. It never falls back to the
  parent's category.
- `bucket` is one of `income`, `spending`, `saving`. Excluded lines never leave the view, so no
  consumer can count a transfer.

### 3.2 M6 `<n>_metric_views` (B2b)
All views are security-invoker. Semantics are normative. The SQL form is the builder's choice, and
tests pin the results.

| View | Columns | Semantics |
|---|---|---|
| `v_balance_anchors` | `household_id, account_id, as_of_date, balance, source ('snapshot'\|'statement')` | `balance_snapshot` rows with `current IS NOT NULL`, plus `import_run` rows with `account_id`, `period_end` and `closing_balance` set and `reconcile_status IN ('reconciled','accepted')`. One row per `(account_id, as_of_date)`: the snapshot wins, then the newest run (`created_at DESC`). Signs as `balance_snapshot.current` (`import_run` uses the same convention) |
| `v_account_balance_daily` | `household_id, account_id, currency, account_type, day, balance, anchor_date` | Only for accounts with ≥ 1 anchor and `NOT is_hidden`. Days run from the earlier of the first anchor and the first live posted row, to `LEAST(closed_at, current_date)`. `balance(d) = A.balance + S(d) − S(A.as_of_date)`. `A` is the latest anchor ≤ d, or, if none, the earliest anchor > d. `S(x)` is the running sum of live (`superseded_by_id IS NULL`), posted (`NOT is_pending`) transaction amounts on the account up to day x, transfers included, under the reader's RLS. A `summary_only` reader gets S ≡ 0, which gives an anchor step function |
| `v_net_worth_daily` | `household_id, currency, day, assets, liabilities, net` | From `v_account_balance_daily`. `assets = Σ balance` for types `depository`, `investment`, `other`. `liabilities = −Σ balance` for `credit`, `loan`, as a magnitude. `net = assets − liabilities`. Grouped by currency |
| `v_monthly_cashflow` | `household_id, currency, month (date, 1st), income, spending, saving, net, unreviewed_lines, has_pending` | From `v_ledger_lines`. `income = Σ amount` (bucket income). `spending = −Σ amount` (bucket spending). `saving = −Σ amount` (bucket saving). `net = Σ amount` over all three buckets (PLAN 8.6 net cash flow). Sums `COALESCE(…, 0)` |
| `v_category_spend` | `household_id, currency, month, category_id (NULL = uncategorised), bucket, amount, lines` | From `v_ledger_lines`, grouped. `amount = Σ amount`, signed |

`v_ledger_lines` (M5) and these five are the Phase 5 agent views. `backend/app/agent/tools.py`
gains `AGENT_VIEW_WHITELIST: frozenset[str]` holding exactly these six names, each with a one-line
doc comment, and nothing else (B2b). Pilot extends it in Phase 8.

**View lint** (B2b, `backend/tests/models/test_view_lint.py`). For every relation in `public`
named `v_%`:
- `relkind = 'v'`
- `reloptions` contains `security_invoker=true`
- `holdfast_app` holds exactly SELECT
- the name is in `AGENT_VIEW_WHITELIST`

It also fails when a whitelisted name has no view. The existing RLS lint (`relkind = 'r'`) is
unchanged.

### 3.3 M7 `<n>_forecast_backtest` (B3b)
**`forecast_backtest`**
| Column | Type | Notes |
|---|---|---|
| `id`, `household_id`, `created_at` | | standard |
| `person_id` | uuid NOT NULL | composite FK → `person(id, household_id)` ON DELETE CASCADE |
| `currency` | text NOT NULL | CHECK `^[A-Z]{3}$` |
| `horizon_days` | smallint NOT NULL | CHECK IN (30, 90) |
| `origins`, `weeks` | smallint NOT NULL | CHECK `>= 1` |
| `first_origin`, `last_origin` | date NOT NULL | CHECK `first_origin <= last_origin` |
| `wape` | NUMERIC(9,4) NOT NULL | CHECK `>= 0` |
| `mape_floored` | NUMERIC(9,4) NULL | CHECK `>= 0`. NULL when no week passes the floor |
| `coverage` | NUMERIC(5,4) NOT NULL | CHECK 0–1 |
| `baseline_wape` | NUMERIC(9,4) NULL | CHECK `>= 0`. NULL when the baseline is undefined |
| `model_version` | text NOT NULL | CHECK 1–16 chars (`f1`) |
| `job_run_id` | uuid NULL | composite FK → `job_run` ON DELETE SET NULL (job_run_id) |

- Index `(person_id, currency, horizon_days, created_at DESC)`.
- RESTRICTIVE FOR ALL: `person_id = public.app_person_id() OR public.app_household_wide()`. This
  is the same scoping as `agent_finding` (ADR 0008).
- No UPDATE grant. The job inserts, and deletes rows beyond the newest 24 per `(person_id,
  currency, horizon_days)`.

### 3.4 M8 `<n>_alerts` (B4b)
**`alert_rule`** (one row per person and kind; a missing row means the default in
`app/domain/alerts.py`)
| Column | Type | Notes |
|---|---|---|
| `id`, `household_id`, `updated_at` | | standard |
| `person_id` | uuid NOT NULL | composite FK → `person` CASCADE |
| `kind` | text NOT NULL | CHECK IN the nine kinds (§5.1) |
| `enabled` | boolean NOT NULL | |
| `params` | jsonb NOT NULL DEFAULT '{}' | CHECK `jsonb_typeof(params) = 'object'`. Values are strings (the service validates them per kind) |
| `cooldown_hours` | integer NULL | CHECK 1–720. NULL means the kind's default |

`UNIQUE (person_id, kind)`. RESTRICTIVE FOR ALL: `person_id = public.app_person_id() OR
public.app_household_wide()`.

**`alert_preference`**
| Column | Type | Notes |
|---|---|---|
| `person_id` | uuid PK | composite FK `(person_id, household_id)` → `person` CASCADE |
| `household_id` | uuid NOT NULL | standard |
| `email_enabled` | boolean NOT NULL DEFAULT false | |
| `email_detail` | text NOT NULL DEFAULT 'minimal' | CHECK IN ('minimal','full') |
| `last_evaluated_at` | timestamptz NULL | watermark for transaction-level kinds (ADR 0041 point 5) |
| `updated_at` | timestamptz | |

The policy is the same as `alert_rule`.

**`alert_event`**
| Column | Type | Notes |
|---|---|---|
| `id`, `household_id` | | standard |
| `person_id` | uuid NOT NULL | composite FK → `person` CASCADE |
| `kind` | text NOT NULL | CHECK IN the nine kinds |
| `dedupe_key` | text NOT NULL | CHECK 1–200, `^[a-z_]+:[A-Za-z0-9:._-]+$`. Ids and dates only, never amounts or names |
| `account_id` | uuid NULL | composite FK `(account_id, household_id)` → `account` CASCADE |
| `severity` | text NOT NULL | CHECK IN ('info','warning','critical') |
| `title` | text NOT NULL | CHECK 1–200 |
| `facts` | jsonb NOT NULL DEFAULT '{}' | object of string values (money as strings) |
| `fired_at` | timestamptz NOT NULL DEFAULT now() | |
| `read_at` | timestamptz NULL | |
| `email_state` | text NOT NULL DEFAULT 'not_sent' | CHECK IN ('not_sent','pending','sent','failed','skipped') |
| `email_attempts` | smallint NOT NULL DEFAULT 0 | CHECK 0–3 |
| `job_run_id` | uuid NULL | composite FK → `job_run` ON DELETE SET NULL (job_run_id) |

- Indexes: `(person_id, fired_at DESC, id DESC)`, `(person_id, kind, dedupe_key, fired_at DESC)`,
  and a partial index on `(person_id)` WHERE `read_at IS NULL`.
- Policies (RESTRICTIVE):
  - SELECT and UPDATE USING: `(person_id = public.app_person_id() OR public.app_household_wide())
    AND (account_id IS NULL OR public.app_account_details_visible(account_id))`. UPDATE WITH CHECK
    is the same.
  - INSERT WITH CHECK: the same predicate.
  - DELETE: `public.app_household_wide()` only.
- Trigger `alert_event_guard` (BEFORE UPDATE; PHASE-4 §3.0 trigger rules): unless household-wide,
  any change to a column other than `read_at`, `email_state` and `email_attempts` raises `42501`.

### 3.5 Models
- `backend/app/models/budgets.py`: `Budget`, `BudgetLine` and `BudgetLineAmount` are updated.
- New model files:
  - `backend/app/models/forecast.py` (`ForecastBacktest`)
  - `backend/app/models/alerts.py` (`AlertRule`, `AlertPreference`, `AlertEvent`)
- Enum tuples go in `_common.py`: `ALERT_KINDS`, `ALERT_SEVERITIES`, `EMAIL_STATES`,
  `EMAIL_DETAILS`.
- Views get no ORM models. Services query them with `sqlalchemy.text` or `table()` constructs
  (`app/services/views.py`, a small typed helper owned by B1b).

### 3.6 Tests every schema slice adds
- An RLS matrix as `holdfast_app`. It is parametrised over:
  - household A vs B
  - person 1 vs person 2
  - `private`, `shared`, `summary_only` and joint accounts
  - an unset context (zero rows)
- Guessed-id composite FK tests: another household's line, person, account or job run.
- The exact privilege set per table and view.
- Up/down/up, including the M5 empty-table guard raising on a non-empty table.
- Trigger tests (`budget_guard`, `alert_event_guard`) with RLS lifted in a rolled-back transaction.
- M5: `v_ledger_lines` against a hand-built fixture covering:
  - a split parent (counted once, through its splits)
  - an uncategorised split
  - a transfer pair
  - a row in a `transfer`-kind category
  - a `needs-review` inflow and outflow
  - a superseded row
  - a hidden account
  - a pending row
- M6: two currencies; a statement-only account; an account with a snapshot and a statement on the
  same day (the snapshot wins); a balance before the first anchor (back-rolled); a closed account.

---

## 4. Pure domain (no database; property-tested; Codex-suitable)

Rules for all four modules:
- `from __future__ import annotations`; frozen `slots` dataclasses; `Decimal` only
- `as_of` is passed in, never read from a clock
- no imports from `app.services`, `app.models` or `app.db` (import-linter)
- constants are module-level and named, with the PLAN source in a comment

### 4.1 `app/domain/budgets.py` (B1a)
```python
PeriodKind = Literal["monthly", "biweekly", "annual"]
Method = Literal["category", "envelope", "zero_based"]
LineKind = Literal["spending", "income", "saving"]          # from the line category's kind
PaceState = Literal["on_track", "over_pace", "over", "not_applicable"]
PACE_ALERT_FACTOR = Decimal("1.10")                         # PLAN 8.5

@dataclass(frozen=True, slots=True)
class BudgetPeriod:
    start: date
    end: date                                               # inclusive
    @property
    def days(self) -> int: ...

def period_containing(kind: PeriodKind, anchor: date, day: date) -> BudgetPeriod
    # monthly: calendar month; annual: calendar year; biweekly: [anchor + 14k, anchor + 14k + 13].
    # day < anchor -> ValueError (the API maps it to 422).
def next_period(kind: PeriodKind, anchor: date, p: BudgetPeriod) -> BudgetPeriod
def periods_between(kind: PeriodKind, anchor: date, first: date, last: date) -> tuple[BudgetPeriod, ...]

@dataclass(frozen=True, slots=True)
class AmountVersion:
    effective_from: date
    amount: Decimal                                         # >= 0

@dataclass(frozen=True, slots=True)
class LineSpec:
    line_id: UUID
    category_id: UUID
    kind: LineKind
    alert_pct: Decimal                                      # (0, 1]
    versions: tuple[AmountVersion, ...]                     # any order; the function sorts
    archived_from: date | None

def planned_for(line: LineSpec, p: BudgetPeriod) -> Decimal | None
    # The version with the greatest effective_from <= p.start. None before the first version or
    # from archived_from on.

def line_for_category(lines_by_category: Mapping[UUID, UUID], parents: Mapping[UUID, UUID | None],
                      category_id: UUID | None) -> UUID | None
    # The category's own line wins, then its parent group's line, else None (unbudgeted).

@dataclass(frozen=True, slots=True)
class ActualRow:                                            # one v_ledger_lines row, one currency
    day: date
    category_id: UUID | None
    bucket: Literal["income", "spending", "saving"]
    amount: Decimal

def actuals(lines: Sequence[LineSpec], parents: Mapping[UUID, UUID | None],
            rows: Iterable[ActualRow], periods: Sequence[BudgetPeriod]
            ) -> tuple[dict[tuple[UUID, date], Decimal], dict[date, Decimal]]
    # Returns ({(line_id, period.start): actual}, {period.start: unbudgeted_spending}).
    # Spending and saving lines: actual = -Σ amount. Income lines: actual = Σ amount.
    # A row whose category has no line (or is NULL) adds -amount to unbudgeted_spending when its
    # bucket is spending, and is otherwise ignored.

@dataclass(frozen=True, slots=True)
class LineStatus:
    line_id: UUID
    category_id: UUID
    kind: LineKind
    planned: Decimal
    carry_in: Decimal
    available: Decimal
    actual: Decimal
    remaining: Decimal
    pct: Decimal | None                                     # actual / available; None if available <= 0
    projected: Decimal | None                               # current period only
    pace: PaceState
    alert_pct: Decimal
    crossed: tuple[Decimal, ...]                            # thresholds reached, e.g. (0.8, 1.0)

def line_status(line: LineSpec, period: BudgetPeriod,
                history: Sequence[tuple[BudgetPeriod, Decimal | None, Decimal]],
                actual: Decimal, as_of: date, rollover: bool) -> LineStatus
    # history: (period, planned, actual) for every earlier period since the budget start.
    # carry_in = Σ (planned − actual) over history where planned is not None, if rollover and the
    #            kind != income; else 0.
    # available = planned + carry_in; remaining = available − actual.
    # days_elapsed = clamp((as_of − start).days + 1, 0, days). For a past period it equals days.
    # projected = actual × days / days_elapsed when 0 < days_elapsed < days, else actual.
    # pace for spending and saving lines:
    #   "over"      if actual > available
    #   "over_pace" if days_elapsed × 2 <= days and projected > available × PACE_ALERT_FACTOR
    #   "on_track"  otherwise
    # pace for income lines: "not_applicable".
    # crossed: alert_pct when pct >= alert_pct; 1 when pct >= 1. Spending and saving lines only.

def to_assign(statuses: Sequence[LineStatus]) -> Decimal
    # Σ planned(income) − Σ planned(spending) − Σ planned(saving). Zero-based balance is to_assign == 0.
```
Property tests:
- rollover is linear: the sum of every period's `remaining` equals `Σ planned − Σ actual`
- periods tile without gaps or overlap
- `planned_for` is monotonic in versions
- order independence
- biweekly anchors

### 4.2 `app/domain/metrics.py` (B2a)
```python
@dataclass(frozen=True, slots=True)
class MonthFlow:
    month: date                    # 1st of the month
    currency: str
    income: Decimal
    spending: Decimal              # positive magnitude
    saving: Decimal                # positive magnitude
    net: Decimal
    unreviewed_lines: int

def trailing_full_months(as_of: date, n: int) -> tuple[date, ...]
    # The n months before as_of's month, oldest first.
def savings_rate(flows: Sequence[MonthFlow]) -> Decimal | None
    # (Σ income − Σ spending) / Σ income; None when Σ income <= 0. One currency, or
    # CurrencyMismatchError. Saving outflows aren't spending, so they count as saved (PLAN 8.6).
    # Payroll-deducted contributions aren't seen (OQ-12).
def runway_months(liquid: Decimal, essential_by_month: Sequence[Decimal]) -> Decimal | None
    # liquid / mean(essential_by_month) over the 3 trailing full months; None when the mean <= 0.

@dataclass(frozen=True, slots=True)
class DebtAccount:
    account_id: UUID
    kind: Literal["credit", "loan"]
    owed: Decimal                  # max(−balance, 0)
    limit: Decimal | None          # credit only, from the latest balance_snapshot.limit_amt
    apr: Decimal | None            # percent (liability.apr); None if unknown or not visible

@dataclass(frozen=True, slots=True)
class DebtSummary:
    total_owed: Decimal
    credit_owed: Decimal
    credit_limit: Decimal
    utilization: Decimal | None              # credit_owed / credit_limit over cards with a limit > 0
    weighted_apr: Decimal | None             # Σ owed×apr / Σ owed over accounts with a known apr and owed > 0
    apr_unknown: int
    per_account_utilization: Mapping[UUID, Decimal | None]

def debt_summary(accounts: Sequence[DebtAccount]) -> DebtSummary
def change_over(series: Sequence[tuple[date, Decimal]], as_of: date, days: int) -> Decimal | None
    # value(as_of) − value(as_of − days); None if either day is missing.
```

### 4.3 `app/domain/forecast.py` (B3a; ADR 0040)
```python
MODEL_VERSION = "f1"
LEVEL_WEEKS = 13
MIN_HISTORY_WEEKS = 26          # below this: status "insufficient_history", P50 only
MIN_ERROR_SAMPLES = 8
MAPE_FLOOR = Decimal("50.00")   # a model constant, not a tax figure; reported with the result
HORIZON_WEEKS = {30: 4, 90: 13, 365: 52}
COVERAGE_BAND = (Decimal("0.70"), Decimal("0.90"))

@dataclass(frozen=True, slots=True)
class ScheduledSeries:
    series_id: UUID
    cadence: Cadence
    next_expected_date: date
    typical_amount: Decimal
    status: Status
    label: str | None

@dataclass(frozen=True, slots=True)
class Occurrence:
    day: date
    amount: Decimal
    series_id: UUID

def project_occurrences(s: ScheduledSeries, origin: date, horizon_days: int) -> tuple[Occurrence, ...]
    # Occurrences in (origin, origin + horizon]. Cadence steps: weekly +7, biweekly +14,
    # semi-monthly +15 (calendar-clamped to the same pair of days), monthly the same day of month
    # (clamped to month end), quarterly +3 months, annual +12 months. A "late" series puts its
    # overdue occurrence on origin + 1. "possibly_cancelled" projects nothing.

def weekly_totals(daily: Mapping[date, Decimal], origin: date, weeks: int) -> tuple[Decimal, ...]
    # Week k covers (origin − 7(k+1), origin − 7k]. Oldest first, zeros where no data.
def residual_level(weekly: Sequence[Decimal]) -> Decimal            # mean of the last LEVEL_WEEKS
def error_quantiles(weekly: Sequence[Decimal], max_weeks: int) -> dict[int, tuple[Decimal, Decimal, Decimal]]
    # For each rolling origin i >= LEVEL_WEEKS and each h <= max_weeks with data:
    # error = Σ weekly[i:i+h] − h × residual_level(weekly[:i]).
    # (q10, q50, q90) by linear interpolation on the sorted errors (Hyndman–Fan type 7).
    # Past the largest H with >= MIN_ERROR_SAMPLES: q_h = q_H × sqrt(h / H), using Decimal sqrt.

@dataclass(frozen=True, slots=True)
class ForecastPoint:
    day: date
    p10: Decimal | None
    p50: Decimal
    p90: Decimal | None

@dataclass(frozen=True, slots=True)
class Forecast:
    status: Literal["ok", "insufficient_history"]
    points: tuple[ForecastPoint, ...]
    occurrences: tuple[Occurrence, ...]

def forecast(origin: date, horizon_days: int, start_balance: Decimal,
             scheduled: Sequence[ScheduledSeries], residual_daily: Mapping[date, Decimal]) -> Forecast
    # p50(d) = start + Σ occurrences <= d + level × (days since origin) / 7 + q50_h,
    # with h = ceil(days / 7). p10 and p90 replace q50 with q10 and q90.

def seasonal_naive(weekly_history: Sequence[Decimal], weeks_ahead: int) -> tuple[Decimal, ...]
    # The value 52 weeks earlier. With fewer than 52 + weeks_ahead weeks: the trailing 4-week mean.

@dataclass(frozen=True, slots=True)
class BacktestCase:
    origin: date
    actual_weekly: tuple[Decimal, ...]
    p50_weekly: tuple[Decimal, ...]
    baseline_weekly: tuple[Decimal, ...] | None
    actual_cum: Decimal
    p10_cum: Decimal | None
    p90_cum: Decimal | None

@dataclass(frozen=True, slots=True)
class BacktestResult:
    horizon_days: int
    origins: int
    weeks: int
    wape: Decimal
    mape_floored: Decimal | None
    coverage: Decimal
    baseline_wape: Decimal | None

def backtest_metrics(horizon_days: int, cases: Sequence[BacktestCase]) -> BacktestResult
    # wape = Σ|a − f| / Σ|a| over all (origin, week) pairs; Σ|a| = 0 -> wape 0 when every error is 0.
    # mape_floored = mean |a − f| / |a| over weeks with |a| >= MAPE_FLOOR.
    # coverage = share of origins with p10_cum <= actual_cum <= p90_cum (cases with no bands are
    # skipped; zero eligible cases -> ValueError).
```
Property tests:
- a world with only recurring items forecasts exactly
- bands are ordered (p10 ≤ p50 ≤ p90)
- quantiles are invariant to input order
- no future data is used: changing data after the origin changes nothing

### 4.4 `app/domain/alerts.py` (B4a; ADR 0041)
```python
AlertKind = Literal["large_transaction", "new_merchant", "duplicate_charge",
                    "recurring_price_increase", "budget_threshold", "budget_pace",
                    "low_balance_forecast", "connection_needs_relink", "statement_needs_review"]
Severity = Literal["info", "warning", "critical"]

@dataclass(frozen=True, slots=True)
class RuleConfig:
    kind: AlertKind
    enabled: bool
    params: Mapping[str, Decimal]
    cooldown: timedelta | None      # None = one-shot per dedupe_key

DEFAULTS: Mapping[AlertKind, RuleConfig]                    # the §5.1 table
def resolve(kind: AlertKind, stored: RuleConfigRow | None) -> RuleConfig
def validate_params(kind: AlertKind, raw: Mapping[str, str]) -> Mapping[str, Decimal]   # ValueError -> 422

@dataclass(frozen=True, slots=True)
class Candidate:
    kind: AlertKind
    dedupe_key: str
    account_id: UUID | None
    severity: Severity
    title: str
    facts: Mapping[str, str]

@dataclass(frozen=True, slots=True)
class TxnFact:
    id: UUID
    account_id: UUID
    posted_date: date
    amount: Decimal
    merchant_key: str | None
    currency: str

def large_transactions(txns: Sequence[TxnFact], threshold: Decimal) -> list[Candidate]
def new_merchants(txns: Sequence[TxnFact], known_keys: frozenset[str], threshold: Decimal) -> list[Candidate]
def duplicate_charges(txns: Sequence[TxnFact], recent: Sequence[TxnFact]) -> list[Candidate]
    # Same account, merchant_key and amount, both outflows, |Δ posted_date| <= 2 days (48 h on
    # dates). Key "duplicate_charge:<lower id>:<higher id>".
def price_increases(series: Sequence[SeriesFact], factor: Decimal) -> list[Candidate]
    # |latest member amount| > |typical_amount| × factor, for tier bill. Key
    # "recurring_price_increase:<series_id>:<member_id>".
def budget_alerts(budget_id: UUID, period: BudgetPeriod, statuses: Sequence[LineStatus],
                  pace_factor: Decimal) -> list[Candidate]
    # Threshold key "budget_threshold:<line_id>:<period.start>:<pct>" for each crossed threshold.
    # Pace key "budget_pace:<line_id>:<period.start>".
def low_balance(currency: str, points: Sequence[ForecastPoint], threshold: Decimal, as_of: date,
                within_days: int = 30) -> Candidate | None
    # Fires when any p10 (or p50 when there are no bands) < threshold within the window. Key
    # "low_balance_forecast:<currency>".
def connection_alerts(conns: Sequence[ConnFact]) -> list[Candidate]      # status needs_relink
def statement_alerts(runs: Sequence[RunFact]) -> list[Candidate]         # reconcile_status needs_review
def due(candidates: Sequence[Candidate], last_fired: Mapping[tuple[AlertKind, str], datetime],
        configs: Mapping[AlertKind, RuleConfig], now: datetime) -> list[Candidate]
    # Drops disabled kinds, one-shot keys already fired, and keys fired within their cooldown.

def email_body(events: Sequence[Candidate], detail: Literal["minimal", "full"], app_url: str) -> tuple[str, str]
    # (subject, text body). minimal: the count and the URL only. full: titles, never facts, masks
    # or account names. A test runs every DEFAULTS kind through both modes.
```
`SeriesFact`, `ConnFact`, `RunFact` and `RuleConfigRow` are small frozen dataclasses of ids,
dates and Decimals, defined in the same module. Titles are built from category or merchant names
only for the in-app view (`full` email reuses them, by choice).

---

## 5. Services, jobs and triggers

### 5.1 Alert kinds (defaults)
| Kind | Default | Params | Dedupe / cooldown | Severity |
|---|---|---|---|---|
| `large_transaction` | off | `threshold` (MoneyStr > 0, required to enable) | one-shot per transaction | warning |
| `new_merchant` | off | `threshold` (required to enable) | one-shot per transaction | info |
| `duplicate_charge` | on | none | one-shot per pair | warning |
| `recurring_price_increase` | on | `factor` default `1.10` (1.01–3.00) | one-shot per (series, member) | info |
| `budget_threshold` | on | uses each line's `alert_pct`, and 100% | one-shot per (line, period, threshold) | warning; critical at 100% |
| `budget_pace` | on | `factor` default `1.10` | one-shot per (line, period) | info |
| `low_balance_forecast` | on | `threshold` default `0.00`, `currency` optional (default: the person's default currency, §6.2) | cooldown 72 h | critical |
| `connection_needs_relink` | on | none | cooldown 24 h per connection | warning |
| `statement_needs_review` | on | none | one-shot per import run | info |

`connection_needs_relink` and `statement_needs_review` go only to people who can see the
connection or run under RLS. Bank connections are household-visible today, so both people get the
first one. Credit kinds wait for Phase 6.

Transaction-level kinds (the first three) consider only live, non-transfer outflows that meet all
of these:
- `created_at` is greater than the person's `last_evaluated_at`
- `posted_date >= as_of − 7 days`
- the person can see the row

On a person's first run, the watermark is set to now, and nothing is evaluated.

### 5.2 Jobs (ADR 0016; IDs-only payloads, the per-household lock)
| Job | Payload | Retry / timeout | Steps |
|---|---|---|---|
| `insights.refresh` | `{household_id}` | `RetryPolicy(max_attempts=3, base=30 s)`, `timeout=120 s` (explicit) | For each person with a login (reuse C4d's helper), in `scoped_session(household, person)`, one transaction each: load facts → `budget_status` for the active budget → `forecast_for(horizon=30)` → candidates → `due` → insert events (`email_state = 'pending'` if `email_enabled` else `'not_sent'`) → advance the watermark → commit. Then, after commit, send one email per person with pending events through `app/notify/email.py`. In a new short transaction, mark `sent` or `failed` (`email_attempts += 1`; `failed` after 3). `skipped` when SMTP isn't configured |
| `forecast.backtest` | `{household_id}` | `RetryPolicy(max_attempts=2, base=60 s)`, `timeout=600 s` | For each person with a login, in their session: load visible liquid flows and recurring inputs. For up to 12 monthly origins (the 1st of each of the last 12 full months, with ≥ `MIN_HISTORY_WEEKS` before them): `detect_rows(rows <= origin)` in the C5b `cpu_pool` (120 s per call, killed on timeout per ADR 0029) → `forecast` → a case. Then `backtest_metrics` for 30 and 90 days, insert 2 rows, and prune to 24 |

Triggers:
- **After `ledger.refresh` succeeds**, its handler enqueues `insights.refresh` with key
  `insights:auto:<the same bucket token>` via `enqueue_household_wide` (B4b edits
  `ledger_refresh.py`: one call at the end).
- **Nightly**: the cron `insights_nightly` (`hour=7, minute=15`, UTC) enqueues `insights.refresh`
  (`insights:nightly:<YYYYMMDD>`) for every household. On day 1 of the month it also enqueues
  `forecast.backtest` (`backtest:monthly:<YYYYMM>`).
  - The fan-out is a new `app.jobs.runner.enqueue_for_all_households(spec, key, payload_factory)`
    in `runner.py`, the module allowed to name `list_household_ids` (ADR 0016).
- **Manual**: `POST /forecast/backtest` → `enqueue(..., requested_by=person)` with key
  `backtest:manual:<YYYYMMDD>`.
- `GET /forecast/backtest` reports `queued` when there are no rows and a run is unfinished.

### 5.3 Email transport (`app/notify/email.py`, B4b)
- `EmailTransport` Protocol: `send(to: str, subject: str, body: str) -> None`.
- `SmtpTransport`:
  - stdlib `smtplib` called through `anyio.to_thread.run_sync`
  - `ssl.create_default_context()`
  - STARTTLS on any port except 465, which uses implicit TLS
  - **no plaintext path**
  - a 15 s timeout
- `RecordingTransport` is for tests. No new dependency.
- New settings in `backend/app/config.py` and `.env.example`:
  - `SMTP_PORT` (default 587)
  - `SMTP_FROM` (required for email; there is no default address)
- `SMTP_HOST` + `SMTP_FROM` set means `smtp_configured`. `SMTP_PASS` stays a `SecretStr` and is
  never logged. Errors are logged by class name only.
- The recipient is the person's login email (`app/services/users.py: login_email(person_id)`, added
  by B4b if missing).
- `ALERT_EMAIL` and `NTFY_*` stay unused (OQ-7, OQ-13).

### 5.4 Service map
| Service (new) | Core functions (session-taking) | Writes |
|---|---|---|
| `services/budgets.py` (B1b) | `budget_status(session, budget_id, day, as_of) -> BudgetStatus`; `budget_history(session, budget_id, periods, as_of)`; `default_currency(session)` | `budget`, `budget_line`, `budget_line_amount` (create, set line, archive line, patch) |
| `services/metrics.py` (B2b) | `metrics_summary(session, currency, months, as_of)`; `net_worth_series(session, currency, start, end, step)`; `category_spend(session, currency, month)`; `hidden_detail_accounts(session) -> int` (`count(*) FROM account WHERE NOT app_account_details_visible(id)`) | none |
| `services/forecast.py` (B3b) | `forecast_for(session, currency, horizon_days, as_of)`; `liquid_daily_flows(session, currency, start, end)` (the ADR 0040 point 2 loader); `latest_backtest(session, currency)` | `forecast_backtest` (job only) |
| `services/alerts.py` (B4b) | `evaluate_person(session, person_id, as_of, now, job_run_id)`; `list_alerts`, `mark_read`, `settings`, `set_rule`, `set_email` | `alert_*` |
| `services/views.py` (B1b) | typed `select()` helpers over the `v_*` views | none |

Liquid flows (B3b, normative):
- rows on visible accounts of type `depository` or `credit` with `NOT is_hidden` and the requested
  currency
- live and posted
- minus transfer legs whose partner is on a liquid account, through `transfer_group_id` (same
  owner) or a `transfer_link` the reader can see (C4c)

Residual = these flows minus the rows in `recurring_member` of the scheduled series. Start balance
= Σ `v_account_balance_daily.balance` on `as_of` over the same accounts. Accounts without an
anchor are skipped and counted in `accounts_without_balance`.

---

## 6. API (all new routes; request and response shapes)

`MoneyStr` is a decimal string with 2 places. `DecimalStr` is a decimal string: ratios have 4
places (0.1234 = 12.34%), runway has 1. Dates are ISO.

### 6.1 Budgets (B1c)
```
BudgetLineIn      {category_id: uuid, amount: MoneyStr (>= 0), alert_pct?: DecimalStr (0 < x <= 1, default "0.8000")}
BudgetCreate      {name: str (1–80), period: "monthly"|"biweekly"|"annual",
                   method: "category"|"envelope"|"zero_based", rollover: bool (envelope only),
                   start_date: date, currency?: str, lines: BudgetLineIn[] (<= 200)}
BudgetPatch       {name?: str, active?: bool, rollover?: bool}
LineSet           {amount: MoneyStr, alert_pct?: DecimalStr, effective: "this_period"|"next_period"}
BudgetLineOut     {id, category_id, category_name, kind: "spending"|"income"|"saving",
                   amount: MoneyStr | null (current period), alert_pct: DecimalStr, archived_from: date | null}
BudgetOut         {id, name, period, method, rollover, start_date, currency, active, lines: BudgetLineOut[]}
BudgetList        {items: BudgetOut[]}
PeriodOut         {start: date, end: date, days: int, days_elapsed: int}
LineStatusOut     {line_id, category_id, category_name, kind, planned: MoneyStr, carry_in: MoneyStr,
                   available: MoneyStr, actual: MoneyStr, remaining: MoneyStr, pct: DecimalStr | null,
                   projected: MoneyStr | null, pace: "on_track"|"over_pace"|"over"|"not_applicable",
                   alert_pct: DecimalStr}
BudgetStatusOut   {budget_id, method, currency, as_of: date, period: PeriodOut,
                   lines: LineStatusOut[] (ordered by kind, then category sort_order),
                   totals: {planned_spending, actual_spending, planned_income, actual_income,
                            planned_saving, actual_saving: MoneyStr},
                   unbudgeted_spending: MoneyStr, to_assign: MoneyStr | null (zero_based only),
                   has_pending: bool, hidden_detail_accounts: int}
BudgetHistoryOut  {budget_id, currency, periods: [{start, end, lines: [{line_id, planned: MoneyStr | null, actual: MoneyStr}]}]}
```
| Method + path | Body → response | Errors |
|---|---|---|
| GET `/budgets` | → `BudgetList` | |
| POST `/budgets` | `BudgetCreate` → 201 `BudgetOut`. The currency defaults to `default_currency` (§6.2). `start_date` is normalised to its period start for `monthly`/`annual`. Each line's first version takes `effective_from` = the period start containing `start_date` | 422 (a line on a `transfer`/`excluded` category, duplicate categories, rollover on a non-envelope budget); 409 `conflict` (another budget is active and `active` is true) |
| PATCH `/budgets/{id}` | `BudgetPatch` → `BudgetOut` | 404; 409 (activating while another is active); 422 |
| PUT `/budgets/{id}/lines/{category_id}` | `LineSet` → `BudgetLineOut`. Upserts the version at the current or next period start, and revives an archived line | 404; 422 |
| DELETE `/budgets/{id}/lines/{category_id}` | → 204. Sets `archived_from` = the next period start (the current period keeps its plan) | 404 |
| GET `/budgets/{id}/status?day=` | → `BudgetStatusOut` for the period containing `day` (default `as_of`) | 404; 422 (`day` before the start) |
| GET `/budgets/{id}/history?periods=6` | → `BudgetHistoryOut` (1–24 periods ending at the current one) | 404; 422 |

No step-up re-auth: budgets are household planning data inside the instance (PHASE-4 §5.6 reasoning).

### 6.2 Metrics (B2b)
`default_currency` is the currency of the most visible non-hidden accounts, with ties broken
alphabetically. Every metrics, forecast and budget-create call takes an optional `currency`, and
422s on a currency the reader has no account in.
```
MonthFlowOut      {month: date, income, spending, saving, net: MoneyStr, unreviewed_lines: int, partial: bool}
DebtAccountOut    {account_id, name, kind: "credit"|"loan", owed: MoneyStr, limit: MoneyStr | null,
                   utilization: DecimalStr | null, apr: DecimalStr | null}
MetricsSummaryOut {as_of, currency, currencies: str[],
                   net_worth: {net, assets, liabilities: MoneyStr | null, change_30d: MoneyStr | null,
                               accounts_included: int, accounts_without_balance: int},
                   cash_flow: MonthFlowOut[] (oldest first, `months` entries, the last partial),
                   savings_rate: {last_full_month: DecimalStr | null, trailing_3m: DecimalStr | null},
                   runway: {months: DecimalStr | null, liquid: MoneyStr, essential_monthly_avg: MoneyStr},
                   debt: {total_owed, credit_owed, credit_limit: MoneyStr, utilization: DecimalStr | null,
                          weighted_apr: DecimalStr | null, apr_unknown: int, accounts: DebtAccountOut[]},
                   hidden_detail_accounts: int}
NetWorthPointOut  {day: date, assets, liabilities, net: MoneyStr}
NetWorthSeriesOut {currency, step: "day"|"week"|"month", points: NetWorthPointOut[] (<= 400)}
CategorySpendItem {category_id: uuid | null, name, parent_id: uuid | null, amount: MoneyStr (spend magnitude),
                   prev_month: MoneyStr, same_month_last_year: MoneyStr}
CategorySpendOut  {currency, month: date, items: CategorySpendItem[], total: MoneyStr}
```
| Method + path | Response |
|---|---|
| GET `/metrics/summary?currency=&months=12` (1–36) | `MetricsSummaryOut` |
| GET `/metrics/net-worth?currency=&start=&end=&step=day` | `NetWorthSeriesOut`. `week`/`month` take the last day of each bucket. 422 if there would be more than 400 points |
| GET `/metrics/category-spend?currency=&month=YYYY-MM` | `CategorySpendOut` (bucket `spending` only) |

Runway's liquid figure is `depository` balances on `as_of`. Essential spend is `spending` lines
with `is_essential`, over the 3 trailing full months.

### 6.3 Forecast (B3b)
```
ForecastPointOut  {day: date, p10: MoneyStr | null, p50: MoneyStr, p90: MoneyStr | null}
OccurrenceOut     {day: date, amount: MoneyStr, series_id: uuid, label: str | null}
ForecastOut       {as_of, currency, horizon_days: 30|90|365, model_version: "f1",
                   status: "ok"|"insufficient_history"|"no_balances",
                   start_balance: MoneyStr | null, points: ForecastPointOut[] (one per day),
                   occurrences: OccurrenceOut[] (<= 500), low_balance: {threshold: MoneyStr,
                   first_day_below: date | null}, accounts_included: int, accounts_without_balance: int,
                   hidden_detail_accounts: int}
BacktestHorizonOut {horizon_days: 30|90, origins: int, weeks: int, wape: DecimalStr,
                    mape_floored: DecimalStr | null, mape_floor: MoneyStr, coverage: DecimalStr,
                    baseline_wape: DecimalStr | null, beats_baseline: bool | null}
BacktestOut       {currency, status: "none"|"queued"|"ok", run_at: datetime | null,
                   first_origin: date | null, last_origin: date | null, model_version: str | null,
                   horizons: BacktestHorizonOut[]}
```
| Method + path | Response | Errors |
|---|---|---|
| GET `/forecast?currency=&horizon=90` | `ForecastOut`. `low_balance.threshold` is the person's `low_balance_forecast` threshold | 422 |
| GET `/forecast/backtest?currency=` | `BacktestOut` (the newest row per horizon) | 422 |
| POST `/forecast/backtest` | 202 `{run_id, status, created}` | 503 `server_error` (queue down) |

### 6.4 Alerts (B4c)
```
AlertOut          {id, kind, severity, title, facts: {str: str}, account_id: uuid | null,
                   fired_at: datetime, read_at: datetime | null}
AlertPage         {items: AlertOut[], next_cursor: str | null, unread_count: int}
AlertRuleOut      {kind, enabled, params: {str: str}, cooldown_hours: int | null, is_default: bool}
AlertRuleIn       {enabled: bool, params?: {str: str}, cooldown_hours?: int | null}
EmailSettingsOut  {enabled: bool, detail: "minimal"|"full", smtp_configured: bool, address_hint: str}
EmailSettingsIn   {enabled: bool, detail: "minimal"|"full"}
AlertSettingsOut  {rules: AlertRuleOut[] (all nine, default-filled), email: EmailSettingsOut}
MarkRead          {ids?: uuid[] (<= 200), all?: bool}   # exactly one
```
| Method + path | Body → response | Errors |
|---|---|---|
| GET `/alerts?unread=false&cursor=&limit=50` | → `AlertPage` (newest first, keyset on `fired_at, id`) | 422 |
| POST `/alerts/read` | `MarkRead` → `{updated: int}` | 422 |
| GET `/alerts/settings` | → `AlertSettingsOut`. `address_hint` masks the local part (`s***@example.org`) | |
| PUT `/alerts/rules/{kind}` | `AlertRuleIn` → `AlertRuleOut` | 404 (unknown kind); 422 (`validate_params`) |
| PUT `/alerts/email` | `EmailSettingsIn` → `EmailSettingsOut`. **`detail = "full"` needs a fresh re-auth** (the existing `reauth_fresh` check, as Plaid link uses) | 403 `reauth_required`; 409 `smtp_not_configured` when enabling without SMTP |
| POST `/alerts/email/test` | → 202. Sends a fixed, data-free test message to the caller's login email. At most 1 per person per minute | 409 `smtp_not_configured`; 429 |

New `ApiError` code: **`smtp_not_configured` (409)**, added to `web/src/api/contracts.ts` in B4c.
If `reauth_required` or a 429 code doesn't exist yet, B4c reuses the codes the auth API already
uses. It never invents a second one.

### 6.5 Recurring (Phase 4, C5d) is reused, not changed
The Dashboard's upcoming bills come from `GET /recurring?direction=outflow`. The client keeps tier
`bill` and `next_expected_date` within 14 days.

---

## 7. Stories (one PR each)

Kinds:
- **pure:** no DB, no pnpm
- **db:** needs Postgres
- **web:** Prism

Gates: Bulwark on every migration, view, job, notifier and re-auth path. Ledger (money) on every
backend money path; for B3, a **separate** Ledger review run, because Ledger builds it. Prism on
web. Sounding on acceptance.

### 7.1 Story table
| ID | Scope | Kind · Owner | Reviewers | Migration token | Depends | Acceptance evidence |
|---|---|---|---|---|---|---|
| **B1a** | `app/domain/budgets.py` (§4.1) | pure · Helm (Grok team), Ledger lane | Ledger | — | — | Property tests (linear rollover, tiling periods, order independence, biweekly anchor); a pace table at the mid-period boundary; `to_assign` cases; float lint clean |
| **B1b** | M5: `budget`/`budget_line` changes, `budget_line_amount`, `budget_guard`, `v_ledger_lines`; models; `services/views.py`; `services/budgets.py` (core plus writes, audit) | db · Helm (Grok team), Ledger lane | Ledger, Bulwark | **M5** | B1a; M4 merged (or the skip rule, OQ-16) | §3.6 tests; the `v_ledger_lines` fixture (§3.6 M5); an empty-table guard test; a service test: the status of a split, a transfer, a refund, a pending row, and a hidden account; a summary_only reader sees no actuals from that account; two currencies refused in one budget |
| **B1c** | `/budgets` routes and schemas (§6.1), `make api-types` | db · Helm (Grok team), Ledger lane | Ledger, Bulwark | — | B1b | API tests across private/shared/summary_only/joint; 409 on a second active budget; 422 on a transfer-kind line; money as strings in the OpenAPI test; the `schema.d.ts` diff |
| **B2a** | `app/domain/metrics.py` (§4.2) | pure · Helm (Grok team), Ledger lane | Ledger | — | — | Property tests: savings rate None at zero income; debt utilization only over cards with a limit; weighted APR skips unknowns; currency mismatch raises |
| **B2b** | M6 views; `AGENT_VIEW_WHITELIST`; the view lint; `services/metrics.py`; `/metrics` routes (§6.2); a benchmark | db · Helm (Grok team), Ledger lane | Ledger, **Bulwark** | **M6** | B1b (`v_ledger_lines`), B2a | The view RLS matrix (§0.4); the view lint; the §3.6 M6 fixture; property tests (§0.5); bounded queries (a fixed query count per endpoint); a recorded benchmark at 100k rows, 20 accounts and 5 years: `GET /metrics/summary` p95. **If p95 is over 1 s, B2r is scheduled** |
| B2r | *Conditional fallback (ADR 0039 point 6):* the `ledger_daily_rollup` table plus the `metrics.rollup` job. Not scheduled unless B2b's benchmark fails | db · Helm, Ledger lane | Ledger, Bulwark | after M8 | B2b | Lattice amends this contract first |
| **B3a** | `app/domain/forecast.py` (§4.3) | pure · **Ledger (opus)** | Ledger (separate run) | — | — | Property tests (§4.3); an exact recurring-only world; a leakage test; Hyndman–Fan quantile vectors |
| **B3b** | M7; the `ForecastBacktest` model; `services/forecast.py` (the liquid-flows loader, `forecast_for`); the `forecast.backtest` job; `/forecast` routes (§6.3); an `up_to` parameter on C5b's recurring loader | db · **Ledger (opus)** | Ledger (separate run), **Bulwark** | **M7** | B3a, C5b, C4c (`transfer_link`), B2b (`v_account_balance_daily`) | The RLS matrix for `forecast_backtest`; card payments cancel; a mortgage payment counts; a backtest on the B6 world beats the baseline (§0.2) in a test; a pool timeout kills the child and fails the attempt |
| **B4a** | `app/domain/alerts.py` (§4.4) | pure · Helm (Grok team), Ledger lane | Ledger, Bulwark | — | B1a, B3a (types) | One test per kind; `due` honours one-shot and cooldown; dedupe keys match the CHECK regex; `email_body` minimal carries no digits other than the count and none of the fact values |
| **B4b** | M8; models; `services/alerts.py`; `app/notify/email.py`; the `insights.refresh` job; the nightly cron; `enqueue_for_all_households`; the enqueue at the end of `ledger.refresh`; SMTP settings | db · Helm (Grok team), Ledger lane | Ledger, **Bulwark** | **M8** | B4a, B1b, B3b (`forecast_for`), C4d (job), C3b (helper) | §0.3 for all nine kinds; per-person isolation (Alex's private charge never reaches Sam); a watermark test (an import of old rows fires nothing); email retry to `failed` after 3; a log canary: `SMTP_PASS` never appears in a log; the `alert_event_guard` test; the guard tests updated (`list_household_ids` only in the runner) |
| **B4c** | `/alerts` routes (§6.4); the `smtp_not_configured` code; re-auth on `detail=full`; a rate limit on the test email | db · Helm (Grok team), Ledger lane | Bulwark, Ledger | — | B4b | 403 without a fresh re-auth; 409 without SMTP; keyset paging; `MarkRead` exactly-one validation; `schema.d.ts` diff |
| **B5a** | PWA shell (§8.1) | web · Prism | Prism, **Bulwark** (`web/Caddyfile`, SW scope) | — | — | The config test (no `/api` route); `pwa.e2e.ts` (installability `[]`, no `/api` in caches, offline shell) |
| **B5b** | Budgets page (§8.2) | web · Prism | Prism, Sounding | — | B1c (MSW earlier) | vitest; 375 px; **`budgets.e2e.ts`**: create a budget, upload a synthetic statement, see the actuals and the pace marker |
| **B5c** | Forecast page (§8.3), chart wrapper (`recharts`) | web · Prism | Prism, Sounding | — | B3b (MSW earlier), B5a (`package.json` order) | vitest; 375 px; **`forecast.e2e.ts`**: the band chart renders; the backtest card shows WAPE, coverage, floored MAPE and the baseline |
| **B5d** | Dashboard v1 (§8.4) and the alerts inbox sheet | web · Prism | Prism, Sounding | — | B2b, B1c, B3b, B4c, C5d (MSW earlier), B5c (chart wrapper) | vitest; **`dashboard.e2e.ts` at 375 px**: no horizontal scroll, every card renders or shows its empty state, and opening an alert marks it read |
| **B5e** | Settings → Alerts (§8.5) | web · Prism | Prism, Sounding, Bulwark (re-auth flow) | — | B4c, B5d (`features/alerts`) | vitest; the `full`-detail toggle opens `ReauthDialog`; e2e: toggle a rule and see it persist |
| **B6** | Acceptance: the forecast world generator (extends Sounding's C1 generator), §0 runs, `docs/phases/PHASE-5.md`, the Q1 report | Sounding (+ Keel for the Q1 report) | Sounding | — | all | §0 criteria 1–7 |

Sizes: every pure slice and B1c, B2a, B4c and B5a are S. B1b, B2b, B3b, B4b and B5b–B5d are M.
B5e is S.

### 7.2 Order (waves; parallel inside a wave)
| Wave | Slices | Note |
|---|---|---|
| 0 | B1a, B2a, B3a, B4a (pure); **B5a** | B4a imports `BudgetPeriod`/`LineStatus` from B1a and `ForecastPoint` from B3a, so it merges after both, or stubs the types and rebases |
| 1 | **B1b** (M5), as soon as it holds the token | |
| 2 | **B2b** (M6), B1c | |
| 3 | **B3b** (M7), once C5b and C4c are in; B5b | |
| 4 | **B4b** (M8), once C4d and C3b are in; B5c | |
| 5 | B4c, B5d | live redeploy (Keel standing rule, PHASE-4 §7.1) |
| 6 | B5e, then B6 | |

Shared file orders:
- **Migrations and `backend/app/models/__init__.py`:** M4 → M5 → M6 → M7 → M8.
- **`backend/app/jobs/handlers/ledger_refresh.py`:** C4d → C5b → B4b. B4b adds one enqueue at the
  end of the handler.
- **`backend/app/worker/main.py`:** C5b (pool lifecycle) → B4b (cron).
- **`backend/app/jobs/runner.py`:** C5b (`cpu_pool`) → B4b (`enqueue_for_all_households`).
- **`backend/app/services/recurring.py`:** C5b → C5d → B3b (the `up_to` loader parameter only).
- **`web/package.json`, `pnpm-lock.yaml`:** B5a (`vite-plugin-pwa`) → B5c (`recharts`).
- **`web/src/mocks/handlers.ts`, `web/src/app/{routes,lazyRoutes}.tsx`, `web/src/api/schema.d.ts`:**
  additive only. Rerun `make api-types` to resolve conflicts, and never edit by hand.

### 7.3 Brief notes each slice carries (Keel copies them into briefs)
- **B1b:**
  - `v_ledger_lines` is the normative SQL in §3.1. Any deviation needs a contract amendment.
  - The view's grants are SELECT only.
  - `budget_guard` follows the PHASE-4 §3.0 trigger rules.
  - The service refuses lines on `transfer`/`excluded` categories, and lines in other currencies.
- **B2b:**
  - Views are security-invoker, and the lint enforces it.
  - `hidden_detail_accounts` uses `app_account_details_visible` (M1), never a copy of its predicate.
  - No `household_wide` anywhere.
  - The benchmark is recorded, not gating.
- **B3b:**
  - The loader reads `transaction` directly (ADR 0040 point 2). A test fails if it uses
    `v_ledger_lines`.
  - Detection per origin uses only rows on or before that origin.
  - The pool and timeout behaviour is ADR 0029 point 3.
  - The child gets ids, dates, amounts and merchant keys only.
- **B4b:**
  - Evaluation happens in person sessions only.
  - Email is sent after commit, never inside the transaction.
  - Exceptions are logged by class name only.
  - A canary test covers `SMTP_PASS` and the recipient address.
  - `dedupe_key` holds ids and dates only.
  - `enqueue_for_all_households` is the only new name for `list_household_ids` outside `db_context`
    and rotation, so update `tests/jobs/test_job_boundaries.py` and the household-wide guard only
    as needed, and say so in the PR.
- **B4c:**
  - Step-up on `detail=full` only. Enabling minimal email doesn't need re-auth.
  - The test email carries no data.
  - Audit `alert.email_updated {fields}`.
- **B5a:** see §8.1. `web/Caddyfile` serves `/sw.js` and `/manifest.webmanifest` with
  `Cache-Control: no-cache`. No change to `infra/caddy/` (a protected path).
- **All web slices:**
  - Money renders through `components/Money.tsx` (mono, tabular).
  - Arithmetic uses `decimal.js` only.
  - Semantic tokens only.
  - 44 px targets.
  - `ResponsiveSheet` instead of modals on small screens.
  - Copy follows Direction 01 (builder-to-builder) on self-host.

### 7.4 B6 synthetic forecast world (Sounding)
- 36 months per seed, 3 liquid accounts (checking, savings, one card) plus one loan account.
- Planted recurring items:
  - biweekly pay
  - monthly rent or mortgage as a paired transfer to the loan account
  - 6 monthly bills
  - 2 annual bills
  - 1 variable utility with seasonality
- Discretionary spend with a weekly level, month-of-year seasonality and heavy-tailed noise.
- Card payments as paired transfers. A few one-off large purchases.
- Fixed hidden seeds (as C7), and the generator lives under `backend/tests/eval/forecast/`.
- The report gives WAPE, coverage, MAPE and baseline per horizon over 5 seeds. **Pass = every seed
  meets §0.2.**

### 7.5 File ownership per run
| Slice | Owns (creates or edits) |
|---|---|
| B1a | `backend/app/domain/budgets.py`, `backend/tests/domain/test_budgets.py` |
| B1b | `backend/migrations/versions/<n>_budgets_v2.py`, `backend/app/models/budgets.py`, `backend/app/models/__init__.py`, `backend/app/models/_common.py` (enum tuples only), `backend/app/services/budgets.py` (new), `backend/app/services/views.py` (new), `backend/tests/models/test_rls_budgets.py`, `backend/tests/models/test_migration_budgets.py`, `backend/tests/models/test_view_ledger_lines.py`, `backend/tests/services/test_budgets_service.py`, `backend/tests/test_migrations.py` (the new revision only) |
| B1c | `backend/app/api/routes_budgets.py`, `backend/app/schemas/budgets.py`, `backend/tests/api/test_budgets*.py`, `web/src/api/schema.d.ts` (generated) |
| B2a | `backend/app/domain/metrics.py`, `backend/tests/domain/test_metrics.py` |
| B2b | `backend/migrations/versions/<n>_metric_views.py`, `backend/app/agent/tools.py` (the `AGENT_VIEW_WHITELIST` constant only), `backend/app/services/metrics.py`, `backend/app/services/views.py` (additions), `backend/app/api/routes_metrics.py`, `backend/app/schemas/metrics.py`, `backend/tests/models/test_view_lint.py`, `backend/tests/models/test_rls_metric_views.py`, `backend/tests/models/test_metric_views.py`, `backend/tests/api/test_metrics*.py`, `backend/tests/bench/test_metrics_bench.py`, `backend/tests/domain/test_money_invariants_phase5.py`, `web/src/api/schema.d.ts` |
| B3a | `backend/app/domain/forecast.py`, `backend/tests/domain/test_forecast.py` |
| B3b | `backend/migrations/versions/<n>_forecast_backtest.py`, `backend/app/models/forecast.py`, `backend/app/models/__init__.py`, `backend/app/services/forecast.py`, `backend/app/services/recurring.py` (`up_to` parameter only), `backend/app/jobs/handlers/forecast_backtest.py`, `backend/app/api/routes_forecast.py`, `backend/app/schemas/forecast.py`, `backend/tests/models/test_rls_forecast.py`, `backend/tests/services/test_forecast_service.py`, `backend/tests/jobs/test_forecast_backtest.py`, `web/src/api/schema.d.ts` |
| B4a | `backend/app/domain/alerts.py`, `backend/tests/domain/test_alerts.py` |
| B4b | `backend/migrations/versions/<n>_alerts.py`, `backend/app/models/alerts.py`, `backend/app/models/__init__.py`, `backend/app/models/_common.py` (enum tuples), `backend/app/services/alerts.py`, `backend/app/services/users.py` (`login_email` only), `backend/app/notify/email.py`, `backend/app/jobs/handlers/insights_refresh.py`, `backend/app/jobs/handlers/ledger_refresh.py` (one enqueue), `backend/app/jobs/runner.py` (`enqueue_for_all_households`), `backend/app/worker/main.py` (cron), `backend/app/config.py` (`smtp_port`, `smtp_from`), `.env.example` (`SMTP_PORT`, `SMTP_FROM`, comments on `ALERT_EMAIL`/`NTFY_*`), `backend/tests/models/test_rls_alerts.py`, `backend/tests/services/test_alerts_service.py`, `backend/tests/jobs/test_insights_refresh.py`, `backend/tests/notify/test_email.py`, `backend/tests/jobs/test_job_boundaries.py` (allowlist only) |
| B4c | `backend/app/api/routes_alerts.py`, `backend/app/schemas/alerts.py`, `backend/app/services/errors.py` (the code), `web/src/api/contracts.ts` (the code only), `backend/tests/api/test_alerts*.py`, `web/src/api/schema.d.ts` |
| B5a | `web/vite.config.ts`, `web/src/pwa/**` (new: `config.ts`, `register.ts`, `config.node.test.ts`), `web/src/main.tsx` (register call), `web/public/manifest.webmanifest`, `web/public/icons/**` (192 and 512 PNG, maskable), `web/Caddyfile` (headers), `web/package.json`, `web/pnpm-lock.yaml`, `web/playwright.config.ts` (a `prod` project on `vite preview`), `web/e2e/pwa.e2e.ts` |
| B5b | `web/src/pages/budgets/**`, `web/src/api/budgets.ts`, `web/src/mocks/budgets.ts`, `web/src/mocks/handlers.ts`, `web/src/app/{routes,lazyRoutes}.tsx`, `web/e2e/budgets.e2e.ts` |
| B5c | `web/src/pages/forecast/**`, `web/src/components/charts/**` (new), `web/src/api/forecast.ts`, `web/src/mocks/forecast.ts`, `web/src/mocks/handlers.ts`, `web/src/app/{routes,lazyRoutes}.tsx`, `web/package.json`, `web/pnpm-lock.yaml`, `web/e2e/forecast.e2e.ts` |
| B5d | `web/src/pages/OverviewPage.tsx` → `web/src/pages/overview/**`, `web/src/features/alerts/**` (new), `web/src/api/{metrics,alerts}.ts`, `web/src/mocks/{metrics,alerts}.ts`, `web/src/mocks/handlers.ts`, `web/src/app/{routes,lazyRoutes}.tsx`, `web/e2e/dashboard.e2e.ts` |
| B5e | `web/src/pages/settings/Alerts*.tsx`, `web/src/features/alerts/**` (after B5d), `web/src/app/routes.tsx`, `web/e2e/alerts-settings.e2e.ts` |
| B6 | `backend/tests/eval/forecast/**`, `backend/tests/acceptance/phase5/**`, `docs/phases/PHASE-5.md` |

---

## 8. Screens (B5; Direction 01, semantic tokens, money in mono, 375 px first, 44 px targets)

The nav already has `overview` (Dashboard), `budgets` and `forecast` (`web/src/app/nav.ts`). No new
nav entries. Alerts live on the Dashboard and in Settings.

### 8.1 PWA shell (B5a)
- `vite-plugin-pwa` (MIT; Prism records the licence and maintenance check in the PR) in
  `generateSW` mode. Options live in `web/src/pwa/config.ts` (exported, so they can be tested):
  - `registerType: 'prompt'`, `injectRegister: false` (`register.ts` registers in production
    only), `devOptions.enabled: false`
  - `workbox.globPatterns: ['**/*.{js,css,html,svg,png,woff2}']`,
    `navigateFallback: '/index.html'`, `navigateFallbackDenylist: [/^\/api(\/|$)/]`
  - `runtimeCaching: []`, no `importScripts`, `cleanupOutdatedCaches: true`
  - the manifest stays in `web/public/manifest.webmanifest`. Add 192 and 512 PNG icons, one
    `maskable`. Colours come from the brand tokens (`lib/tokens.ts`), never new hex values
- **The no-`/api` test** (`web/src/pwa/config.node.test.ts`):
  - `runtimeCaching` is empty or absent
  - the denylist matches `/api`, `/api/`, `/api/budgets` and `/api/x?y`, and doesn't match `/`
    or `/budgets`
  - no `urlPattern` key appears anywhere in the serialised options
  - after `vite build`, `dist/sw.js` contains no `registerRoute` whose pattern can match
    `/api/budgets`. Run it by importing the generated route regexes, or by grepping for
    `registerRoute(` calls other than the navigation route
- `pwa.e2e.ts` (the `prod` project) checks §0.6.
- The update prompt is a small toast ("New version ready, reload") using the existing toast
  pattern.

### 8.2 Budgets (B5b)
- **Header:**
  - the budget name, with a picker if there's more than one (the active one is preselected)
  - a period navigator (‹ November 2026 ›; biweekly shows dates) that calls `status?day=`
  - totals: planned, spent and left for spending
  - income received vs planned
  - zero-based: a "To assign: $X" banner (`to_assign ≠ 0`), "Balanced" otherwise
- **Lines** (spending first, then saving, then income):
  - each line shows the category name, a progress bar with a **pace marker** at
    `days_elapsed / days`, `actual / available` in mono, and a status chip (`On track`,
    `Over pace`, `Over`)
  - envelope lines show `carry_in` as "+$X rolled over" or "−$X carried"
  - tapping a line opens a sheet with:
    - the amount field ("From this period" or "From next period")
    - alert %
    - the last 6 periods as small bars (`/history?periods=6`)
    - "Remove from budget"
- An **unbudgeted spending** row sits under the lines.
- When `has_pending` is true, a small "Includes pending" note.
- When `hidden_detail_accounts > 0`: "Doesn't include spending on N accounts shared with you as
  summary only."
- **Create flow (sheet):**
  - name, period, method, a rollover toggle (envelope only), start date, currency (shown only if
    there's more than one)
  - lines are added with the grouped category picker (`GET /categories`, excluding transfer and
    excluded kinds) and an amount
- **Empty state:** "No budget yet" plus Create.

### 8.3 Forecast (B5c)
- Horizon tabs: 30 / 90 / 365 days.
- **Chart** (`components/charts/BandChart.tsx`, Recharts):
  - a P10–P90 area and a P50 line
  - a dashed low-balance threshold line
  - an "Below $X around <date>" callout when `first_day_below` is set
  - a tooltip with the three values in mono
  - an accessible `<table>` fallback (visually hidden)
- `insufficient_history`: P50 only, with "Bands appear after 26 weeks of history."
- `no_balances`: "Add a balance or import a statement with balances to forecast."
- **Scheduled items:** the next occurrences (date, label or merchant, amount in mono).
- **Backtest card:**
  - per horizon: WAPE, P10–P90 coverage ("Actual landed inside the band 78% of the time"),
    floored MAPE with its floor, and the baseline WAPE
  - a verdict: "Beats the simple baseline" or "Doesn't beat the simple baseline yet". Honest copy,
    never hidden
  - "Last checked <date>" and a "Run backtest" button (POST; it shows `queued`)
  - `none`: "No backtest yet"
- Footer: "Excludes N accounts shared with you as summary only" when relevant.

### 8.4 Dashboard v1 (B5d)
A card stack at 375 px and a 2–3 column grid on desktop. Each card has its own query and its own
empty or error state, so one failing endpoint doesn't blank the page.
1. **Net worth.** The current value (mono, large), the 30-day change, and a 90-day sparkline
   (`/metrics/net-worth?step=day`). A plain number: no Magic UI ticker in v1.
2. **This month.** Income, spending and net (`/metrics/summary?months=2`), and a "N to review"
   link to Transactions → needs category when `unreviewed_lines > 0`.
3. **Budget health.** The active budget's 3 lines with the highest `pct`, plus overall spent vs
   planned. It links to Budgets. Empty: "Set up a budget".
4. **Upcoming bills.** The next 14 days from `GET /recurring` (§6.5), up to 6 rows.
5. **Forecast.** A 90-day band sparkline and the low-balance callout. It links to Forecast.
6. **Alerts.** The unread count and the newest 3. Tapping one opens the alerts inbox sheet (paged
   `/alerts`). Opening an alert marks it read, and so does "Mark all read".

Agent findings wait for Phase 8.

### 8.5 Settings → Alerts (B5e)
- A rule per kind, with a switch and its parameters:
  - money fields use the `MoneyInput` pattern with string values
  - the factor is shown as a percent ("10% above usual")
- An email section:
  - an "Email me alerts" switch, disabled with "SMTP isn't configured on this server" when
    `smtp_configured` is false
  - detail: "Count and link only" (default) or "Include alert titles". The second opens
    `ReauthDialog` first
  - "Send test email"
  - the address hint
- Copy states plainly: "Emails pass through the mail server this instance is configured with."

---

## 9. Reviewer checklist (folded in from the earlier round-table and Phase 4 items)

| Source | Item | Where it's answered |
|---|---|---|
| Roundtable 04 Risks | Matview refresh cost on every sync | D1, ADR 0039 (plain views plus benchmark; B2r fallback) |
| Roundtable 04 §4–5 | MAPE undefined near zero; ETS bands fragile | WAPE + coverage + baseline + floored MAPE (ADR 0040) |
| Roundtable 04 §5 | ntfy needs an ADR | D13, OQ-7 |
| Roundtable 05 Risks | The SW must not cache real data | §8.1 config test plus `pwa.e2e.ts` |
| Gadfly C2 | Ship the manifest and SW with the Phase 5 Dashboard | B5a |
| PHASE-4 §10 | Recurring price-increase alerts are Phase 5 B4 | §5.1 |
| PHASE-4 §10 | `v_recurring` for the agent is Phase 8 | D17 |
| STATUS carry-over | Metrics use `COALESCE(splits, parent)` and exclude `is_transfer`; never sum across currencies | §2, `v_ledger_lines` |
| ADR 0022 | Rules for metrics 1–4 | `v_ledger_lines` `bucket` |

---

## 10. Out of Phase 5 (recorded, not built)
- What-if (PLAN 8.7), irregular-income mode (PLAN 20.3; OQ-6), retirement Monte Carlo, debt payoff
  timelines (avalanche vs snowball), spend z-score outliers, and fixed-vs-variable as a metric (the
  ROADMAP cut list)
- forecasts per account (OQ-9)
- recurring transfers to non-liquid accounts as scheduled items (OQ-10)
- `v_budget_status`, `v_recurring`, `v_holdings_latest`, `v_contributions_ytd` and `v_tax_items`
  for the agent (Phase 8; `v_tax_items` with Phase 7)
- ntfy or any push channel (needs an ADR)
- credit alerts (Phase 6)
- per-person budgets (OQ-2)
- a household time-zone setting (OQ-5)
- budget suggestions from history
- the Magic UI number ticker on the Dashboard

---

## 11. Open questions for Keel (each has a default; none blocks a story)

1. **OQ-1, the meaning of `summary_only` for metrics.** Should a summary-only viewer see aggregate
   cash flow or spend totals for those accounts, without the rows? **Default: no.** Balances only,
   as ADR 0008 has it today, with `hidden_detail_accounts` surfaced in the UI. Showing totals would
   need a definer aggregate path and a Bulwark review.
2. **OQ-2, budgets per household or per person.** **Default:** household budgets, at most one
   active, any member edits, and actuals from what each reader can see. Per-person budgets would
   add `budget.person_id` and a restrictive policy.
3. **OQ-3, hidden accounts in metrics.** **Default: excluded everywhere.** Confirm, or define
   "hidden from lists, still counted".
4. **OQ-4, the sign of envelope rollover.** **Default: both signs carry** (an overspend reduces the
   next period). That keeps rollover linear and SQL-expressible for the Phase 8 view. The
   alternative is YNAB's reset-to-zero on overspend.
5. **OQ-5, household time zone.** **Default:** `as_of` = the UTC date, and the nightly cron at 07:15
   UTC. Near midnight local time, budgets can be a day early or late. A setting would go in
   household settings (hard rule 13), not in code.
6. **OQ-6, irregular-income mode.** PLAN 20.3 puts it in Phase 5, but the ROADMAP cut list moves it
   to Q2. **Default:** not in Phase 5 acceptance. Confirm the ROADMAP ruling stands.
7. **OQ-7, ntfy.** It needs an ADR (hard rule 9) before any code: a self-hosted container in
   compose or a public server, topic secrecy, and payload content (it should match ADR 0041's
   minimal default). **Default:** not in Phase 5. Do you want Lattice to draft the ADR as a stretch
   item?
8. **OQ-8, how Helm builds.** `docs/process/FIRSTCHAIR.md` rule 3 says "Codex writes, Grok reads",
   and that a Grok write worker is refused. The brief assigns B1, B2 and B4 to "Helm (Grok team),
   Ledger lane". **Please confirm Helm's write path:** which seat writes, who applies the diff, and
   that confinement still holds. If Grok can't write here, the default is Anvil (Codex) for the
   pure slices and Ledger (sonnet) for the db slices.
9. **OQ-9, forecasting each account.** PLAN 8.7 asks for bands per account and in total.
   **Default:** total liquid position per currency only (ADR 0040). Per account needs flows between
   accounts modelled.
10. **OQ-10, paired payments to non-liquid accounts** (mortgage, brokerage). The forecast counts
    them as outflows, but they aren't scheduled, because recurring detection ignores transfers
    (ADR 0028), so they widen the bands. **Default:** accept for f1. The B6 world plants one, so
    the effect is measured. An extension would detect series on transfer legs whose partner isn't
    liquid.
11. **OQ-11, deferred metrics.** Debt payoff timeline, z-score outliers, retirement projection.
    **Default:** deferred (ROADMAP E5.8 and the cut list). Debt in Phase 5 is owed, utilization and
    weighted APR.
12. **OQ-12, savings rate and payroll contributions.** Payroll-deducted 401(k) contributions never
    pass through a cash account, so the rate is on take-home pay. **Default:** state that in the UI
    tooltip. Adding them needs paystub or `investment_txn` contribution data (Phase 7 or 11).
13. **OQ-13, `ALERT_EMAIL`.** ADR 0041 sends to each person's login email. **Default:** leave
    `ALERT_EMAIL` unused, with an `.env.example` comment. The alternative is to keep it as the
    recipient for household-level kinds only (relink, statement review).
14. **OQ-14, "Lighthouse installable".** Lighthouse 12 removed the PWA category. **Default:**
    acceptance uses CDP `Page.getInstallabilityErrors` in Playwright (§0.6). Confirm the ROADMAP B5
    wording can change to match.
15. **OQ-15, new web dependencies.** `vite-plugin-pwa` (with Workbox) and `recharts`, both MIT. They
    are build or runtime libraries, not external services, so no hard rule 9 ADR. Prism records the
    maintenance and licence check in each PR. Confirm no ADR is wanted.
16. **OQ-16, M5/M6 ahead of M4.** M5 and M6 need no Phase 4 table. **Default:** if C5b's PR has no
    verdicts one window after M3 merges, M5 and M6 may take the token under the existing skip rule,
    and Keel notes it in SPRINTS.
