# 01 — Lattice (architecture + sequencing)

## 1. Position
1. **Land a "Phase 1.5 Foundations" sprint before Phase 2 code.** Phases 2–10 all assume five things that don't exist yet: a settled layer rule (`app/services/` exists beside an empty `app/domain/`), a job framework (arq is a dependency, `worker/main.py` is a stub), generated OpenAPI types (`web/src/api/types.ts` is hand-written with a TODO), a feature-flag layer (no `FEATURE_*` in `config.py`), and a BYOK/secret-settings resolver (§17.4/§20.3). If each phase builds its own version, we get five versions plus merge conflicts.
2. **Critical path:** Foundations → 2 (connector + ingest ledger) → 4a (categorize + transfers) → 5 (metrics) → 8 (advisor reads `v_*` views). Phase 3 importers, 6 credit, 7 tax config, 10a crypto watch-only, and the brand theme each depend only on Foundations and the ingest ledger, so they run as **parallel lanes**, not in sequence.
3. **Reorder/split:**
   - **Split 2:** 2a = `BankConnector` + ingest service + Plaid sandbox (Tide/Ledger); 2b = SimpleFIN + Sync Health UI (Tide/Prism). 2b can run in parallel with 3.
   - **Merge "Plaid wins" dedupe into one ingest service** used by both 2 and 3. Don't build it twice.
   - **Pull 7's tax-config YAML + verifier forward** as a background lane. It's pure data, collides with nothing and is gated by `TODO_VERIFY`.
   - **Move 8.5's PWA shell earlier** (with Phase 5 UI), so every new page gets built at 375px with sheets. Leave the networking half (DNS-01) in the owner queue.
   - **10a (crypto watch-only) is independent of 6.** Run it as a lane, not a phase slot.

## 2. Epics + stories
**E1 Foundations (blocks everything)**
- E1.1 ADR 0011 layering: `api` (thin) → `services` (transactions, auth, audit) → `domain` (pure, no I/O) — S — ADR merged, import-linter contract in CI — none
- E1.2 Job framework: arq worker, a `@job` registry, idempotency keys, a per-household lock, a retry policy and a `job_run` table with RLS — M — test enqueues a job, it runs twice, side-effects happen once — E1.1
- E1.3 OpenAPI→TS generation: `make api-types` dumps the schema offline (no running server), CI fails on drift, money fields are `string` — S — removing `types.ts` still typechecks — none
- E1.4 Flags: `app/flags.py` (env + household override), `/api/flags`, a `useFlag` hook, and a test that every `FEATURE_*` in `.env.example` is registered — S — `FEATURE_TAX=false` returns 404 on tax routes — E1.3
- E1.5 Secret-settings resolver: `get_secret(household, key)` reads `.env` or the Fernet table, with redaction in logs — M — BYOK test for both sources, no plaintext in logs — E1.1
- E1.6 Money primitives: a `Money` type, a Decimal JSON encoder, `parse_float=Decimal` helpers, and a TS decimal lib wrapper — S — property tests, no `float` anywhere (lint rule) — none

**E2 Ingest ledger (shared by 2, 3, 10a)**
- E2.1 `IngestService.upsert(normalized_txns, source)`: fingerprint, `(account_id, external_id)` upsert, pending→posted, `removed`, Plaid-wins dedupe — L — the STATUS carry-in list passes as tests — E1.1, E1.6
- E2.2 Plugin entry points for connectors/importers (§20.3 "3+") — S — a sample plugin loads in a test — E2.1

**E3 Read model for agent + metrics**
- E3.1 `v_*` views + materialized-view refresh job, versioned through Alembic — M — views respect RLS and `summary_only` — E1.2, phase 4

**E4 Cross-cutting UI**
- E4.1 Brand theme plumbing (ADR 0009) — M — AA contrast tests in both brands — none (lane now)
- E4.2 PWA shell + mobile primitives (sheet, bottom nav) — M — Lighthouse installable — E4.1

## 3. Risks
- **File collisions in parallel lanes.** Hot files: `main.py` router registration, `models/__init__.py`, Alembic heads, `config.py`. Fix: routers auto-register by module discovery (E1.1), one migration per lane rebased at merge (**the Alembic multi-head check in CI is mandatory**), and settings split per module.
- **Real Plaid behaviour differs from sandbox.** Retire it with recorded sandbox JSON fixtures plus a "dynamic" replay test. Real linking stays in the owner queue behind the slot guard.
- **Phase 3 has no real statements.** Use synthetic PDFs generated from a template (reportlab) per profile, so reconciliation logic is proven before the owner supplies samples.
- **Running out of usage windows.** Foundations are small (6S/2M). Do them in one window with 3 lanes: E1.1+E1.2, E1.3+E1.4, E1.5+E1.6.

## 4. Challenge
- **"Phase 7 accept: last year's return reproduces within $1" makes the critical path depend on owner documents.** Under full autonomy, gate on IRS worked examples plus synthetic golden returns, and make the real-return check an owner-queue verification, not a phase gate. The same applies to "three real reports parse" in Phase 6 and "matches your real bills" in Phase 4.
- **Phase 4's "≥ 90% after ~200 labels" can't be measured without real data.** Replace it with a synthetic labelled corpus and a stated precision target.
- The §2 diagram shows the agent as a separate service. Keep it **in-process in `api`** (SSE) with the worker running scheduled reviews. A fourth container adds attack surface for no gain.

## 5. Token savers
- Generated OpenAPI types end the hand-sync between `types.ts` and the backend, along with its review loops.
- One `IngestService` saves about one L of duplicated dedupe work across 2, 3 and 10a.
- A per-phase "contract doc" (as in Phase 1) written by Lattice before the lanes fan out means implementers don't re-read PLAN.
- Scout (haiku) does recon. Opus reviewers get only diffs plus the relevant `.claude/rules` file.
- ADR titles go in BRIEF, so agents don't open ADRs to check settled choices.
