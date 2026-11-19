# CLAUDE.md — Holdfast

> Every account. One root. Your server.

Holdfast is an open-source (AGPL-3.0), self-hosted personal finance platform: banks, cards,
401(k), HSA, brokerage and crypto in one app, with honest forecasts, credit-report tracking,
an optional year-round tax module, and a private AI advisor that runs on the model you choose.

- **Spec:** `PLAN.md` (Sections 0–19, plus **Section 20 amendments, which win on conflict**).
- **Progress:** `docs/STATUS.md`, the single source of truth for which phase is active.
- **Decisions:** `docs/adr/`. Read the ADRs before reopening a settled choice.
- **Brand / UI:** `docs/brand/BRAND.md`.
- **Path-scoped rules:** `.claude/rules/*.md` load automatically when you touch matching files.

## Roles

Holdfast is built by a named agent team (`docs/team/ROSTER.md`). **Keel** (the main session) is the
project manager with full authority on technical decisions; Lattice is the architect; lane agents build;
Bulwark, Sounding and the lane reviewers gate. Record any non-obvious decision as an ADR.
**Full autonomy (owner decision 2026-11-19):** agents plan, build, review, merge, push and release.
The owner is not a gate; they get a weekly digest and the owner queue in `docs/STATUS.md`.

## Workflow (trunk-based, agent-gated)

The full process is `docs/process/SDLC.md`; model choice and token rules are `docs/process/MODEL-ROUTING.md`;
the plan is `docs/planning/ROADMAP.md` and the sprint log `docs/planning/SPRINTS.md`.

1. Keel plans one sprint per usage window, writes a brief per story, and assigns agent + model.
2. One story (or one same-module epic slice) = one short-lived branch off `origin/main` = one PR.
3. A PR merges only through the gate: `ci-required`, one Alembic head, RLS lint, audits, e2e where relevant,
   and SHA-bound reviewer sign-offs by path. **Only Keel merges** (squash).
4. Phases are milestones: Sounding runs acceptance, writes `docs/phases/PHASE-<n>.md`, Keel tags `phase-<n>`.
   No owner stop; escalate only owner-queue items (money, accounts, real data, legal).
5. Estimates are in usage windows, runs and tokens, never human days.

Commits: Conventional Commits (`feat(plaid): …`, `fix(import): …`, `docs: …`, `chore: …`, `test: …`).
Explain *why* in the body. **Attribution:** end every commit with
`Co-Authored-By: <Agent> (Holdfast) <agents@holdfastfinance.app>` and PRs with `— <Agent>, Holdfast`.
Never credit a model vendor. Remote: `holdfast-finance/holdfast` (private). Never force-push or rewrite `main`.

## Commands

First run: `make keys` (creates `.env` with fresh secrets), then `make up && make migrate`.
The app is at https://localhost (self-signed in dev).

| Task | Linux | Windows (pwsh 7) |
|---|---|---|
| Bring the stack up / down | `make up` / `make down` | `./scripts/dev.ps1 up` / `down` |
| All tests | `make test` | `./scripts/dev.ps1 test` |
| Lint + types | `make lint` | `./scripts/dev.ps1 lint` |
| Migrate (one-off `migrate` service, owner role) / seed sandbox | `make migrate` / `make seed` | `./scripts/dev.ps1 migrate` / `seed` |
| Health through Caddy / port-exposure check | `make health` / `make ports` | `./scripts/dev.ps1 health` / `ports` |
| Backend tests | `cd backend && uv run pytest -q` | same |
| Backend lint/types | `cd backend && uv run ruff check . && uv run mypy app` | same |
| Frontend | `cd web && pnpm dev` / `pnpm test` / `pnpm lint` / `pnpm typecheck` | same |
| New migration | `cd backend && uv run alembic revision --autogenerate -m "<msg>"` | same |
| Secret scan | `gitleaks git --staged` (runs in `.githooks/pre-commit`) | same |
| API types | `make api-types` (drift: `make api-types-check`, part of `make lint`) | pytest `tests/test_openapi_export.py` also checks the schema hash |

