# Holdfast (Linux). Windows: ./scripts/dev.ps1 <target> (PowerShell 7). `make` alone prints help.
.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c

COMPOSE      := docker compose -f docker-compose.yml -f docker-compose.dev.yml
COMPOSE_PROD := docker compose -f docker-compose.yml -f infra/docker-compose.prod.yml

.PHONY: help docs-dir up up-prod down logs ps test test-db lint lint-imports fmt migrate rotate-keys rotate-keys-prod seed keys keys-add-missing check health ports env-check sync test-locked release-check dist

help: ## Show this help
	@echo "Holdfast make targets:"
	@grep -E '^[a-z][a-z-]*:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-9s %s\n", $$1, $$2}'

env-check:
	@test -f .env || { echo "error: .env not found. Run 'make keys' to create it from .env.example." >&2; exit 1; }
	@grep -qE '^REDIS_PASSWORD=[A-Za-z0-9_-]+$$' .env || { echo "error: REDIS_PASSWORD is missing (or has characters outside A-Za-z0-9_-) in .env. Run 'make keys-add-missing' (adds it, changes nothing else)." >&2; exit 1; }

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

# Host-side tests reach the dev stack's loopback-published db/redis, but never its data: they
# use a separate `holdfast_test` database (the migration test downgrades to base) and Redis db 15
# (API tests FLUSHDB). TEST_DATABASE_URL is the runtime role (holdfast_app),
# TEST_MIGRATIONS_DATABASE_URL the schema owner (holdfast_owner). Passwords are read from .env
# without echoing them. Pre-set any TEST_* variable to override (then no test db is created).
TEST_DB := holdfast_test

# Create holdfast_test once (idempotent) and apply the same role/grant script as the real db.
test-db:
	@$(COMPOSE) exec -T -e POSTGRES_DB=$(TEST_DB) db sh -c 'createdb -U "$$POSTGRES_USER" "$$POSTGRES_DB" 2>/dev/null; /docker-entrypoint-initdb.d/00-roles.sh >/dev/null'
envval = grep -E '^$(1)=' .env | head -1 | cut -d= -f2-
test: ## Backend pytest (needs `make up` for the db) + web vitest
	@if [ -f backend/pyproject.toml ]; then \
	  if [ -f .env ]; then \
	    if [ -z "$${TEST_DATABASE_URL:-}$${TEST_MIGRATIONS_DATABASE_URL:-}" ]; then \
	      $(MAKE) --no-print-directory test-db; \
	    fi; \
	    if [ -z "$${TEST_DATABASE_URL:-}" ]; then \
	      app_pw=$$($(call envval,POSTGRES_APP_PASSWORD)); \
	      export TEST_DATABASE_URL="postgresql+psycopg://holdfast_app:$${app_pw}@127.0.0.1:5432/$(TEST_DB)"; \
	    fi; \
	    if [ -z "$${TEST_MIGRATIONS_DATABASE_URL:-}" ]; then \
	      owner_pw=$$($(call envval,POSTGRES_OWNER_PASSWORD)); \
	      export TEST_MIGRATIONS_DATABASE_URL="postgresql+psycopg://holdfast_owner:$${owner_pw}@127.0.0.1:5432/$(TEST_DB)"; \
	    fi; \
	    if [ -z "$${TEST_REDIS_URL:-}" ]; then \
	      redis_pw=$$($(call envval,REDIS_PASSWORD)); \
	      export TEST_REDIS_URL="redis://:$${redis_pw}@127.0.0.1:56379/15"; \
	    fi; \
	  fi; \
	  cd backend && uv run pytest -q; \
	else echo "skip backend: no backend/pyproject.toml"; fi
	@if [ -f web/package.json ]; then cd web && pnpm test --run; else echo "skip web: no web/package.json"; fi

lint: api-types-check ## ruff + mypy + import-linter, OpenAPI drift, eslint + tsc
	@if [ -f backend/pyproject.toml ]; then cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy app; else echo "skip backend: no backend/pyproject.toml"; fi
	@$(MAKE) --no-print-directory lint-imports
	@if [ -f web/package.json ]; then cd web && pnpm lint && pnpm typecheck; else echo "skip web: no web/package.json"; fi

lint-imports: ## Layering contracts (ADR 0011): api -> services -> domain, pure domain
	@if [ -f backend/pyproject.toml ]; then cd backend && uv run lint-imports; else echo "skip backend: no backend/pyproject.toml"; fi

fmt: ## Format backend (ruff) and web (format script, if any)
	@if [ -f backend/pyproject.toml ]; then cd backend && uv run ruff format . && uv run ruff check --fix .; fi
	@if [ -f web/package.json ]; then cd web && pnpm run --if-present format; fi

# One-off container with the owner role's URL; api/worker never get those credentials.
migrate: env-check ## alembic upgrade head as holdfast_owner (one-off `migrate` container)
	$(COMPOSE) run --rm migrate

rotate-keys: env-check ## Re-encrypt stored secrets under the primary FERNET_KEY (ADR 0015); run twice
	$(COMPOSE) run --rm --no-deps rotate-keys

rotate-keys-prod: env-check ## rotate-keys against the prod stack (use this on a prod host)
	$(COMPOSE_PROD) run --rm --no-deps rotate-keys

seed: ## Seed Plaid Sandbox data (placeholder until Phase 2)
	python3 scripts/seed_sandbox.py

