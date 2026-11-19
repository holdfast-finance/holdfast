# Holdfast (Linux). Windows: ./scripts/dev.ps1 <target> (PowerShell 7). `make` alone prints help.
.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c

COMPOSE      := docker compose -f docker-compose.yml -f docker-compose.dev.yml
COMPOSE_PROD := docker compose -f docker-compose.yml -f infra/docker-compose.prod.yml

.PHONY: help docs-dir up up-prod down logs ps test lint fmt migrate seed keys check health ports env-check

help: ## Show this help
	@echo "Holdfast make targets:"
	@grep -E '^[a-z][a-z-]*:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-9s %s\n", $$1, $$2}'

env-check:
	@test -f .env || { echo "error: .env not found. Run 'make keys' to create it from .env.example." >&2; exit 1; }

# The backend image runs as uid 10001; hand it ./data/documents using that same image (as root,
# no extra image pulled). Host-side reads of stored documents then need sudo, by design.
BACKEND_UID := 10001
docs-dir:
	@mkdir -p data/documents
	$(COMPOSE) run --rm --no-deps --user 0 --entrypoint chown api -R $(BACKEND_UID):$(BACKEND_UID) /data/documents

up: env-check docs-dir ## Start the dev stack (hot reload) behind https://localhost
	$(COMPOSE) up -d --build --renew-anon-volumes

up-prod: env-check ## Start the prod stack (DNS-01 cert, binds TAILSCALE_BIND_IP)
	@mkdir -p data/documents
	$(COMPOSE_PROD) build api
	$(COMPOSE_PROD) run --rm --no-deps --user 0 --entrypoint chown api -R $(BACKEND_UID):$(BACKEND_UID) /data/documents
	$(COMPOSE_PROD) up -d --build

down: ## Stop the stack (keeps volumes)
	$(COMPOSE) down --remove-orphans

logs: ## Follow logs (make logs s=api for one service)
	$(COMPOSE) logs -f --tail=200 $(s)

ps: ## List running services
	$(COMPOSE) ps

# Host-side tests reach the dev stack's loopback-published db/redis. The password is read from
# .env without echoing it. Pre-set TEST_DATABASE_URL / TEST_REDIS_URL to override.
test: ## Backend pytest (needs `make up` for the db) + web vitest
	@if [ -f backend/pyproject.toml ]; then \
	  if [ -z "$${TEST_DATABASE_URL:-}" ] && [ -f .env ]; then \
	    pw=$$(grep -E '^POSTGRES_PASSWORD=' .env | head -1 | cut -d= -f2-); \
	    export TEST_DATABASE_URL="postgresql+psycopg://holdfast:$${pw}@127.0.0.1:5432/holdfast"; \
	    export TEST_REDIS_URL="$${TEST_REDIS_URL:-redis://127.0.0.1:56379/0}"; \
	  fi; \
	  cd backend && uv run pytest -q; \
	else echo "skip backend: no backend/pyproject.toml"; fi
	@if [ -f web/package.json ]; then cd web && pnpm test --run; else echo "skip web: no web/package.json"; fi

lint: ## ruff + ruff format --check + mypy, eslint + tsc
	@if [ -f backend/pyproject.toml ]; then cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy app; else echo "skip backend: no backend/pyproject.toml"; fi
	@if [ -f web/package.json ]; then cd web && pnpm lint && pnpm typecheck; else echo "skip web: no web/package.json"; fi

fmt: ## Format backend (ruff) and web (format script, if any)
	@if [ -f backend/pyproject.toml ]; then cd backend && uv run ruff format . && uv run ruff check --fix .; fi
	@if [ -f web/package.json ]; then cd web && pnpm run --if-present format; fi

migrate: ## alembic upgrade head inside the api container
	$(COMPOSE) exec api alembic upgrade head

seed: ## Seed Plaid Sandbox data (placeholder until Phase 2)
	python3 scripts/seed_sandbox.py

keys: ## Create .env from .env.example with fresh secrets (refuses to overwrite)
	python3 scripts/gen-keys.py --write

check: lint test ## lint + test

health: ## GET /api/health through Caddy
	curl -fsSk https://localhost/api/health; echo

ports: env-check ## Assert no compose service publishes beyond the rules
	$(COMPOSE) config --format json | python3 scripts/check-compose-ports.py --dev
	docker compose -f docker-compose.yml config --format json | python3 scripts/check-compose-ports.py
