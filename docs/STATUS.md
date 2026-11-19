# Holdfast build status

Single source of truth for phase progress. Updated by the `phase-kickoff` and `phase-review` skills.

| Phase | Name | Status | Branch | Report |
|---|---|---|---|---|
| −1 | Repo foundation + Claude Code setup | PASS (2026-11-18) | `main` | this commit |
| 0 | Scaffold | PASS-WITH-BLOCKED (2026-11-19), merged `phase-0` | `phase/0-scaffold` | [PHASE-0](phases/PHASE-0.md) |
| 1 | Auth, household, data model (+ RLS, per-person visibility) | IN PROGRESS (2026-11-19) | `phase/1-auth-data-model` | |
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

- **Active phase:** 1 (auth, household, data model) on branch `phase/1-auth-data-model`. The contract is `docs/phases/PHASE-1-contract.md`.
- **In flight at checkpoint:** two worktree subagents under `.claude/worktrees/`:
  - data-model (models, `0002_core_schema` migration, RLS, `app/db_context.py`, RLS tests)
  - web-auth (setup wizard, passkey login, Household/Security settings, MSW mocks)
  Their branches are named `worktree-agent-*`. If they finished, they have commits. If the session ended mid-run, they may hold partial work.
- **Next steps:**
  1. Check each `worktree-agent-*` branch: `git log phase/1-auth-data-model..<branch>`. Review, run its verification, and merge `--no-ff` into the phase branch. Rerun any workstream that's missing or incomplete, from the contract.
  2. After data-model merges, start **auth-api** (contract "Auth model" + "API contract"): routes, services, sessions, CSRF, rate limits, setup token, audit, plus a Playwright e2e test with a CDP virtual authenticator in `web/e2e/`.
  3. Integrate (`make up && make migrate && make test && make lint && make ports`). Run security-reviewer, money-invariants-reviewer, ui-brand-reviewer and phase-acceptance-checker, write `docs/phases/PHASE-1.md`, update this file, and **stop for owner approval** before merging to main.
- **Local env:** `.env` exists (includes Plaid sandbox/prod keys, `PLAID_ENV=sandbox`; never print or commit it). The dev stack is compose project `holdfast` on 127.0.0.1:443 / 5432 / 56379. Subagents must use their own containers and ports.
- **Shell gotchas:** `cp` is interactive (use `command cp -f`), and a bare `ls` can hang.

## Owner to-do

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
