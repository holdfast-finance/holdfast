# Ledger memo: Phases 4, 5, 6 (money/domain)

## 1. Position
1. **One write path for money state.** Build `app/domain` as pure functions on frozen dataclasses (no DB, no I/O) plus one `LedgerService` in `app/services` that is the only code allowed to set `amount`, `category_id`, `category_source`, `is_transfer`, `transfer_group_id`. Modules: `money.py` (Decimal helpers, `quantize_presentation`, sign guard), `categorize.py`, `transfers.py`, `recurring.py`, `budgets.py`, `forecast.py`, `alerts.py`. Enforce with (a) an architecture test that greps/AST-scans for writes to those columns outside `LedgerService`, (b) ruff `float`/`Decimal(float)` ban over `app/domain`, (c) hypothesis property tests: transfers excluded from every metric, pair is symmetric and sums to 0, sign invariants, splits sum to parent amount, budget spent == sum of non-transfer outflows. This is Phase 4's first story, before any algorithm.
2. **Fix the metric definitions before the models.** PLAN's "90% auto-categorized" mixes coverage and correctness, and MAPE is undefined near zero balances. Define both (section 4) and ship a measurement harness in Phase 4/5 so criteria are executable by Sounding with no owner data.
3. **Cut speculative ML.** Ship rules + Plaid PFC map + transfers + recurring first, measure coverage on synthetic data, then add the TF-IDF classifier only if coverage < target. No nightly cron: retrain on demand after N new confirmed labels, model stored per household, deterministic seed.

Order change: in 8.2 transfer detection must run before user rules' `is_transfer` and PFC (a user rule can still force it, but pairing wins over a category). Phase 4 before 5 is fine; Phase 6 is the only phase blocked on real data (see Challenge).

## 2. Epics + stories
**E4 Categorize/transfer/recurring**
- E4.1 Domain skeleton, `money.py`, LedgerService, invariant + arch tests, synthetic txn generator (seeded, ground-truth labels) — M — property tests green, arch test fails if a router writes `is_transfer` — Phase 1 schema
- E4.2 Category tree seed + PFC mapping table (migration, RLS) — S — every PFC code maps or is explicitly unmapped — E4.1
- E4.3 Rules engine (priority, match jsonb, first-hit, `category_source`) + retroactive preview/apply — M — preview count equals applied count; idempotent re-apply — E4.2
- E4.4 Transfer pairing (0-5d, same household, abs amount, hint words, ambiguity -> no pair + review) — M — card payment excluded, purchases counted; ambiguous cases land in queue — E4.1
- E4.5 Recurring detection (cadences, CV thresholds, status, paychecks) — M — recovers all planted bills in generator at >= 95% — E4.1
- E4.6 Review queue API + UI hook (recategorize -> "create rule?") — S/M — Prism owns UI — E4.3
- E4.7 ML classifier (conditional, on-demand retrain) — M — only if E4.8 shows gap — E4.8
- E4.8 Categorization eval harness (section 4) — S — prints coverage/precision/per-source report — E4.3
**E5 Budgets/metrics/forecast/alerts**
- E5.1 Budget status service (category/envelope/zero-based, rollover, biweekly periods) — M — E4
- E5.2 Metrics service behind `v_*` views (cashflow, savings rate, net worth, runway, debt) — M — E5.1
- E5.3 Forecast: deterministic recurring schedule + residual model with empirical-quantile bands — M — E4.5
- E5.4 Backtest + MAPE/WAPE/coverage store and Forecast page data — S — E5.3
- E5.5 What-if overlay — S — E5.3
- E5.6 Alerts engine (rule objects, dedupe/cooldown, email + ntfy channels) — M — E5.1
- E5.7 Irregular-income mode (pay-yourself salary, buffer months, tax set-aside jar) — M — E5.3
- E5.8 Retirement Monte Carlo + debt payoff — L, defer to Q2 unless idle
**E6 Credit**
- E6.1 Credit domain model + manual score log + diff engine on structured tradelines — M — synthetic parsed fixtures
- E6.2 PDF parser (one bureau at a time) against synthetic PDFs built from the template layout — M each
- E6.3 Cross-bureau discrepancy + new-inquiry alert — S — E5.6

## 3. Risks
- Transfer pairing ambiguity (two same-amount rows, delayed posting, pending to posted amount changes). Retire: generator emits adversarial cases; ambiguity must degrade to "unpaired + queue", never a wrong pair.
- Pending to posted replacement breaking categories/rules/labels: specify that user-set category survives the swap (carry via `supersedes`).
- Materialized `budget_period_actual` refresh cost on every sync: use a plain view or targeted recompute per affected (budget, period); spike with 100k synthetic rows.
- Credit parsers drift; real reports are owner-only. Parse to a versioned schema, flag `parsed_ok=false` loudly.
- Timezone/period boundaries: store date, not timestamp, for posted_date; period math in household tz.

## 4. Measuring without owner labels
- **Categorization:** generator = merchant catalog (~300 merchants, each with true category, descriptor variants, bank-style noise like `SQ *`, trailing store IDs, truncation, amount distributions), 24 months, 3 accounts. An oracle "labels" the 200 most recent/frequent rows; rest is holdout. Report coverage (not Uncategorized), accuracy on covered rows against ground truth, per-source split. Define criterion as **coverage >= 90% AND accuracy >= 95% on covered**, with noise tiers (clean/medium/hostile). Report synthetic results as an optimistic upper bound; real-data check goes on the owner queue (label 200, run harness locally, post only aggregate numbers).
- **Forecast:** generative world with known recurring items + seasonal discretionary + noise. Rolling-origin backtest (monthly origins, 30/90-day horizons). Metric: WAPE on weekly category/total outflow aggregates (MAPE only where |actual| > floor), plus P10-P90 interval coverage (target 75-85%), and must beat a seasonal-naive baseline. Display all three; "MAPE is displayed" is then met honestly.

## 5. Challenge
- PLAN Phase 6 acceptance ("three real reports parse") is unachievable autonomously. Re-scope acceptance to synthetic reports + a bureau-layout contract test; real reports become an owner-queue verification, not a gate.
- "Retrain nightly once 200+ labels" is over-scoped and adds a scheduler for a tiny model. Replace as above.
- ETS bands for P10/P90 on sparse weekly data is fragile. Use empirical residual quantiles from the backtest.
- ntfy is an external service: needs an ADR (hard rule 9) before E5.6. Email-only is the safe default.
- 8.6 Monte Carlo retirement and Spend-by-category z-score outliers are not in the Phase 5 acceptance; park.

## 6. Token savers
- Generator + eval harness built once (E4.1/E4.8) and reused by E5 and E6 tests.
- Pure-function modules mean agents need only `domain/` + fixture context, not routers/models.
- One reviewer pass (money-invariants) per epic, not per story.
- Keep stories to one module + its tests; migrations via the `new-migration` skill in their own run.
