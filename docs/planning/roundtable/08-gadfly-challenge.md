# 08 — Gadfly: round-2 challenge

## 1. Conflicts and rulings

| # | Conflict | Ruling |
|---|---|---|
| C1 | **Four "Phase 1.5" proposals.** Lattice, Bulwark, Harbor and Prism each propose different contents. | One Phase 1.5, **capped at 2 windows**, with the duplicates merged. **In:** Harbor E1.1, E1.4, E2.1, E2.2; RLS lint (E3.2); Lattice E1.1, E1.2, E1.3 (absorbs Prism E5.1b: Lattice builds the dump, Prism the client); one money story (absorbs Lattice E1.6, Ledger `money.py`, Harbor E3.3 and Bulwark's float rule); one secrets story (Lattice E1.5 + Bulwark E1.2); action pinning and blocking audits **once** (Bulwark E1.3/E2.1 = Harbor E3.5/E3.6); Tide E2.1b schema-gap migration; brand E5.1a. **Out to Phase 2+:** flags (land with the first flag), visual regression, PWA, coverage ratchet, release, deploy, Semgrep beyond the float and secret-logging rules. |
| C2 | **PWA timing.** Prism says the Phase 2 window, Lattice says Phase 5, PLAN says 8.5. | The value is the 375px discipline, not the service worker. Add a "renders at 375px" acceptance line to every page from Phase 2 on. Ship manifest + SW (with the no-`/api` test) alongside the Phase 5 Dashboard. |
| C3 | **PR per story vs phase branches.** | Trunk, PR per **epic slice**, not per S story: same-module S stories go in one PR. Keel is the only merger. Phase = milestone + tag. |
| C4 | **Review scope.** Harbor's gate needs "Bulwark sign-off always", but Bulwark's own memo says it reads only protected-path and new-boundary diffs. | Bulwark's version wins. Security sign-off is path-triggered. |
| C5 | **Ingest size.** Lattice sizes it as one L (E2.1). Tide splits it into M + M + M (E2.1c, E3.1a, E3.1b). | Tide owns and builds it. Lattice writes the contract. Budget ~750k. |
| C6 | **Argon2 and client IP.** Bulwark wants them now; Harbor wants Phase 9. | Now, as one S, plus Harbor's XFF assertion in e2e. |
| C7 | **Tax.** Lattice pulls forward the config only; Specialists pull forward the engine, oracles, corpus and SC. | Config + verifier only (E7.1). See §3. |
| C8 | **Plaid webhooks.** | Poll-first, as Bulwark and Tide both say. No Funnel ADR this quarter. |

## 2. Ranked attacks

1. **The token plan is fantasy, and its unit is wrong.** The memos list ~150 stories. Implementation alone is ~25M tokens (Tide ~4.5M, Ledger ~4.6M, Prism ~5M, Harbor ~3.5M, Specialists ~5.3M, Foundations ~2M). Review + acceptance at ≈S each adds ~24M. **~50M ≈ 25–33 full windows**. "80k per run" is *output* tokens (2.1M ÷ 28); each run also did ~8.4M cache reads, and nobody knows which the limit counts. Phase 1.5 as merged is ~5M, i.e. ~3 windows, not Lattice's one. **Fix:** in the first Phase 1.5 window, log the `/usage` delta per run and recalibrate S/M/L before committing any further scope. Plan for ~50% of the memo stories this quarter.
2. **Production Plaid keys are in `.env` today**, and agents with merge rights read the repo. Bulwark's `.env.agent` is listed as story E1.4, i.e. later. **Fix:** a pre-Phase-1.5 owner-queue item #1: move the prod keys off the dev tree now. Until then, no Phase 2 runs.
3. **The review gate is self-attested.** Every agent uses the same `gh` identity. A head-SHA comment can be posted by anyone, and a workflow-file edit can delete the check. **Fix:** use repository **rulesets** (stored outside the repo tree) for required checks. Changes to `.github/`, `.claude/` and `CLAUDE.md` merge only through a weekly owner digest, the one human touchpoint we keep. Agent sign-off is advisory.
4. **CI minutes, and the strict up-to-date rule, will stall merges.** With 3 parallel PRs, every merge invalidates the other two. Reruns blow past private-repo free minutes. **Fix:** batch PRs (C3). Run e2e on main-merge plus nightly, with auto-revert on red. Turn strict off if a merge queue isn't available on this plan.
5. **`ci.yml` and Alembic are single-writer files with 3 writers.** Seven Phase 1.5 stories edit CI. **Fix:** Harbor alone edits workflows; the other agents ship `make` targets that Harbor wires in during one run. Migrations go through one serialized "schema" run per window.
6. **Synthetic acceptance is circular.** Ledger builds the generator, the harness and the algorithm, then grades itself. Tally's oracles share the author's reading of the rules. **Fix:** Sounding owns the generators and hidden holdout seeds. Real-data checks are listed as *unverified*, not *passed*.
7. **Background lanes eat the critical path.** Tax, crypto and advisor cores "1 run per window" on opus amount to ~⅓ of capacity. **Fix:** see §3. Background lanes run only when the critical-path queue is blocked.
8. **Plaid sandbox fixtures stand in for reality.** **Fix:** one owner-queued real-Item sync dump (redacted), recorded as soon as the Trial exists. Sync Health stays `beta` until it is replayed.

## 3. Cut this quarter

- Phase 3 PDF profiles (E3.2d). Ship CSV, OFX and competitor exports only.
- Phase 6 credit (E6.x), entirely.
- The tax engine, corpus, oracles, SC plugin, relief catalog and HSA (E7.2–7.7). Keep E7.1.
- Crypto E10.4–10.6. Keep E10.1 descriptors and E10.3 lots only if windows are left after Phase 5.
- Advisor E8.3–8.6. Keep E8.1 redaction.
- The ML classifier (E4.7), what-if (E5.5), irregular income (E5.7) and Monte Carlo (E5.8).
- The Starlight docs site and full doc set (E5.3a/b). Keep one docs page per phase.
- Visual regression (E5.1d), release-please/SBOM, host deploy (E6.1–6.2).
- SimpleFIN (E2.3) unless Phase 2a closes early.

**Critical path left:** 1.5 → 2a → 3 (CSV/OFX) → 4 → 5, with brand and E7.1 as the only side lanes.

## 4. The decision Keel must make now

**Set the quarter's finish line and the order in which work gets starved.** Every memo adds parallel lanes. None says what stops when windows run out, and §2.1 says they will. Keel should declare: *Q1 is done when Phase 5 passes acceptance on sandbox and synthetic data. Every non-critical-path lane can be preempted, and is paused automatically whenever the critical path is more than one window behind.* Otherwise eight lanes reach 60% and nothing ships.
