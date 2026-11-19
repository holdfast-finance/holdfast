# syntax=docker/dockerfile:1.7
# Holdfast API + worker image. One image, two commands:
#   api:    uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000   (default)
#   worker: arq app.worker.main.WorkerSettings

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ghcr.io/astral-sh/uv:0.13.7 AS uv

# ---------------------------------------------------------------- build: resolve the venv
FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------------------------------------------------------------- runtime: no uv, no dev deps
FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH
RUN groupadd --system --gid 10001 holdfast \
    && useradd --system --uid 10001 --gid holdfast --home-dir /app --shell /usr/sbin/nologin holdfast \
    && mkdir -p /data/documents \
    && chown holdfast:holdfast /data/documents
# The venv lives outside /app so a dev bind mount of ./backend over /app does not hide it.
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder --chown=root:root /app/app /app/app
COPY --from=builder --chown=root:root /app/migrations /app/migrations
COPY --from=builder --chown=root:root /app/alembic.ini /app/pyproject.toml /app/
WORKDIR /app
USER holdfast
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
