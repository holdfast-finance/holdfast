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
# Plain `docker run`, not `compose run`: compose services drop every capability, and chown needs
# CAP_CHOWN.
BACKEND_IMAGE := holdfast-backend:local
docs-dir:
	@mkdir -p data/documents
	$(COMPOSE) build api
	docker run --rm --user 0 --entrypoint chown -v "$(CURDIR)/data/documents:/data/documents" \
	  $(BACKEND_IMAGE) -R $(BACKEND_UID):$(BACKEND_UID) /data/documents

up: env-check docs-dir ## Start the dev stack (hot reload) behind https://localhost
	$(COMPOSE) up -d --build --renew-anon-volumes

up-prod: env-check ## Start the prod stack (DNS-01 cert, binds TAILSCALE_BIND_IP)
	@mkdir -p data/documents
	$(COMPOSE_PROD) build api
	docker run --rm --user 0 --entrypoint chown -v "$(CURDIR)/data/documents:/data/documents" \
	  $(BACKEND_IMAGE) -R $(BACKEND_UID):$(BACKEND_UID) /data/documents
	$(COMPOSE_PROD) up -d --build db redis
	$(COMPOSE_PROD) run --rm migrate
	$(COMPOSE_PROD) up -d --build

down: ## Stop the stack (keeps volumes)
	$(COMPOSE) down --remove-orphans

logs: ## Follow logs (make logs s=api for one service)
	$(COMPOSE) logs -f --tail=200 $(s)

ps: ## List running services
	$(COMPOSE) ps

# Host-side tests reach the dev stack's loopback-published db/redis: TEST_DATABASE_URL as the
# runtime role (holdfast_app), TEST_MIGRATIONS_DATABASE_URL as the schema owner (holdfast_owner).
# Passwords are read from .env without echoing them. Pre-set any TEST_* variable to override.
envval = grep -E '^$(1)=' .env | head -1 | cut -d= -f2-
test: ## Backend pytest (needs `make up` for the db) + web vitest
	@if [ -f backend/pyproject.toml ]; then \
	  if [ -f .env ]; then \
	    if [ -z "$${TEST_DATABASE_URL:-}" ]; then \
	      app_pw=$$($(call envval,POSTGRES_APP_PASSWORD)); \
	      export TEST_DATABASE_URL="postgresql+psycopg://holdfast_app:$${app_pw}@127.0.0.1:5432/holdfast"; \
	    fi; \
	    if [ -z "$${TEST_MIGRATIONS_DATABASE_URL:-}" ]; then \
	      owner_pw=$$($(call envval,POSTGRES_OWNER_PASSWORD)); \
	      export TEST_MIGRATIONS_DATABASE_URL="postgresql+psycopg://holdfast_owner:$${owner_pw}@127.0.0.1:5432/holdfast"; \
	    fi; \
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

# One-off container with the owner role's URL; api/worker never get those credentials.
migrate: env-check ## alembic upgrade head as holdfast_owner (one-off `migrate` container)
	$(COMPOSE) run --rm migrate

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
	$(COMPOSE_PROD) config --format json | python3 scripts/check-compose-ports.py --prod
