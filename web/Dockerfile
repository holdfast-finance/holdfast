# syntax=docker/dockerfile:1

# ---- deps: Node 24 + pnpm (via corepack) with dependencies installed ----
# Also usable as the dev image (`target: deps`, run `pnpm dev --host 0.0.0.0 --port 5173`
# with ./web bind-mounted and node_modules kept in a volume).
FROM node:26-alpine@sha256:6bd9ab43c0ec0190ca7b2d193906cb8b7edf6e7f4afa43ddeccd6452710bffb5 AS deps
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
FROM caddy:2-alpine@sha256:003a97808e781aa9f7e75cc2f43d34d461099031faf5e20faefb9f61126836f0
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv
EXPOSE 80

# OCI image labels. VERSION and REVISION are build args set by the release workflow.
ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="holdfast-web" \
      org.opencontainers.image.description="Holdfast web app: static SPA served by Caddy on an internal port" \
      org.opencontainers.image.source="https://github.com/holdfast-finance/holdfast" \
      org.opencontainers.image.url="https://github.com/holdfast-finance/holdfast" \
      org.opencontainers.image.documentation="https://github.com/holdfast-finance/holdfast#readme" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.vendor="Holdfast" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"
