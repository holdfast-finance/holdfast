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

## Sprint 2 (W2) — Phase 1.5, wave 2 (planned)
F2 job framework · F5 secrets resolver + key rotation · F6 schema-gap migration (schema run) · F11 trunk-model skills + PR template · F12 e2e in CI · Phase 1.5 acceptance (Sounding) · tag `phase-1.5`.

## Standups

```
W0 · done: Phase 1 merged + tagged; round table (7 memos + challenge); roadmap, SDLC, routing, roster, agent defs · doing: — · blocked: none · tokens: ~0.6M output this session
```
