# Holdfast work orders

Anvil owns dispatch, dependency tracking, and evidence collection for this queue. Keel owns merges and releases. [Roadmap](ROADMAP.md) and [SDLC](../process/SDLC.md) govern scope and gates.

## Current checkpoint

Cached `origin/main` is `2eae764`. See [STATUS](../STATUS.md) for phase progress
and `docs/planning/TRIAD-BOARD.md` for assignments and sequence. The latest
Keel brief supplies the PR references below. Live PR heads and merge status
remain unverified because GitHub was unreachable during this refresh.

| State | Work |
| --- | --- |
| Merged on the cached baseline or recorded in STATUS | F2, F3, F5a/F5d, F6, F7, alpha publishing, I2 CSV/OFX, P2a, P2m, P4a, P4-link, and P5a (#90) |
| Keel's merge queue per the board | C3b (#91, migration token 0012), C3d (#92), H2 (#89), P9b-link follow-ups (#93), F7b (#16) |
| Next for Anvil | P2b, I3, P4-preview, P4-map, then P5b. Hold I4 until P5b wires Plaid ingest end to end. |
| Foundation work remaining per STATUS | F7b, F11, F12, then Phase 1.5 acceptance |

The wave tables retain scope and historical dependencies. Use the board for
approved overlap, and verify live PR and branch state before dispatch.
One story per branch and PR. Record the assignee, branch, head SHA, tests,
blockers, and independent reviewer verdicts bound to that SHA.

## Wave 1 · Foundations

| Order | Owner | Start when | Deliverable and acceptance evidence |
| --- | --- | --- | --- |
| F7 verification | Harbor, with Anvil tracking | Ready | Inspect a live PR, preferably an existing one. Record the head SHA, conclusions for both required checks, and GitHub's merge eligibility. Include how the gate behaves for a public fork PR. |
| F3 | Lattice for API contract, Prism for web | Ready | Locate and resume the existing worktree. Generate TS from the offline OpenAPI schema, preserve money fields as strings, and prove the drift check fails on a schema change. Stage CI wiring after Harbor's concurrent workflow changes. |
| F2 | Lattice | F1 complete, migration token available | Add the job registry, per-household lock, retries, and RLS-backed run records. Demonstrate duplicate enqueue yields one side effect and malformed payloads are rejected. |
| F5 | Harbor builds, Bulwark designs and reviews | F1 complete, Harbor available | Implement the secret wrapper, resolver, and key rotation. Use synthetic keys to prove rows rotate and a canary stays out of logs. Reserve the migration token if the design needs schema changes. |
| F6 | Tide, with Lattice and Bulwark review | F1 complete, migration token available | Add supersession and import/sync run schema. Show one Alembic head, successful up/down/up, clean RLS lint, and cross-household isolation. |
| F11 | Harbor | F7 verification | Reconcile the existing PR template and phase skills with the merge gate. Check attribution, reviewer evidence, and Keel-only merge ownership. |
| F12 | Harbor and Sounding | F7 verification, Harbor available | Run the first-run Playwright smoke in CI after merge and nightly with isolated synthetic data. Capture a green run under ten minutes and demonstrate red-run reporting and the roadmap's revert workflow. Keel owns any resulting revert merge. |
| Phase 1.5 acceptance | Sounding, with Anvil coordinating | F2, F3, F5, F6, F11, and F12 verified | Run the phase criteria and publish evidence. Keel records milestone acceptance and tags. |

## Wave 2 · Sandbox ingestion

| Order | Owner | Start when | Deliverable and acceptance evidence |
| --- | --- | --- | --- |
| P1 | Tide | Phase 1.5 accepted, F5 and F6 complete | Connector protocol, registry, and fake connector. Run contract tests without live credentials. |
| P2 | Tide, with Lattice contract and Ledger review | P1, F4 | Shared ingest service with idempotent upserts, pending-to-posted transitions, removals, and overlap dedupe. Show deterministic output across 100 randomized replay batches. |
| P3–P4 | Tide, with Bulwark review of Link and slot guard | P1, then P3 before P4 | Submit separate PRs for parsing/sign conversion and Link/slot guard. Show Decimal parsing, token redaction, and tests at 7, 8, and 10 Items. |
| P5–P7 | Tide, with Ledger reviewing product signs | P2 and P3; F2 for scheduling | Separate PRs for cursor-safe transactions, balance/investment/liability snapshots, and poll/relink flow. Demonstrate replay without duplicates after interruption and update-mode recovery in the same sandbox slot. |
| P8–P10 | Tide and Prism, then Bulwark and Sounding | P5 for P8; P4, P8, F3 for P9; all Phase 2a stories for P10 | Submit API and UI separately. Prove stale sync is surfaced, accounts work at 375px, and the slot guard is visible. Record the threat review and sandbox acceptance report. |

## Waves 3–5 · MVP completion

| Order | Owner | Start when | Deliverable and acceptance evidence |
| --- | --- | --- | --- |
| I1–I6 | Tide and Prism, with Bulwark and Sounding gates | Phase 2a acceptance; I1 depends on P2 | Brief normalization and reconciliation, CSV/OFX, competitor imports, overlap dedupe, and review UI as separate stories. Prove replay adds zero duplicates and each statement reconciles within one cent or is flagged for review. |
| C1–C7 | Ledger and Prism, with Sounding owning holdout evidence | Phase 3 acceptance; C1 depends on F1 and F4 | Brief ledger writes, rules, transfers, recurring, and transaction review separately. Prove transfer pairs stay out of spending, at least 95% of planted bills are recovered, and categorization reaches 90% coverage at 95% precision. |
| B1–B6 | Ledger and Prism, with Bulwark for views and Sounding for acceptance | Phase 4 acceptance; follow dependency order in the roadmap | Brief budgets, metrics, forecasting, alerts, and dashboard/PWA individually. Show budget updates within a sync cycle, forecasts beat the specified baseline, and the PWA leaves API responses uncached. Phase 5 PASS closes the MVP. |

Grouped rows are dispatch queues. Before an agent starts a grouped story, Anvil writes its scoped brief using the story ID and dependencies from the roadmap.

## Release and follow-up orders

| Order | Owner | Start when | Deliverable and acceptance evidence |
| --- | --- | --- | --- |
| Release policy and alpha metadata | Beacon and Harbor, with Anvil coordinating | Harbor available without delaying foundation gates | Write the SemVer policy and update package and lock metadata for product version `0.0.0-alpha` and Python version `0.0.0a0`. Build both packages and verify version agreement. Keel owns the tag and release. |
| Release validation and publishing | Harbor, with Bulwark reviewing workflow permissions | Alpha metadata and F12 complete | In a separate protected-path PR, validate tag, package versions, changelog, and build artifacts. Demonstrate a dry run and a rejected version mismatch. Keel owns publishing. |
| F10b and F8b | Prism and Harbor respectively | Capacity after critical-path assignments | Turn remaining review notes in the sprint log into scoped PRs and verify brand tokens and worktree resume behavior independently. |
| Post-MVP iteration | Anvil queues, Keel schedules | Phase 5 PASS | Triage MVP regressions first. Then brief credit, tax, advisor, hardening, and wallet work from the roadmap. Public release readiness follows PLAN §17.6. |

## Dispatch and evidence

- For I3, use synthetic fixtures for Mint, YNAB, Monarch, and Copilot.
  Verify Decimal amounts, outflow-negative signs, and zero new rows on
  re-import. Keel assigned Ledger and Bulwark review. No migration is scoped.
- P2b precedes Helm's C3e in the statement import path. P4-preview precedes
  P4-map in the connections route. Keep those orders when dispatching.
- The next release target is `0.1.0-beta.1`. Its acceptance requires
  Phases 1.5, 2a, 3, 4, and 5 to pass, plus the upgrade and backup/restore
  drills described in the release ladder.
