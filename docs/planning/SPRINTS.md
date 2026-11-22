# Sprint log

Keel keeps this file. One sprint is one usage window. Standup format is in `docs/process/SDLC.md` §5.

## Sprint 1 (W1) — Phase 1.5 Foundations + Gate, wave 1

Goal: the merge gate exists and the layering is settled, so every later run is cheaper and safer. Measure the per-run usage delta to recalibrate sizes.

| # | Story | Agent · model | Parallel wave | Status |
|---|---|---|---|---|
| 1 | Owner queue #1: move production Plaid keys out of the dev `.env` | Owner | — | waiting (non-blocking) |
| 2 | F7 Merge gate (ci-required, one head, RLS lint, blocking audits, pinned actions, protected paths) + rulesets on `main` | Harbor · sonnet | A | todo |
| 3 | F1 ADR 0011 layering + auto-registering routers + import-linter | Lattice · opus | A | todo |
| 4 | F4 Money primitives + float lint | Ledger · sonnet | A | todo |
| 5 | F9 Security lows: argon2 before DB session, real client IP | Bulwark · opus | B | todo |
| 6 | F3 OpenAPI → TS generation, drop hand-written types | Lattice + Prism · sonnet | B | todo |
| 7 | F10 Brand-theme plumbing | Prism · sonnet | B | todo |
| 8 | F8 Worktree hygiene (`wt-new.sh`, `make sync`) | Harbor · sonnet | B | todo |
| 9 | T1 Tax-year config TY2025/TY2026 (stretch, only if a wave slot is idle) | Tally · opus | — | todo |
| 10 | Log per-run usage; recalibrate S/M/L in MODEL-ROUTING.md | Keel | end | todo |

### Checkpoint 2026-11-19 07:20 — all agents stopped by the owner

| Story | Branch · head | PR | State | Next step |
|---|---|---|---|---|
| F4 money primitives | merged `434ce3b` | #2 | **DONE** (money review APPROVE-WITH-NOTES on 9f74d16) | — |
| F1 layering | `chore/F1-layering` · eeebcb7 | #4 | Bulwark APPROVE on 7ecb938; main merged in (pyproject/uv.lock conflict resolved by Keel); CI was re-running | CI green → Bulwark confirms delta eeebcb7 is merge-only → Keel squash-merges |
| F7 merge gate | `chore/F7-merge-gate` · ca3ecaa | #3 | Bulwark BLOCK on 4ff95bc; Harbor pushed fixes ca3ecaa, then was stopped mid-run | Check ca3ecaa covers all 6 items in the BLOCK; finish leftovers; Bulwark re-review; add `bulwark-approved` label; merge; run `scripts/protect-main.sh` |
| F9 security lows | `fix/F9-security-lows` · 8a8c3e9 (WIP) | — | Argon2-outside-session change + test written, **not verified**; ops doc + ADR note not written | Resume: verify, finish docs, open PR, Bulwark-independent review |
| F10 brand theme | `feat/F10-brand-theme` · ac83c12 (WIP) | — | Holo tokens/fonts working per agent; **not verified**, no screenshots committed | Resume: lint/typecheck/test/build, screenshots, open PR, Prism-review |
| F3, F8, T1 | — | — | not started | Sprint 1 wave C |

Process notes from W1:
- Worktree agents can't run DB tests without the main `.env`: use `ln -s <repo>/.env .env` (never read) + `COMPOSE_PROJECT_NAME=holdfast` + `flock /tmp/holdfast-make-test.lock make test`. Bake into briefs.
- Parallel PRs that each add a dev dependency conflict on `pyproject.toml`/`uv.lock`: resolve by keeping both and `uv lock`.
- F9 WIP commit was made with `--no-verify` at checkpoint (Keel); gitleaks run manually on it: no leaks. Don't repeat.
- Measured runs (subagent tokens reported by harness): memos 51–75k; builds 63–110k; reviews 52–62k. Recalibrate S ≈ 60–110k.

### Checkpoint 2026-11-19 08:30 — weekly usage limit hit on all accounts

| Story | PR · head | State | Next step |
|---|---|---|---|
| F1 layering | #4 merged | DONE | — |
| F10 brand theme | #6 merged | DONE (Prism APPROVE-WITH-NOTES). Squash subject landed as "wip(...)" by mistake | F10b: 5 Prism notes (Watch-only Lavender vs Indigo, token parity tests, first-paint `data-brand` in index.html, Money.tsx comment, setHouseholdBrand TODO) |
| F8 worktrees | #7 merged | DONE (Bulwark APPROVE-WITH-NOTES) | F8b: `--resume` must not `-B` reset, `git check-ref-format`, lock path under XDG_RUNTIME_DIR, pwsh lock waits |
| F7 merge gate | #3 merged (fc5d0fa) | **DONE** (Bulwark APPROVE on aa07c9b). Round 3 fixes pushed (quoted paths, fetch auth, `edited`, digests). Bulwark re-review of 8598ea8..aa07c9b dispatched, may not have finished | Bulwark verdict → `bulwark-approved` → merge → `protect-main.sh` (needs paid plan, owner queue) |
| F9 security lows | #9 merged (0186ad6) | **DONE**. Bulwark APPROVE on 8deb121 with low+nit; both fixed in a8b0675. Delta re-review dispatched, may not have finished | Bulwark delta verdict → merge |
| Anvil/Flint onboarding | #8 · 49e024e | Bulwark BLOCK on 4621ba4; reworked to machine user + fork (owner choice). Re-review died on the limit | Bulwark re-review → merge. Owner runs docs/process/EXTERNAL-AGENTS-SETUP.md |
| F3 OpenAPI types | `feat/F3-openapi-types` (wt-new) | Lattice started, died on the limit; work may be partial in its worktree | Resume Lattice in that worktree |

