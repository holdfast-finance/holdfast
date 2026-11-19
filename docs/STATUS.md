# Holdfast build status

Single source of truth for phase progress. Updated by the `phase-kickoff` and `phase-review` skills.

| Phase | Name | Status | Branch | Report |
|---|---|---|---|---|
| −1 | Repo foundation + Claude Code setup | PASS (2026-11-18) | `main` | this commit |
| 0 | Scaffold | NOT STARTED | | |
| 1 | Auth, household, data model (+ RLS, per-person visibility) | NOT STARTED | | |
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

## Owner to-do (only you can do these; PLAN Section 16)

- [ ] Plaid: sign up, apply for the Trial plan, confirm which products the bundle includes (needed before Phase 2 goes live; sandbox work doesn't need it, but sandbox keys do)
- [ ] Fill in the 10-Item budget (PLAN 3.2). Confirm the HSA custodian and 401(k) login
- [ ] Anthropic API key with a monthly spend limit (Phase 8)
- [ ] Pick the host: a personal machine or VPS, **not** county equipment. Tailscale on server, phone and laptop
- [ ] Domain + DNS API token for Caddy DNS-01 (Phase 8.5). Check that `holdfast` trademark/domain is available
- [ ] Gather statements from 2020 onward (Phase 3). Provide **sanitized** samples for fixtures
- [ ] Pull 3 credit reports from AnnualCreditReport.com (Phase 6)
- [ ] Last year's return + forms for engine validation (Phase 7)
- [ ] Backup target (B2 or similar). Store the restic password + Fernet key in a password manager
- [ ] Create GitHub org **`holdfast-finance`** (free as of 2026-11-18; `holdfast` is taken) and repo `holdfast`, private until the open-source milestone (PLAN 17.6). Optional: also register `holdfastfinance` and `getholdfast` so nobody squats them
- [ ] Check matching domains: `holdfast.finance`, `holdfastfinance.com`, `getholdfast.com`
