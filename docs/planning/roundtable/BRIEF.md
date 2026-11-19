# Round table brief — Quarter plan Q1 (2026-11-19 → 2027-02-18)

Read this first. It replaces re-reading the whole repo. Pull only the PLAN.md sections your lane needs.

## Where we are
- Phase −1, 0, 1: **PASS**, merged to `main`, tagged `phase-1` (2026-11-19). Reports: `docs/phases/PHASE-0.md`, `PHASE-1.md`.
- Built so far: compose stack (db, redis, api, worker, web, caddy), CI, role split, full §5 schema with RLS + per-person visibility, passkey/TOTP auth, household/invites, audit, web shell + auth UI. 532 backend / 191 web tests.
- Sites: holdfastfinance.app (board A, live), Cloud preview (board C, local, engineer deploys).
- Remaining build order (STATUS.md, PLAN §13 as amended by §20.3): **2** Plaid + SimpleFIN + Sync Health → **3** Historical import (+ competitor exports) → **4** Categorization/transfers/recurring → **5** Budgets/metrics/forecasts/alerts → **10a** Crypto track → **6** Credit → **7** Tax foundation + HSA shoebox / **10b** crypto tax → **8** AI advisor (provider abstraction + MCP) → **8.5** Mobile PWA + private access → **9** Hardening + public site → **10c** BTC self-custody wallet.
- Queued first: brand-theme plumbing (`data-brand`, Holo tokens; ADR 0009). Open security lows from Phase 1 (argon2 queue holds DB conn; verify real client IPs reach Caddy).
- Carry into Phase 2: see `docs/STATUS.md` "Carry into Phase 2 (money review)".

## Operating model (owner decision 2026-11-19)
- **Full autonomy.** The owner is out of the loop. Agents plan, build, review, merge, push and release. Phase gates are enforced by agents (Sounding = acceptance, Bulwark = security), not by the owner.
- **Owner-only blockers still exist** (money, accounts, real data): Plaid Trial signup + real Item linking, Anthropic API key, host machine, domain DNS token, real statements/credit reports/tax docs, backup target. Plan so agents never wait on these: sandbox, synthetic fixtures, feature flags, and a clearly listed owner queue.
- Hard rules in `CLAUDE.md` are not negotiable (money as Decimal, no hardcoded tax figures, no secrets logged, Plaid sandbox default + slot guard, read-only agent tools, idempotent imports, RLS on every household table, Tailscale-only networking, crypto self-custody).

## Estimation units (agents, not humans)
Do **not** estimate in human days. Use:
- **Run** = one subagent task carried to a verified commit. Measured Phase 0–1: 28 runs, ~80–170k tokens processed per run, 3–8 min wall clock each; total ~2.1M output tokens, 235M cache-read tokens.
- **Size:** S = 1 run (~80k tok) · M = 2–3 runs (~250k) · L = 4–6 runs (~600k) · XL = must be split.
- **Window** = one 5-hour usage window (we hit the limit once on 2026-11-18 with 3 parallel agents). Plan ~3 parallel runs per window, ~1.5–2M tokens/window.
- Every story also needs its review cost (security/money/UI review ≈ S each) and acceptance cost (≈ S).

## The team
| Name | Role | Default model |
|---|---|---|
| Keel | Project manager / orchestrator (main session) | opus |
| Lattice | Architect, ADRs, cross-cutting design | opus |
| Bulwark | Security reviewer | opus |
| Ledger | Money/domain engineer + money-invariants reviewer | sonnet |
| Tide | Integrations: Plaid, SimpleFIN, importers, bank profiles | sonnet |
| Prism | Frontend + brand reviewer | sonnet |
| Harbor | DevOps, CI/CD, infra, releases | sonnet |
| Sounding | QA, acceptance checker | sonnet |
| Pilot | AI advisor / LLM + MCP engineer | sonnet |
| Cipher | Crypto engineer + custody auditor | opus |
| Tally | Tax config + tax engine verifier | opus |
| Beacon | Docs, sites, release notes | sonnet |
| Scout | Fast search / codebase recon | haiku |
| Gadfly | Red-team challenger in planning | opus |

## Your round-table memo (≤ 700 words, write it to the file path you were given)
1. **Position:** the 3 most important things for this quarter in your lane, and anything in the current order you'd change (with reasons).
2. **Epics + stories** in your lane: `E<n>.<m> title — size (S/M/L) — acceptance in one line — depends on`.
3. **Risks** and how to retire them early (spikes, fixtures, flags).
4. **Challenge** at least one assumption in PLAN.md or this brief that you think is wrong.
5. **Token savers** specific to your lane.
No preamble, no restating this brief.