Notes: Codex ran its own setup in the main checkout (uncommitted `AGENTS.md` edit, `.codex/`, `.agents/`). Untouched by Keel; reconcile with #8 (which owns AGENTS.md) next window, and read `.codex/hooks/patch.py` before committing any of it.

### Checkpoint 2026-11-20 — owner stop and usage limit

`main` was at `0633604`. Merged in this window: #18, #21, #17, #19, #20, #22, #23, #25, #24, #26.

| Story | PR | State | Next step |
|---|---|---|---|
| F3c MoneyStr, PriceStr, QuantityStr | #27 | money review APPROVE, then merged `2b4a61e` | — |
| F7b free-tier main guard | #16 | open, Harbor fixed Bulwark's empty-listing finding | Bulwark re-review, then land |
| S0 statement sanitizer | #29 | merged `7916e34` | — |
| F5c secret resolver and rotation | #31 | open, conflicted with main at the stop | merge main, rerun suite, Bulwark |
| F6 import and sync runs | #28 | merged `6a6c8fa` | — |
| F2b job hardening | #32 | open | Bulwark |
| Codex and Grok onboarding | #8 | open, conflicting | reconcile into one PR, Bulwark |
| Dependabot #11 to #14 | — | closed | — |

### Checkpoint 2026-11-21 — `main` at `733132e`

| Story | PR | State | Next step |
|---|---|---|---|
| I1 PDF statement import, I1b, I1c, I5b upload API | #40, #41, #48, #46, #55 | merged | follow-ups below |
| U1 accounts and transactions read API and screen | #42 | merged | — |
| C1 one ledger writer | #44 | merged | — |
| I2a CSV and OFX/QFX parsers (pure) | #45 | merged | wire into the import service |
| C2 category tree and Plaid mapping | #47 | merged | — |
| I5 import wizard, needs-review queue, I5c stored reason | #49, #57 | merged | web follow-ups |
| C5a recurring detection (pure), C5a-2 | #52, #58 | merged | storage, job and screen; off the event loop |
| C3a rules matcher (pure) | #53 | merged | C3b needs an ADR for a linear-time regex engine first |
| C4 transfer pairing | #50 | merged, not wired (a guard test blocks callers) | C6 stores suggestions per person under RLS |
| ADR 0021 zero-config private access | #51 | merged as Proposed | owner checks, then Accepted |
| Phase 4 wiring contract | #59 | open | Gadfly, Bulwark, then slices |
| P3 Plaid client and checklist | #56 | open, other lane | that lane decides |
| F5c, F7b, F2b | #31, #16, #32 | open | as above |

Queued follow-ups:
- Import: freeze `review_reason` in `import_run_guard`; report an ignored profile; an audited re-import action; balance-only documents that stay latched; year ambiguity in periods of 11 months or more.
- Web: copy for a balanced run with no reason; unknown result codes; Retry error text; the shell remount that drops the result card.
- Guard tests for `app.household_wide`; ADR 0021 open points.
- Docs: this refresh. ADR 0031 names the dependencies that had none.

Process notes:
- `firstchair delegate` bases the worktree on the checkout it runs from. Pass `--repo` for work on a PR branch.
- Codex cannot run database tests or `pnpm`. Keel applies its patch and runs the suite. Reviewers rerun with their own data.
- A Grok review is a second opinion. It does not satisfy `review-gate`.

## Sprint 2 (W2) — Phase 1.5, wave 2 (planned)
**First:** F7b free-tier CI (ADR 0013, owner: no paid plans) · resume F3 · Bulwark re-review #8 (first PR to exercise `review-gate`; verify it reports on the head SHA) · F8b, F10b follow-ups.
F2 job framework · F5 secrets resolver + key rotation · F6 schema-gap migration (schema run) · F11 trunk-model skills + PR template · F12 e2e in CI · Phase 1.5 acceptance (Sounding) · tag `phase-1.5`.

## Standups

```
W2b · done: #40 to #58 (I1, I5, C1 to C5a, ADR 0021); phases 3 and 4 in progress on trunk · doing: Phase 4 wiring contract (#59), Plaid P3 (#56) · blocked: C3b needs a regex-engine ADR; live instance upgrade waits on the owner · tokens: Codex about 6.4M input, Claude builders 65k to 175k each
W2a · done: #18 to #27, #29, #28 (F2, F3b/c, F5a, F6, F10b/c, S0) · doing: F5c, F7b, F2b reviews · blocked: weekly limit at the stop · tokens: limit
W1b · done: F1 (#4), F10 (#6), F8 (#7) · doing: F7, F9 reviews; #8 re-review; F3 · blocked: weekly limit (all accounts) · tokens: limit
W1 · done: F4 (#2) · doing: — (stopped by owner) · blocked: none · open: #3 F7, #4 F1, WIP F9/F10 · tokens: ~1.3M subagent this window
W0 · done: Phase 1 merged + tagged; round table (7 memos + challenge); roadmap, SDLC, routing, roster, agent defs · doing: — · blocked: none · tokens: ~0.6M output this session
```
