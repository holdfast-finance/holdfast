# Holdfast roadmap — Q1 (2026-11-19 → 2027-02-18)

Owner: **Keel** (PM). Decided at the 2026-11-19 round table (`docs/planning/roundtable/`: 7 lane memos + Gadfly's challenge). Rulings C1–C8 in `08-gadfly-challenge.md` are adopted as written.

## Finish line

**Q1 is done when Phase 5 passes acceptance on Plaid sandbox + synthetic data.**
Critical path: **1.5 Foundations → 2a Plaid + ingest → 3 CSV/OFX import → 4 Categorize → 5 Budgets/metrics/forecast.**
Every other lane is **preemptible**: it runs only when the critical-path queue is blocked, and pauses automatically when the critical path is more than one window behind.

## Units (agent time, not human days)

- **Window** = one 5-hour usage window. This is the binding constraint, not wall clock. A run takes 3–8 minutes; a window fits roughly 2 waves of 3 parallel runs plus reviews.
- **Size:** S = 1 run · M = 2–3 runs · L = split before scheduling.
- **Tokens:** a run measured ~75k output tokens and several million cache-read tokens (Phase 0–1: 2.1M output / 235M cache-read over 28 runs). We don't yet know which of these the limit weighs, so **Sprint 1 logs the usage delta per run and recalibrates** before later sprints are committed.
- **Calendar dates are derived, not promised:** at ~3 windows a day of continuous running, 1 window ≈ ⅓ day.

## Timeline

| Sprint | Windows | Milestone | Exit criteria | Est. output tokens |
|---|---|---|---|---|
| S1–S2 | W1–W2 | **Phase 1.5 Foundations + Gate** | Merge gate live in CI (free tier, ADR 0013); layering ADR, job framework, OpenAPI types, money primitives, secrets resolver, schema-gap migration, brand plumbing | ~1.5M |
| S3–S5 | W3–W5 | **Phase 2a: Plaid sandbox + ingest** | Sandbox Items sync from fixtures + sandbox; idempotent replay; relink flow; slot guard; Sync Health API + page | ~2.0M |
| S6–S7 | W6–W7 | **Phase 3: CSV/OFX/competitor import** | Re-import gives zero duplicates; overlap with Plaid dedupes; reconcile ±$0.01 or `needs_review`; review UI | ~1.3M |
| S8–S10 | W8–W10 | **Phase 4: categorize, transfers, recurring** | Synthetic holdout: ≥90% coverage at ≥95% precision; transfers excluded from spend; planted bills recovered ≥95% | ~1.8M |
| S11–S13 | W11–W13 | **Phase 5: budgets, metrics, forecast, alerts + PWA shell** | Budget updates within one sync cycle; forecast WAPE + P10–P90 coverage beat seasonal-naive; alerts fire in tests; installable app shell, no `/api` caching | ~2.0M |
| — | W14–W16 | Buffer / stretch lanes | — | — |

Derived calendar if we run ~3 windows a day: Phase 1.5 ≈ 2026-11-20 · Phase 2a ≈ 11-21 · Phase 3 ≈ 11-23 · Phase 4 ≈ 11-24 · **Phase 5 ≈ 2026-11-26**. The quarter leaves a large buffer for recalibration, owner-queue waits and stretch lanes. Keel re-forecasts after every sprint.

## Epics and stories (critical path)

Format: `ID title — size — owner/model — acceptance — depends`. Story IDs are stable; briefs live in `docs/planning/briefs/<ID>.md`.

### E1 Phase 1.5 Foundations + Gate (cap: 2 windows)
- **F1** ADR 0011 layering `api → services → domain`, auto-registering routers, import-linter in CI — S — Lattice/opus — CI fails on a layering violation — none
- **F2** Job framework: arq `@job` registry, idempotency keys, per-household lock, retry policy, `job_run` table with RLS, IDs-only payloads — M — Lattice/opus — job enqueued twice gives one side effect; non-UUID payload rejected — F1
- **F3** OpenAPI → TS: offline schema dump (`make api-types`), CI drift check, delete hand-written `types.ts`, money fields typed as strings; Prism wires `openapi-fetch` — M — Lattice + Prism/sonnet — typecheck fails on a backend contract change — none
- **F4** Money primitives (one story for Lattice E1.6, Ledger `money.py`, the float lint and the `parse_float=Decimal` helpers) — S — Ledger/sonnet — property tests; seeded float fails lint — none
- **F5** Secrets: `SecretToken` type (no repr/str/JSON), Fernet `kid` versioning + `make rotate-keys`, `get_secret(household, key)` resolver, log-canary test — M — Bulwark design/opus, Harbor build/sonnet — rotation re-encrypts all rows; canary never reaches logs — F1
- **F6** Schema-gap migration (the serialized schema run): `transaction.superseded_by`, `import_batch`/statement table, `sync_run` table, RLS — S — Tide/sonnet + Lattice review — RLS lint + up/down green — F1
- **F7** Merge gate: `ci-required` aggregate, single-Alembic-head check, RLS lint, blocking audits with expiring allowlist, SHA-pinned actions, gitleaks on PR range, protected-path check; `main` guarded per ADR 0013 (free tier: `main-guard` detection, minutes budget, `ci-local`) — M — Harbor/sonnet (sole CI writer) — a red, stale or unreviewed PR can't merge — none
- **F8** Worktree hygiene: `scripts/wt-new.sh`, `make sync`, stale-base guard — S — Harbor/sonnet — new worktree HEAD == `origin/main` — none
- **F9** Phase 1 security lows 1–2: argon2 semaphore before the DB session + real-client-IP check (XFF assertion in e2e) — S — Bulwark/opus — burst test keeps the pool free — none
- **F10** Brand-theme plumbing (`data-brand`, Holo tokens/fonts, AA tests for both brands) — S — Prism/sonnet — token tests pass for Signal + Holo — none
- **F11** Trunk model in skills: rewrite `phase-kickoff`/`phase-review` (milestones, no owner stop), PR template with the gate checklist — S — Harbor/sonnet — skills say nothing about asking the owner to merge — F7
- **F12** e2e in CI on main-merge + nightly, auto-revert on red — M — Harbor/sonnet + Sounding — first-run spec green in CI in under 10 minutes — F7

### E2 Phase 2a Plaid sandbox + ingest (poll-first, no webhooks)
- **P1** `BankConnector` protocol, registry, `FakeConnector`, per-household BYO keys via F5 — S — Tide/sonnet — fake-connector contract tests — F5, F6
- **P2** `IngestService` (shared by 2, 3, 10a): `(account_id, external_id)` upsert, fingerprint with same-day ordinal, pending→posted, `removed`, Plaid-wins dedupe, `superseded_by` — M (budget ~750k) — Tide/sonnet, contract by Lattice — 100 random replay batches give the same end state — P1, F4
- **P3** Plaid client: `parse_float=Decimal`, `request_id` logging, token-redaction test, sign negated once per product — S — Tide/sonnet — no float in the parse path — P1
- **P4** Link token, exchange, Item slot guard (typed confirm at 8, never an 11th; deleted Items still count) — M — Tide/sonnet; Bulwark review — guard tested at 7/8/10 — P3
- **P5** Transactions sync: cursor saved after commit, mutation-during-pagination restart, recorded-fixture replay — M — Tide/sonnet — kill mid-page and resume gives no duplicates — P2, P3
- **P6** Balances, investments, liabilities → snapshots/holdings (sign test per product) — M — Tide/sonnet; Ledger review — one test per product — P5
- **P7** `ITEM_LOGIN_REQUIRED` → `needs_relink`, update mode, backoff; poll scheduler every 4–6h on F2; `seed_sandbox.py` — M — Tide/sonnet — sandbox reset-login recovers in the same slot — P5, F2
- **P8** Sync Health API (stale > 48h, error codes) + relink duplicate cleanup — M — Tide/sonnet — stale flag tested; relink duplicates merged reversibly — P5
- **P9** Pages: Accounts, Plaid Link, slot counter (typed confirm), Sync Health — M — Prism/sonnet — renders at 375px; the UI can't create an 11th Item — P4, P8, F3
- **P10** Phase 2a threat model + review + acceptance — S+S — Bulwark/opus, Sounding/sonnet — PHASE-2.md PASS — all

### E3 Phase 3 import (CSV/OFX + competitor exports; PDF cut)
- **I1** `NormalizedTxn`, statement reconcile (±$0.01 or `needs_review`), upload endpoint with SHA-256 dedupe, document storage threat model — M — Tide/sonnet; Bulwark for documents — re-upload returns the same document — P2
- **I2** Generic CSV with a YAML profile loader + OFX/QFX (FITID → `external_id`) — M — Tide/sonnet — re-import gives zero rows — I1
- **I3** Mint, YNAB, Monarch and Copilot exports (one PR) — M — Tide/sonnet — each re-import gives zero rows — I2
- **I4** Dedupe vs Plaid on overlap fixtures (±3 days, fuzzy ≥ 85, Plaid wins) — S — Tide/sonnet — overlap fixtures pass — I2, P2
- **I5** Import wizard + `needs_review` queue UI — M — Prism/sonnet — reconciled and unreconciled statements render — I1, F3
- **I6** Acceptance — S — Sounding — PHASE-3.md PASS — all

### E4 Phase 4 categorize, transfers, recurring
- **C1** `LedgerService` as the only writer of amount/category/transfer fields + architecture test; Sounding's synthetic generator with hidden holdout seeds — M — Ledger/sonnet, generator by Sounding — router writing `is_transfer` fails the arch test — F1, F4
- **C2** Category tree seed + Plaid PFC mapping (schema run) — S — Ledger/sonnet — every PFC code mapped or explicitly unmapped — C1
- **C3** Rules engine + retroactive preview/apply — M — Ledger/sonnet — preview count = applied count; idempotent — C2
- **C4** Transfer pairing (0–5 days, ambiguity → review) — M — Ledger/sonnet — card payment excluded, purchases counted — C1
- **C5** Recurring detection — M — Ledger/sonnet — ≥ 95% of planted bills recovered on the holdout — C1
- **C6** Review queue API + Transactions page (virtualized, filters, bulk, split, rules) — M — Ledger + Prism/sonnet — 375px; 50k-row scroll budget — C3, F3
- **C7** Eval harness + acceptance (coverage/precision on the holdout) — S+S — Sounding — ≥ 90% coverage at ≥ 95% precision — C3–C5

### E5 Phase 5 budgets, metrics, forecast, alerts
- **B1** Budget status service (category/envelope/zero-based, rollover, biweekly) — M — Ledger/sonnet — new txn updates budget within one sync cycle — C1
- **B2** Metrics behind `v_*` views (cash flow, savings rate, net worth, runway, debt) + matview refresh job (schema run) — M — Ledger/sonnet; Bulwark for views — views respect RLS + `summary_only` — B1, F2
- **B3** Forecast: recurring schedule + residual model with empirical-quantile bands, backtest store (WAPE + P10–P90 coverage vs seasonal-naive) — M — Ledger/opus — beats the baseline on the holdout — C5
- **B4** Alerts engine (rules, cooldown) over email; ntfy needs an ADR first — M — Ledger/sonnet — alerts fire in tests — B1
- **B5** Budgets, Forecast and Dashboard v1 pages + PWA manifest/SW (app shell only; test asserts no `/api` route) — M — Prism/sonnet — Lighthouse installable; Dashboard at 375px — B1–B3
- **B6** Acceptance + Q1 report — S — Sounding + Keel — **PHASE-5.md PASS = Q1 done** — all

## Stretch lanes (preemptible, in this order)
1. **T1** Tax-year config TY2025 + TY2026 YAML + verifier (Tally/opus): side lane from W1, since it's pure data.
2. **A1** Redaction library with property tests (Pilot/opus).
3. **S1** SimpleFIN connector + quirks (Tide), only if 2a closes early. Needs an ADR.
4. **K1** Descriptor/xpub parsing + derivation vectors (Cipher/opus); **K2** per-wallet lot engine.
5. **D1** One user doc page per phase (Beacon/sonnet low), the minimum alongside each phase.

## Cut from Q1 (Q2 candidates)
PDF statement profiles · Phase 6 credit · tax engine, corpus, oracles, SC plugin, relief catalog, HSA (E7.2–7.7) · advisor beyond redaction (E8.2–8.6) · crypto E10.4–10.6 · ML classifier · what-if · irregular income · Monte Carlo · Starlight docs site and full doc set · visual regression · release-please/SBOM · host deploy · Plaid webhooks/Funnel.

## Q2 outline (2027-02-19 →)
Phase 6 credit → 7 tax engine + 10b → 8 Advisor (provider abstraction, MCP, findings) → 8.5 private access on the owner's host → 9 hardening, deploy, backups, docs site → 10c BTC wallet (QR + file signing) → OSS milestone (PLAN 17.6 step 2).

## Phase 11: Investments

[Phase plan](../phases/PHASE-11-investments.md). 11a is preemptible after Phase 3 and supplies K2 with the shared lot engine and price history before 10a. Phase 5 PASS remains the Q1 finish line.

### 11a Track
- **V1** Brokerage schema and household RLS — M — Lattice/opus — one Alembic head and runtime-role RLS tests — Phase 3 PASS, F4, schema ADR
- **V2** Shared lot engine, transfer matching and price contract — M — Ledger/sonnet — brokerage and wallet fixtures pass the same engine tests — V1, schema and pricing ADRs
- **V3** Positions and activity CSV — M — Tide/sonnet — reconciliation and same-day replays pass — V2, I1, I2, P2, strategy ADR
- **V4** Investment OFX/QFX and overlap handling — M — Tide/sonnet — FITID and file/feed replay leave one effective row — V3, I4
- **V5** Realized G/L CSV evidence — M — Tide/sonnet — broker basis and adjustments survive import — V3
- **V6** Manual corporate-action corrections — M — Ledger/sonnet — a split preserves basis and incomplete terms go to review — V2
- **V7** P6 extension for Plaid Investments lots, prices and delayed history — M — Tide/sonnet — delayed pages replay without duplicates — V2, V4, P1–P7, Plaid amendment
- **V8** Investments read API and page v1 — M — Prism/sonnet — 375px demo covers freshness, missing prices and restricted accounts — V5–V7, F3, copy ADR
- **V9** 11a acceptance — S — Sounding/sonnet — plan checks pass on Sandbox and synthetic fixtures — V1–V8

### 11b Connectors
- **VB1** One eligible direct read-only broker connector — M — Tide/sonnet — read-only credentials and endpoint tests pass — V9, connector ADRs, eligible broker
- **VB2** Optional SnapTrade Personal connector — M — Tide/sonnet — disclosure, replay and endpoint tests pass — VB1 or explicit skip, SnapTrade ADR, verified terms, owner decision

### 11c Tax
- **VT1** Realized gains and disposals behind `FEATURE_TAX` — M — Tally/opus — fixture sales reconcile to lots and flag-off tests pass — V9, Phase 7 tax foundation, 10b export contract, copy ADR
- **VT2** Household wash-sale estimate — M — Tally/opus — cross-account fixture follows verified config and surfaces coverage gaps — VT1
- **VT3** 1099-B reconciliation and shared export — M — Tally/opus — mismatches cite source rows and replay is idempotent — VT1, VT2, Phase 7 document verification

## Owner queue (non-blocking by design)
1. **Move the production Plaid keys out of the dev `.env`** (keep only sandbox keys there). Until then agents build Phase 2 fixture-first and never run against live Plaid.
2. Plaid Trial signup; afterwards one redacted real-Item sync dump so Sync Health can leave `beta`.
3. Pick the host and set up Tailscale (blocks deploy, Q2).
4. Anthropic API key with a spend cap (Q2 advisor).
5. Real statements, bills and labels to validate import/categorization. These checks are recorded as *unverified*, never *passed*, until done.
6. Weekly digest: read when convenient. Changes to `.github/`, `.claude/` and `CLAUDE.md` are listed there so you can veto.
