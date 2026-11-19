# Holdfast backend

FastAPI API + arq worker. Python 3.12, managed by [uv](https://docs.astral.sh/uv/).
Don't use the system Python.

## Setup

```sh
cd backend
uv sync                      # creates .venv from uv.lock (dev group included)
```

Settings come from environment variables, then `../.env` and `backend/.env`
(see `app/config.py` and the repo's `.env.example`). Nothing is required to boot:
`DATABASE_URL` defaults to `postgresql+psycopg://holdfast@db:5432/holdfast` and takes its
password from `POSTGRES_PASSWORD` when the URL has none. `REDIS_URL` defaults to
`redis://redis:6379/0`.

## Run

```sh
uv run uvicorn app.main:create_app --factory --reload --port 8000   # API: /api/health, /api/docs
uv run arq app.worker.main.WorkerSettings                           # worker
uv run alembic upgrade head                                         # migrations
```

Normally you run all of this through `make up` / `./scripts/dev.ps1 up` from the repo root.

## Checks

```sh
uv run ruff check . && uv run ruff format --check . && uv run mypy app
uv run pytest -q
uv run pip-audit
```

Tests block outbound network (see `tests/conftest.py`). Tests marked `services` need a real
Postgres and Redis:

```sh
docker run -d --rm --name hf-test-pg -e POSTGRES_USER=holdfast -e POSTGRES_PASSWORD=test \
  -e POSTGRES_DB=holdfast -p 127.0.0.1:55432:5432 postgres:16-alpine
docker run -d --rm --name hf-test-redis -p 127.0.0.1:56379:6379 redis:7-alpine
TEST_DATABASE_URL=postgresql+psycopg://holdfast:test@127.0.0.1:55432/holdfast \
TEST_REDIS_URL=redis://127.0.0.1:56379/0 uv run pytest -q
docker rm -f hf-test-pg hf-test-redis
```

Without them those tests skip locally. With `CI` set they fail instead, so CI must provide both.

## Layout

`app/main.py` (app factory), `app/config.py` (settings), `app/db.py` (engine, `Base`),
`app/logging.py` (JSON logs + redaction), `app/security/crypto.py` (Fernet at rest),
`app/api/` (routers under `/api`), `app/worker/` (arq), `migrations/` (Alembic).
Empty packages mark where later phases land: `models`, `schemas`, `integrations/plaid`,
`importers`, `domain`, `tax`, `agent`, `notify`.

## Rules that bite

Money is `Decimal`, never `float`. Never log tokens, keys or passwords (the redaction filter is
a backstop, not permission). Schema changes go through Alembic only. See `CLAUDE.md`.
