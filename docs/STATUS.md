# Holdfast build status

Single source of truth for phase progress. Updated by the `phase-kickoff` and `phase-review` skills.

| Phase | Name | Status | Branch | Report |
|---|---|---|---|---|
| −1 | Repo foundation + Claude Code setup | PASS (2026-11-18) | `main` | this commit |
| 0 | Scaffold | PASS-WITH-BLOCKED (2026-11-19), merged `phase-0` | `phase/0-scaffold` | [PHASE-0](phases/PHASE-0.md) |
| 1 | Auth, household, data model (+ RLS, per-person visibility) | PASS (2026-11-19), merged `phase-1` | `phase/1-auth-data-model` | [PHASE-1](phases/PHASE-1.md) |
| 1.5 | Foundations + merge gate | IN PROGRESS | trunk | Merged: F1, F2 (+b, +c: sweeper, claim guard, lock timeout, #69), F3 (+b, +c), F4, F5a (+ ADR 0015), F5d (hf2 envelope, #73), F6, F7 (merge gate), F8 (+b), F9, F10 (+b, +c), ADR 0036 and the worker wrapper (#64, #70, #72), Redis password (#66), alpha release metadata and publishing. Remaining: F7b free-tier main guard (PR #16), F11, F12, acceptance |
| 2 | Bank connectors: Plaid sandbox + SimpleFIN + Sync Health | IN PROGRESS | trunk | Merged: contract (#62), P1 (#71), P4a schema and migration 0011 (#74), P2m matcher (#79), P2a writer verbs (#84), P4-link (#85, #86). Building: P9b-link screen, H2 instance split. Next: H1 backup runbook, P5a Plaid fetch, P4-preview, then M-link (owner) |
| 3 | Historical import (+ competitor exports) | IN PROGRESS | trunk | Merged: S0 sanitizer, I1 PDF import with reconciliation (+ I1b, I1c), I2a pure CSV and OFX/QFX parsers, CSV/OFX import wired into the service (#65), I5b upload API, I5 import wizard and needs-review queue (+ I5c). Remaining: I3 competitor exports, I4 dedupe against Plaid (needs Phase 2 ingest), I6 acceptance, queued follow-ups |
| 4 | Categorization, transfers, recurring | IN PROGRESS | trunk | Merged: C1, C2, C3a, C3c (#68), C4 and C4b (#82), C5a, C5c (#80), C6a category API (#76), W1 Transactions screen (#77, live). Remaining: C3b (needs P2a, now merged) and C3d/C3e rules, A1/A1w accounts, C4c–C4e and W3 transfers, C5b/C5d and W4 recurring, C7 acceptance |
| 5 | Budgets, metrics, forecasts, alerts (+ irregular income) | NOT STARTED | | |
| 10a | Crypto: track | NOT STARTED | | |
| 6 | Credit | NOT STARTED | | |
| 7 | Tax foundation (+ HSA shoebox in core) / 10b crypto tax | NOT STARTED | | |
| 8 | AI advisor (provider abstraction + MCP) | NOT STARTED | | |
| 8.5 | Mobile PWA + private access | NOT STARTED | | |
| 9 | Hardening + public site | NOT STARTED | | |
| 10c | Bitcoin self-custody wallet | NOT STARTED | | |

Statuses: NOT STARTED · IN PROGRESS · PASS · PASS-WITH-BLOCKED · FAIL

## Resume here (checkpoint 2026-11-22, later)

- **Operating model:** full autonomy, named agent team (`docs/team/ROSTER.md`), trunk-based flow (`docs/process/SDLC.md`). Only Keel merges.
- **Plan:** `docs/planning/ROADMAP.md`. Q1 finish line = Phase 5 PASS on sandbox + synthetic data. Sprint log and checkpoints: `docs/planning/SPRINTS.md`.
- **Phases 0 and 1** are done and tagged. Phases 1.5, 2, 3 and 4 are in progress on trunk.
- **Open PRs:** #67 (Phase 11 plan), #16 (F7b), #8 (Codex and Grok onboarding), plus dependency bumps #36 to #38. P9b-link and H2 are being built.
- **Owner instance:** on migration 0011 (head), counts unchanged, tailnet 200. The move into `holdfast-instance` (H2) is a later dump, restore and verify step.
- **The ledger engines are not usable yet.** Rules, recurring and transfers have no wired storage, API or screen. No user-written rule pattern may be saved or run until an ADR picks a linear-time regex engine. See `docs/planning/SPRINTS.md`.
- **Carry into Phase 2 (money review):** dedupe Plaid against imported rows by `(account_id, posted_date, amount)` (Plaid wins); the import fingerprint hash includes a same-day ordinal; handle pending→posted via `transaction.pending_external_id`; save `tx_cursor` only after its batch commits; upsert on `(account_id, external_id)` and handle `removed`; parse Plaid JSON with `parse_float=Decimal`; negate Plaid signs once in `integrations/plaid/`, with a sign test per product; API amounts are strings, with an OpenAPI test; never sum across currencies; splits are written through one domain service; transfer pairing is service-enforced (2 legs, opposite signs, different accounts); metrics use `COALESCE(splits, parent)` and exclude `is_transfer`.
- **Local env:** `.env` exists (includes Plaid sandbox/prod keys, `PLAID_ENV=sandbox`; never print or commit it). The dev stack is compose project `holdfast` on 127.0.0.1:443 / 5432 / 56379. Subagents must use their own containers and ports. A new worktree needs `pnpm install --frozen-lockfile` in `web/` before `make test` or `make lint`.
- **Shell gotchas:** `cp` is interactive (use `command cp -f`), and a bare `ls` can hang.

## Owner to-do

- [ ] Local AI model (ADR 0038, Proposed). Answer O1–O6 in `docs/planning/LOCAL-MODEL-PLAN.md`. Recommended: start with stock weights ($0), set up a Hugging Face org with hardware-key 2FA, sign off on the Apache-2.0 licences, and accept chat-only local AI on a 14–16 GB laptop

- [ ] Borrow or rent a Mac for Docker Desktop and Safari testing (install plan Q3b)
- [ ] Decide whether Holdfast gets its own Cloudflare account with hardware-key 2FA (Q7)
- [ ] Decide who the Windows `.exe` is for, versus pointing newcomers to Holdfast Cloud (N3)
- [ ] Recommended: clear the Tailscale operator setting on the host (ADR 0036), and bind other local projects' published database ports to 127.0.0.1 (a host check found unrelated containers listening on all interfaces)
- [x] Decide (no cost, optional): make the repo public before the OSS milestone. The repo is public now (checked with `gh repo view`). ADR 0013 covers the free-tier guard (PR #16 is still open)
- [ ] **First:** move the production Plaid keys out of the dev `.env` (keep only sandbox keys). Agents build Phase 2 fixture-first until then
- [ ] Read the weekly digest (`docs/digest/`) when convenient. Changes to `.github/`, `.claude/` and `CLAUDE.md` are listed there for veto

- [ ] Phase 0 blocked item: run `./scripts/dev.ps1 keys` and `./scripts/dev.ps1 up` once on a personal Windows machine with PowerShell 7 and Docker Desktop

- [x] Verify `holdfastfinance.app` for GitHub Pages in org settings (takeover protection, docs/ops/domain-email-pages.md)
- [ ] Require 2FA in the `holdfast-finance` org
- [ ] Optional: register `runholdfast.com` + `holdfast-finance.com` as redirects (only you can do these; PLAN Section 16)

- [ ] Plaid: sign up, apply for the Trial plan, confirm which products the bundle includes (needed before Phase 2 goes live; sandbox work doesn't need it, but sandbox keys do)
- [ ] Fill in the 10-Item budget (PLAN 3.2). Confirm the HSA custodian and 401(k) login
- [ ] Anthropic API key with a monthly spend limit (Phase 8)
- [ ] Pick the host: a personal machine or VPS, **not** employer equipment. Tailscale on server, phone and laptop
- [ ] Domain + DNS API token for Caddy DNS-01 (Phase 8.5). Check that `holdfast` trademark/domain is available
- [ ] Gather statements from 2020 onward (Phase 3). Provide **sanitized** samples for fixtures
- [ ] Pull 3 credit reports from AnnualCreditReport.com (Phase 6)
- [ ] Last year's return + forms for engine validation (Phase 7)
- [ ] Backup target (B2 or similar). Store the restic password + Fernet key in a password manager
- [x] Create GitHub org **`holdfast-finance`** (free as of 2026-11-18; `holdfast` is taken) and repo `holdfast`, private until the open-source milestone (PLAN 17.6). Optional: also register `holdfastfinance` and `getholdfast` so nobody squats them
- [x] Register **`holdfastfinance.app`** (primary; DNS, email routing and Pages configured, see docs/ops/domain-email-pages.md) + `runholdfast.com` and `holdfast-finance.com` (301 redirects). Unregistered as of 2026-11-18 (ADR 0002). Cloudflare Registrar sells at cost
