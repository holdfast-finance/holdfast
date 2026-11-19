# syntax=docker/dockerfile:1

# ---- deps: Node 24 + pnpm (via corepack) with dependencies installed ----
# Also usable as the dev image (`target: deps`, run `pnpm dev --host 0.0.0.0 --port 5173`
# with ./web bind-mounted and node_modules kept in a volume).
FROM node:24-alpine AS deps
ENV CI=true \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
EXPOSE 5173

# ---- build: type-check and bundle the static app ----
FROM deps AS build
COPY . .
RUN pnpm build

# ---- final: Caddy serves /srv on :80 with the SPA fallback ----
FROM caddy:2-alpine
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv
EXPOSE 80