Tooling: Python **3.12** via `uv` (system Python is 3.14, so don't use it). Node 24 + **pnpm**.
Docker Compose for services.

## Hard rules

1. **Money** is `Decimal` / `NUMERIC(14,2)`. Never float, including in tests, JSON (serialize as
   strings) and TS (use string amounts plus a decimal lib, never `number` arithmetic). Sign
   convention: outflow negative, inflow positive. Plaid is inverted, so convert on ingest.
2. **No hardcoded tax figures.** They live in `backend/app/tax/config/<year>.yaml` with a source
   URL per value. Unknown → `TODO_VERIFY`. A test fails if any remain for the target year.
3. **Never log or print** Plaid/SimpleFIN access tokens, API keys, SSNs or full account numbers.
   Bank tokens, exchange keys and xpubs are Fernet-encrypted at rest. SSNs are never stored.
4. `PLAID_ENV` defaults to `sandbox`. No code path creates production Items without the slot
   guard (max 10, typed confirmation at ≥ 8, never an 11th).
5. **Agent tools are read-only** against whitelisted `v_*` views. Agent writes go only to pending
   or findings tables. Redaction runs before **every** LLM provider, local ones included.
6. **Imports are idempotent.** Every statement reconciles (opening + Σ txns = closing ± $0.01) or
   gets flagged `needs_review`. Never silently accept an unreconciled statement.
7. **Every new module gets tests.** Fixtures are synthetic or sanitized. Real statements, credit
   reports, tax forms and exports never go in the repo.
8. **Migrations go through Alembic only.** No manual schema edits. Every table with household data
   carries `household_id` and an RLS policy. The app connects as `holdfast_app` (no superuser, no
   BYPASSRLS), and migrations run as `holdfast_owner` (ADR 0007). Never give the app role DDL.
9. **No new external service** (API, SaaS, container) without an ADR in `docs/adr/`.
10. Windows scripts target **PowerShell 7** (`pwsh`), not Windows PowerShell 5.
11. **Crypto:** never accept, store, log or transmit seed phrases or private keys. Only xpubs,
    descriptors and addresses. Signing happens on the hardware wallet (client-side
    WebUSB/QR/file). The server only builds and broadcasts PSBTs. Spending stays behind
    `FEATURE_CRYPTO_SPEND=false` and is self-host only. Exchange API keys must be read-only.
12. **Networking:** Caddy binds to the Tailscale IP only. Never add a public port or port-forward.
    The service worker caches the app shell only, never API responses.
13. **No personal assumptions in code.** State, institutions and names belong in household settings
    or `.env`. This codebase ships to strangers (PLAN 17.4, 20.1).
14. **Tax module is isolated** behind `FEATURE_TAX` (`backend/app/tax/`, agent tax tools, Taxes
    page). Never call it a "CPA" in user-facing copy. It's tax-prep and planning software.
    The relief scanner declines evasion (🔴) plainly.

## Style

- **Python:** ruff + mypy `--strict`. FastAPI routers stay thin, and logic goes in `app/domain`. Pydantic v2
  schemas at the edges. SQLAlchemy 2.0 typed `Mapped[]` models. Async where I/O-bound.
- **TypeScript:** `strict`, no `any`, API types generated from OpenAPI (`openapi-typescript`).
  TanStack Query for server state, Zustand for UI state, react-hook-form + zod for forms.
- **UI:** shadcn/ui is the base. Magic UI / Aceternity live only in `web/src/components/magic/`
  and appear only on Dashboard/login, never in dense data views. Mobile-first: test at 375px,
  touch targets ≥ 44px. All money renders in the theme's mono (JetBrains Mono / DM Mono) with tabular figures. Use
  semantic brand tokens from `docs/brand/BRAND.md`, never ad-hoc hex values or brand-hue class
  names. Two brands (ADR 0009): Direction 01 for self-host, Holo for Holdfast Cloud.
- **Copy voice:** self-host surfaces are builder-to-builder, short, confident, a little cypherpunk.
  Cloud surfaces are warm and plain-English (BRAND.md, Holo). Both talk about ownership. Never fear-sell.

## Claude Code setup in this repo

- **Subagents** (`.claude/agents/`, roster in `docs/team/ROSTER.md`): builders `lattice`, `tide`,
  `ledger`, `prism`, `harbor`, `pilot`, `beacon`, `scout`, `gadfly`; gates `security-reviewer` (Bulwark),
  `money-invariants-reviewer`, `tax-config-verifier` (Tally), `crypto-custody-auditor` (Cipher),
  `ui-brand-reviewer`, `phase-acceptance-checker` (Sounding). Frontmatter sets each agent's model and effort.
  Run Bulwark on anything touching auth, tokens, Plaid, the agent, crypto or a protected path.
- **Skills** (`.claude/skills/`): `phase-kickoff`, `phase-review`, `adr`, `tax-year-config`,
  `bank-profile`, `new-migration`.
- **Hooks** (`.claude/hooks/`): `guard-secrets.sh` blocks writes that contain real-looking
  secrets, seed phrases or files under `data/`, `.env` and `secrets/`. `format.sh` formats edited
  Python/TS files once the toolchains exist.
- **Plugins:** pyright-lsp, typescript-lsp, security-guidance, frontend-design, pr-review-toolkit,
  commit-commands, claude-md-management, context7 (library docs), playwright (UI checks).

## Gotchas

- Plaid Trial: 10 Items lifetime. Deleting an Item does **not** free a slot. Relink via update mode.
- Plaid Transactions gives about 24 months, so older history comes from statement import.
- No consumer credit-bureau API exists. Use manual AnnualCreditReport.com PDF import only, with no scraping.
- HWI is winding down (Aug 2026). Never build signing on server-side USB.
- Never run anything on county-owned hardware or over the county VPN (PLAN Section 0).
