# Phase 0: Scaffold

- **Verdict: PASS-WITH-BLOCKED** (2026-11-19)
- Branch: `phase/0-scaffold`, verified at `acbbe58` (CI run 44449736296: all jobs green)
- Built by three parallel subagents (backend, web, infra) against `PHASE-0-contract.md`, then reviewed (security, UI/brand), fixed by two more subagents, and checked independently by the acceptance checker.

## Summary
- **Backend:** FastAPI app factory, `/api/health` (db + redis checks, 503 on failure with no exception text), pydantic-settings with every contract env var (secrets as `SecretStr`), Fernet helpers with key rotation, structured logging that redacts secrets including rendered tracebacks, an arq worker, and an Alembic baseline (`pg_trgm`, `pgcrypto`). Python 3.12 via uv, ruff and mypy `--strict`.
- **Web:** Vite + React 19 + TS strict + Tailwind v4 + shadcn/ui shell in the Direction 01 brand. Desktop sidebar and mobile bottom tabs, routes for every PLAN 11 page, a `<Money>` component that does no float math (decimal.js, string amounts), StatusChip, a live API health indicator, self-hosted fonts and logomark SVGs. AA-checked text tokens are enforced by a test.
- **Infra:** Compose with db, redis, api, worker, web, caddy and a one-off `migrate`. Caddy serves HTTPS on `127.0.0.1:443` only (the prod template binds the Tailscale IP and uses DNS-01). Internal backend network, `cap_drop: ALL` with minimal add-backs, read-only roots, and per-service env lists. Also `Makefile` and `dev.ps1` with matching targets, `gen-keys.py` (writes `.env` at 0600, never echoes secrets), and a port-exposure checker for base/dev/prod.
- **Database security:** admin / owner / app role split (ADR 0007). The runtime role has no superuser, BYPASSRLS or DDL, and a test asserts it.
- **CI (GitHub Actions):** detect, secrets (gitleaks, full history), backend (real Postgres + Redis as the restricted role, alembic up/down/up, pip-audit), web (lint, typecheck, test, build, audit), compose (config, port checks incl. prod, build).

## Acceptance (phase-acceptance-checker, independent run)
| Criterion | Evidence | Status |
|---|---|---|
| `make up` brings everything up | api/db/redis healthy, caddy/web/worker up, no errors in logs | PASS |
| `./scripts/dev.ps1 up` | Parses with 0 errors and `help` runs in a pwsh 7.4 container | **BLOCKED** (needs a Windows/pwsh host with Docker) |
| `/api/health` OK through Caddy HTTPS | `{"status":"ok","version":"0.0.0","checks":{"db":"ok","redis":"ok"}}` | PASS |
| CI green | `acbbe58`: detect, secrets, backend, compose, web all success | PASS |
| No public exposure | `http://localhost` refused. Listeners are only on 127.0.0.1 (443, 5432, 56379). `make ports` ok for base, dev and prod | PASS |
| Security headers / CSP | CSP (+ frame-ancestors, base-uri, object-src), HSTS, DENY, nosniff, no-referrer | PASS |
| Lint + types | ruff, ruff format, mypy strict, eslint (0 warnings), tsc: clean | PASS |
| Tests | backend 44 passed / 0 skipped. Web 89 passed / 2 skipped (built-CSS checks run only when `dist/` exists) | PASS |
| Alembic baseline, health endpoint, React routing, Section 4 layout, gitleaks pre-commit | verified | PASS |

## Reviewer findings and dispositions
**Security reviewer**
- H1, the app ran as a Postgres superuser: **fixed** with the role split (ADR 0007).
- M1, tracebacks weren't redacted: **fixed**, with tests.
- M2, prod didn't force `APP_ENV=prod`: **fixed**, and the port checker asserts it.
- L1 (env scoping), L2 (prod port check in CI), L3 (CSP hardening) and capability dropping: **fixed**.
- **Deferred to Phase 9:** M3 (pin actions by SHA and images by digest, including the caddy-dns plugin version), M4 (run Caddy as non-root), L4 (Redis `requirepass` in dev), L5 (pass only IDs in job payloads, an enforced rule from Phase 2), and Docker secrets for the Postgres passwords.

**UI/brand reviewer**
- Light-theme Signal, Flare and Breach text and dark Deepwater chip text failed AA: **fixed** with separate `*-text` tokens. The worst pair is now 4.67:1, enforced by a test, and BRAND.md is updated.
- Radix scroll-lock `<style>` blocked by the prod CSP: **fixed** with a CSS rule, without loosening the CSP.
- Fonts inlined as `data:` (blocked by CSP), mono font on prose, invisible light skeletons, and the stale Vite dep cache in Docker: **fixed**.
- **Open polish:** disabled primary buttons look washed out in light theme.

**Acceptance checker notes**
- A Cloudflare CLI cache (account id/name, no credentials) had been committed: now untracked and `.cloudflare/` is ignored. It remains in the private repo's history.
- The pre-commit hook failed open without gitleaks: it now fails closed, with `HOLDFAST_SKIP_GITLEAKS=1` as an explicit bypass.

## ADRs this phase
- 0007: Postgres role split (admin / owner / app)

## Blocked: what the owner must do
- Run `./scripts/dev.ps1 keys` then `./scripts/dev.ps1 up` once on a Windows machine with PowerShell 7 and Docker Desktop, and report any error. **Never on county equipment** (PLAN Section 0).

## Known gaps / follow-ups
- Phase 9 hardening list above.
- `docs/runbook.md` is still a stub (Phase 9).
- The web bundle is about 487 kB (156 kB gzip). Route-level code splitting can wait until real pages exist.
- The 2 skipped vitest checks run when `dist/` exists. Consider building before testing in CI so they always run.

## How to demo
```sh
make keys          # first time only: creates .env with fresh secrets
make up            # dev stack with hot reload
make migrate       # alembic as holdfast_owner
make health        # → {"status":"ok",...}
open https://localhost   # accept the local cert; app shell, API status in the sidebar
make test && make lint && make ports
make down          # stop (keeps volumes)
```