.PHONY: import-measure
import-measure: ## Aggregate-only parse report over a directory of statements: make import-measure DIR=<dir>
	@test -n "$(DIR)" || { echo "usage: make import-measure DIR=<dir of PDFs or sanitized .txt>"; exit 2; }
	@cd backend && uv run python -m app.importers.harness "$(abspath $(DIR))"

.PHONY: sanitize
sanitize: ## Sanitize real statements offline: make sanitize IN=$$HOME/holdfast-private/inbox OUT=$$HOME/holdfast-private/outbox [REVIEW=1] [KEEP="Word"] [CLEAR=1]
	@test -n "$(IN)" || { echo "usage: make sanitize IN=<dir|zip|file> OUT=<dir> [REVIEW=1] [KEEP=Word]"; exit 2; }
	@for p in "$(abspath $(IN))" $(if $(OUT),"$(abspath $(OUT))"); do \
	  case "$$p/" in "$(CURDIR)"/*) echo "sanitize: $$p is inside the repository. Real statements and sanitizer output stay outside it (ADR 0019). Use an absolute path such as $$HOME/holdfast-private/..." >&2; exit 2;; esac; \
	done
	@cd backend && uv run python -m app.importers.sanitize "$(abspath $(IN))" $(if $(REVIEW),--review,"$(abspath $(OUT))") $(if $(CLEAR),--clear-out) $(foreach w,$(KEEP),--keep "$(w)")

keys: ## Create .env from .env.example with fresh secrets (refuses to overwrite)
	python3 scripts/gen-keys.py --write

keys-add-missing: ## Existing .env: add only the generated values it lacks (REDIS_PASSWORD), keep the rest
	python3 scripts/gen-keys.py --add-missing

check: lint test ## lint + test

sync: ## Fast-forward main to origin/main; on other branches print ahead/behind
	@git fetch origin --quiet
	@if [ "$$(git rev-parse --abbrev-ref HEAD)" = main ]; then git merge --ff-only origin/main; else git rev-list --left-right --count HEAD...origin/main | awk '{print "ahead " $$1 ", behind " $$2 " vs origin/main"}'; fi

release-check: ## Compare product SemVer and Python version. Does not tag or publish.
	python3 scripts/cut_release.py check

dist: ## Build wheel + sdist (holdfast-finance) and SHA256SUMS into ./dist. Does not publish.
	rm -rf dist
	cd backend && uv build --out-dir ../dist
	rm -f dist/.gitignore
	python3 scripts/dist_checksums.py dist

test-locked: ## make test under flock so parallel worktrees share the test DB safely
	@lock="$${XDG_RUNTIME_DIR:-/tmp}/holdfast-make-test.lock"; \
	mkdir -p "$$(dirname "$$lock")"; \
	flock "$$lock" $(MAKE) test

health: ## GET /api/health through Caddy
	curl -fsSk https://localhost/api/health; echo

ports: env-check ## Assert no compose service publishes beyond the rules
	$(COMPOSE) config --format json | python3 scripts/check-compose-ports.py --dev
	docker compose -f docker-compose.yml config --format json | python3 scripts/check-compose-ports.py
	@# Prod needs TAILSCALE_BIND_IP/CF_API_TOKEN; use .env values if set, else CI-style dummies.
	@tip=$$(grep -E '^TAILSCALE_BIND_IP=.+' .env | cut -d= -f2- || true); \
	 cft=$$(grep -qE '^CF_API_TOKEN=.+' .env && echo set || true); \
	 TAILSCALE_BIND_IP=$${tip:-100.64.0.1} \
	 CF_API_TOKEN=$$( [ -n "$$cft" ] && grep -E '^CF_API_TOKEN=' .env | cut -d= -f2- || printf 'x%.0s' $$(seq 40)) \
	 $(COMPOSE_PROD) config --format json | python3 scripts/check-compose-ports.py --prod

.PHONY: api-types api-types-check api-types-test
# Hash of the canonical schema is the first line of schema.d.ts (pytest compares it; CI runs pytest).
# This target also diffs the full openapi-typescript output. No workflow edit: Harbor owns CI.
api-types api-types-check: ## Generate web API types, or fail if schema.d.ts drifted
	@set -eu; tmp=$$(mktemp -d); trap 'rm -rf -- "$$tmp"' EXIT; \
	  (cd backend && uv run --locked python -m scripts.dump_openapi) > "$$tmp/schema.json"; \
	  pnpm --dir web exec openapi-typescript "$$tmp/schema.json" -o "$$tmp/schema.d.ts"; \
	  checksum=$$(sha256sum "$$tmp/schema.json"); \
	  { printf '// holdfast-schema-sha256: %s\n' "$${checksum%% *}"; \
	    cat "$$tmp/schema.d.ts"; } > "$$tmp/out.d.ts"; \
	  if [ "$@" = api-types ]; then \
	    cp "$$tmp/out.d.ts" web/src/api/schema.d.ts; \
	  else \
	    diff -u web/src/api/schema.d.ts "$$tmp/out.d.ts"; \
	  fi

api-types-check: api-types-test

api-types-test: ## Compile the generated types of a synthetic API money field
	@set -eu; tmp=$$(mktemp -d); trap 'rm -rf -- "$$tmp"' EXIT; \
	  (cd backend && uv run --locked python -m tests.test_openapi_export) > "$$tmp/schema.json"; \
	  pnpm --dir web exec openapi-typescript "$$tmp/schema.json" -o "$$tmp/schema.d.ts"; \
	  cp backend/tests/fixtures/openapi-money.ts "$$tmp/openapi-money.ts"; \
	  pnpm --dir web exec tsc --ignoreConfig --noEmit --strict "$$tmp/openapi-money.ts"
