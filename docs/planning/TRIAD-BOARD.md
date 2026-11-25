# Triad board: epics and stories to 0.1.0-beta.1

Who builds what, per [TRIAD.md](../process/TRIAD.md). Story scope and acceptance come from the
phase contracts ([Phase 2](../phases/PHASE-2-contract.md), [Phase 4](../phases/PHASE-4-contract.md)) and
[ROADMAP.md](ROADMAP.md). This board only assigns owners and order. Live state comes from `gh pr list`
and the bus. Keel updates this file when a story lands or moves.

**Rules:** one story = one branch off `origin/main` = one PR. `claim` on the bus before starting, and
post a `result` with the PR number and head SHA when done. Lane reviewers come from the contract and
satisfy `review-gate`. A cross-vendor second opinion is requested on every PR. Only Keel merges.
Synthetic fixtures only.

**Design authority:** Keel. Anvil and Helm build against the contracts. Where a contract is silent,
post a `challenge` on the bus rather than inventing API shape, schema or UI in code.

## Migration token queue (one holder at a time; Keel assigns)

`C3b` (0012, PR #91, Keel) → `C4c` (M3, Helm) → `C5b` (M4, Helm) → Phase 5 schema (B1/B2, per the
Phase 5 contract). Nobody else writes under `backend/alembic/` or `backend/app/models/`.

## Shared-file order (avoid rebase fights)

- `routes_connections.py`: P4-link (done) → P4-preview → P4-map, all Anvil.
- `statement_import.py`: P2b (Anvil) → C3e (Helm). C3e waits for P2b to merge.
- `IngestService` / `services/sync.py`: P2b then P5b (Anvil) first, then C3e's ingest-time rules (Helm).

## Keel (Claude): design, frontend, gates, merges

| Epic | Story | What | Depends |
|---|---|---|---|
| Merge queue | #91 C3b, #92 C3d, #89 H2, #93 P9b-link follow-ups, #16 F7b | Review, gate and land | — |
| Design | **K-P5** Phase 5 contract | `docs/phases/PHASE-5-contract.md`: budgets, metrics views, forecast, alerts, API shape, schema, screens. Unblocks Helm's B1/B2/B4 | — (start now) |
| Design | ADR 0019 amendment | Live Codex/Grok sessions (owner decision 2026-11-24) | — |
| Frontend | P9b-map, P9d | Preview/decision sheet, Sync Health page | P4-preview, P7/P8 (mocks earlier) |
| Frontend | A1w, W2, W3, W4 | Account owner sheet, rules UI, transfers tab, recurring tab | A1, C3e, C4e, C5d (mocks earlier) |
| Frontend | B5 | Budgets, Forecast, Dashboard v1, PWA app shell | B1–B3 |
| Forecast | B3 | Forecast with backtest vs seasonal-naive (Ledger, opus) | C5b |
| Gates | F11, F12, P10, I6, C7, B6 | Protected CI paths, phase acceptance (Sounding) | per phase |

## Anvil (Codex): Plaid and import pipeline (backend, full-stack)

Lane: Tide. Reviewers per contract (Ledger for money, Bulwark for tokens/connectors).

| Order | Story | What | Depends |
|---|---|---|---|
| 0 | cleanup | Delete `docs/process/THREE-AGENT-COLLABORATION.md`; refresh `WORK-ORDERS.md` (docs-only PR) | — |
| 1 | **P2b** | Phase 2 contract §3.4 plus the matcher call in statement import. Continue Tide's draft on local branch `feat/P2b-ingest-service` (`efdf75b`, unreviewed WIP) | P2a, P2m, P4a (merged) |
| 2 | **I3** | Mint, YNAB, Monarch, Copilot export importers, one PR; re-import = zero rows. Design ruled on the bus (account map) | I2 (merged), **P2b** |
| 3 | **P4-preview** | `connections.preview` job, person-scoped candidates, sealed Redis rows, 50k cap | P2m, P4-link (merged) |
| 4 | **P4-map** | Decisions, close, un-adopt, `confirm_held` | P2b, P4-preview |
| 5 | **P5b** | `plaid.sync_item` job: one transaction, cursor after commit, `sync_run`, balance snapshots, `ledger.refresh` enqueue | P2b, P5a, **P4-map** |
| 6 | **I4** | Dedupe vs Plaid on overlap fixtures | P5b |
| 7 | **P6**, **P7**, **P8** | Investments/liabilities/balances; relink, backoff, cron, `seed_sandbox.py`; Sync Health API | P5b |
| 8 | **V1** | `connector_audit` one-off service (counts only) | P5b |

## Helm (Grok): ledger backend (accounts, rules, transfers, recurring, budgets)

Lane: Ledger. Reviewers per contract (Ledger independent run for money, Bulwark for API/RLS).

| Order | Story | What | Depends |
|---|---|---|---|
| 1 | **A1** | `PATCH /accounts/{id}` owner/visibility, `AccountService`, owner fields; 409 while paired | — (start now) |
| 2 | **H1** | `docs/ops/backup.md` runbook plus one verified synthetic restore in `holdfast-sandbox` | H2 (#89) |
| 3 | **C3e** | `RuleService`, `/api/rules`, ingest-time rules in import and `IngestService` | C3b, C3d, P2b |
| 4 | **C4c** | M3 transfer models and writer changes, `pair_transfers` v2 (**migration token**) | C3b merged, token |
| 5 | **C4d** → **C4e** | `ledger.refresh` job steps 0–2; transfer endpoints | C4c; C6a (merged) |
| 6 | **C5b** → **C5d** | M4 recurring store and job step 3 (**token**); recurring endpoints | C5c (merged), C4d |
| 7 | **B1**, **B2**, **B4** | Budget status service; metrics `v_*` views + matview refresh; alerts engine | K-P5 contract, C5b |

## Done toward 0.1.0-beta.1

Phase 1.5: F1–F10 (see STATUS). Phase 2: contract, P1, P4a, P2m, P2a, P4-link, P5a, P9b-link.
Phase 3: S0, I1, I2, I5. Phase 4: C1, C2, C3a, C3c, C4/C4b, C5a/C5c, C6a, W1.
