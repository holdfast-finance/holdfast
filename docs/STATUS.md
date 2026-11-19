# Holdfast build status

Single source of truth for phase progress. Updated by the `phase-kickoff` and `phase-review` skills.

| Phase | Name | Status | Branch | Report |
|---|---|---|---|---|
| −1 | Repo foundation + Claude Code setup | PASS (2026-11-18) | `main` | this commit |
| 0 | Scaffold | PASS-WITH-BLOCKED (2026-11-19), merged `phase-0` | `phase/0-scaffold` | [PHASE-0](phases/PHASE-0.md) |
| 1 | Auth, household, data model (+ RLS, per-person visibility) | PASS (2026-11-19), merged `phase-1` | `phase/1-auth-data-model` | [PHASE-1](phases/PHASE-1.md) |
| 1.5 | Foundations + merge gate | NOT STARTED | trunk | |
| 2 | Bank connectors: Plaid sandbox + SimpleFIN + Sync Health | NOT STARTED | | |
| 3 | Historical import (+ competitor exports) | NOT STARTED | | |
| 4 | Categorization, transfers, recurring | NOT STARTED | | |
| 5 | Budgets, metrics, forecasts, alerts (+ irregular income) | NOT STARTED | | |
| 10a | Crypto: track | NOT STARTED | | |
| 6 | Credit | NOT STARTED | | |
| 7 | Tax foundation (+ HSA shoebox in core) / 10b crypto tax | NOT STARTED | | |
| 8 | AI advisor (provider abstraction + MCP) | NOT STARTED | | |
| 8.5 | Mobile PWA + private access | NOT STARTED | | |
| 9 | Hardening + public site | NOT STARTED | | |
| 10c | Bitcoin self-custody wallet | NOT STARTED | | |

Statuses: NOT STARTED · IN PROGRESS · PASS · PASS-WITH-BLOCKED · FAIL

## Resume here (checkpoint 2026-11-19)

- **Operating model:** full autonomy, named agent team (`docs/team/ROSTER.md`), trunk-based flow (`docs/process/SDLC.md`).
- **Plan:** `docs/planning/ROADMAP.md`. Q1 finish line = Phase 5 PASS on sandbox + synthetic data.
- **Checkpoint 2026-11-19 07:20 (owner stopped all agents):** see the checkpoint table in `docs/planning/SPRINTS.md`. F4 merged; PR #4 (F1) and #3 (F7) open; F9 and F10 WIP branches pushed. Resume in that table's order: merge F1, finish F7 + apply rulesets, resume F9/F10, then F3, F8.
- **Phase 1** is done: PASS, on `main`, tagged `phase-1`.
- **Queued after Phase 1 (owner decision 2026-11-18, ADR 0009):** two brands. Holo for Holdfast Cloud + mainstream marketing, Direction 01 for self-host. First task after Phase 1: brand-theme plumbing (`data-brand`, Holo tokens/fonts, AA test for both themes). The Cloud marketing site comes when the hosted tier is scheduled.
- **Carry into Phase 2 (money review):** dedupe Plaid against imported rows by `(account_id, posted_date, amount)` (Plaid wins); the import fingerprint hash includes a same-day ordinal; handle pending→posted via `transaction.pending_external_id`; save `tx_cursor` only after its batch commits; upsert on `(account_id, external_id)` and handle `removed`; parse Plaid JSON with `parse_float=Decimal`; negate Plaid signs once in `integrations/plaid/`, with a sign test per product; API amounts are strings, with an OpenAPI test; never sum across currencies; splits are written through one domain service; transfer pairing is service-enforced (2 legs, opposite signs, different accounts); metrics use `COALESCE(splits, parent)` and exclude `is_transfer`.
- **Local env:** `.env` exists (includes Plaid sandbox/prod keys, `PLAID_ENV=sandbox`; never print or commit it). The dev stack is compose project `holdfast` on 127.0.0.1:443 / 5432 / 56379. Subagents must use their own containers and ports.
- **Shell gotchas:** `cp` is interactive (use `command cp -f`), and a bare `ls` can hang.

## Owner to-do

- [ ] Set up Anvil (Codex) and Flint (Grok): machine user + fork + separate OS user, `docs/process/EXTERNAL-AGENTS-SETUP.md` (ADR 0012). They don't start until this is done
- [ ] Rulesets on `main` (F7 `scripts/protect-main.sh`) need GitHub Team on a private repo, or a public repo. Until then `main` is protected only by process
- [ ] **First:** move the production Plaid keys out of the dev `.env` (keep only sandbox keys). Agents build Phase 2 fixture-first until then
- [ ] Read the weekly digest (`docs/digest/`) when convenient. Changes to `.github/`, `.claude/` and `CLAUDE.md` are listed there for veto

- [ ] Phase 0 blocked item: run `./scripts/dev.ps1 keys` and `./scripts/dev.ps1 up` once on a personal Windows machine with PowerShell 7 and Docker Desktop

- [x] Verify `holdfastfinance.app` for GitHub Pages in org settings (takeover protection, docs/ops/domain-email-pages.md)
- [ ] Require 2FA in the `holdfast-finance` org
- [ ] Optional: register `runholdfast.com` + `holdfast-finance.com` as redirects (only you can do these; PLAN Section 16)

- [ ] Plaid: sign up, apply for the Trial plan, confirm which products the bundle includes (needed before Phase 2 goes live; sandbox work doesn't need it, but sandbox keys do)
- [ ] Fill in the 10-Item budget (PLAN 3.2). Confirm the HSA custodian and 401(k) login
- [ ] Anthropic API key with a monthly spend limit (Phase 8)
- [ ] Pick the host: a personal machine or VPS, **not** county equipment. Tailscale on server, phone and laptop
- [ ] Domain + DNS API token for Caddy DNS-01 (Phase 8.5). Check that `holdfast` trademark/domain is available
- [ ] Gather statements from 2020 onward (Phase 3). Provide **sanitized** samples for fixtures
- [ ] Pull 3 credit reports from AnnualCreditReport.com (Phase 6)
- [ ] Last year's return + forms for engine validation (Phase 7)
- [ ] Backup target (B2 or similar). Store the restic password + Fernet key in a password manager
- [x] Create GitHub org **`holdfast-finance`** (free as of 2026-11-18; `holdfast` is taken) and repo `holdfast`, private until the open-source milestone (PLAN 17.6). Optional: also register `holdfastfinance` and `getholdfast` so nobody squats them
- [x] Register **`holdfastfinance.app`** (primary; DNS, email routing and Pages configured, see docs/ops/domain-email-pages.md) + `runholdfast.com` and `holdfast-finance.com` (301 redirects). Unregistered as of 2026-11-18 (ADR 0002). Cloudflare Registrar sells at cost
