# Phase 0: integration contract

Three workstreams build Phase 0 in parallel (backend, web, infra). This file is the shared contract. If you need to change it, say so in your report rather than silently diverging.

## Ownership (each workstream edits ONLY its own paths)
| Workstream | Owns |
|---|---|
| backend | `backend/**` |
| web | `web/**`, `brand/**` |
| infra | `docker-compose.yml`, `docker-compose.dev.yml`, `infra/**`, `Makefile`, `scripts/**`, `.env.example`, `.github/workflows/**`, `.pre-commit-config.yaml` |

Nobody edits `CLAUDE.md`, `PLAN.md`, `docs/**` or `.claude/**`. The project engineer integrates those.

## Services (compose service names → internal ports)
| Service | Image source | Port | Notes |
|---|---|---|---|
| `db` | `postgres:16-alpine` | 5432 | DB `holdfast`, user `holdfast`, volume `pgdata` |
| `redis` | `redis:7-alpine` | 6379 | |
| `api` | `backend/Dockerfile` | 8000 | `uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000` |
| `worker` | `backend/Dockerfile` (same image) | none | `arq app.worker.main.WorkerSettings` |
| `web` | `web/Dockerfile` | 80 | prod: final stage `caddy:2-alpine` serving built static files from `/srv` with SPA fallback (`web/Caddyfile`) |
| `caddy` | `caddy:2-alpine` + `infra/caddy/Caddyfile` | 443 | edge proxy: `/api/*` → `api:8000`, everything else → `{$WEB_UPSTREAM}` (default `web:80`; dev `web:5173`) |

- Dev (`docker-compose.dev.yml` override): `api` runs with `--reload` and bind-mounts `./backend`. `web` runs `pnpm dev --host 0.0.0.0 --port 5173` with `./web` bind-mounted. `WEB_UPSTREAM=web:5173`.
- Edge: Caddy site address `{$APP_HOST:localhost}`, `tls internal` in dev. Published port `${CADDY_BIND:-127.0.0.1}:443:443`. **Never `0.0.0.0`** (CLAUDE.md rule 12). The prod template binds to `TAILSCALE_BIND_IP` and uses DNS-01 (PLAN 12.1). Its headers match PLAN 12.1, including the CSP.
- `/api/health` must work through Caddy: `curl -sk https://localhost/api/health`.

## API contract
- `GET /api/health` → 200 `{"status":"ok","version":"0.0.0","checks":{"db":"ok","redis":"ok"}}`. If a dependency is down, return 503 with that check set to `"error"` (never leak exception text).
- OpenAPI at `/api/openapi.json`, docs at `/api/docs` (disabled when `APP_ENV=prod`).
- All routes are under the `/api` prefix.

## Environment variables (names are fixed; values come from `.env`)
`APP_ENV` (dev|prod), `APP_BASE_URL`, `APP_HOST`, `CADDY_BIND`, `TAILSCALE_BIND_IP`, `CF_API_TOKEN`, `SESSION_SECRET`, `FERNET_KEY`, `DATABASE_URL` (`postgresql+psycopg://holdfast:<pw>@db:5432/holdfast`), `POSTGRES_PASSWORD`, `REDIS_URL` (`redis://redis:6379/0`), `DOCUMENT_STORE` (`/data/documents`), `PLAID_ENV` (default `sandbox`), `PLAID_CLIENT_ID`, `PLAID_SECRET_SANDBOX`, `PLAID_SECRET_PRODUCTION`, `PLAID_PRODUCTS`, `PLAID_COUNTRY_CODES`, `PLAID_REDIRECT_URI`, `PLAID_WEBHOOK_URL`, `PLAID_TX_DAYS_REQUESTED`, `SIMPLEFIN_ACCESS_URL`, `AI_PROVIDER`, `ANTHROPIC_API_KEY`, `AGENT_MODEL_REVIEW`, `AGENT_MODEL_CHAT`, `AGENT_MONTHLY_TOKEN_BUDGET`, `OLLAMA_BASE_URL`, `OPENAI_COMPAT_BASE_URL`, `OPENAI_COMPAT_API_KEY`, `SMTP_HOST`, `SMTP_USER`, `SMTP_PASS`, `ALERT_EMAIL`, `NTFY_URL`, `NTFY_TOPIC`, `HOUSEHOLD_STATE`, `HISTORY_START`, `FEATURE_TAX`, `FEATURE_CRYPTO_SPEND`, `FEATURE_PUBLIC_SECTOR`, `WEB_UPSTREAM`.
Backend reads them with pydantic-settings (secrets as `SecretStr`). Missing optional integrations must not crash startup.

## Commands the Makefile / dev.ps1 must expose
`up`, `down`, `logs`, `test`, `lint`, `fmt`, `migrate`, `seed` (no-op placeholder in Phase 0), `keys` (runs `scripts/gen-keys.py`), `check` (lint + test).
- `test` → backend `uv run pytest -q` (needs the db up) + web `pnpm test --run`.
- `lint` → backend `uv run ruff check . && uv run ruff format --check . && uv run mypy app` + web `pnpm lint && pnpm typecheck`.

## Versions
Python 3.12 (uv-managed), Node 24 + pnpm 10, Postgres 16, Redis 7, Caddy 2.
