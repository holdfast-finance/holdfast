#!/usr/bin/env python3
"""Fail if the rendered compose config publishes a port it shouldn't (CLAUDE.md rule 12).

    docker compose [-f ...] config --format json | python3 scripts/check-compose-ports.py [--dev|--prod]

Rules: only `caddy` publishes (plus `db`/`redis` on 127.0.0.1 with --dev), and never on 0.0.0.0 / :: /
an empty host IP (which Docker treats as all interfaces). With --prod, the backend services must
also render APP_ENV=prod (so /api/docs is off whatever .env says). In every mode `redis` must
render `--requirepass` with a non-empty value: the dev override publishes it on loopback, and an
open Redis lets any local process reset lockouts, drop locks or queue jobs.
"""

from __future__ import annotations

import json
import sys

ALLOWED = {"caddy"}
DEV_LOOPBACK = {"db", "redis"}  # host-side tests only, loopback only
WILDCARD = {"", "0.0.0.0", "::", "[::]"}
PROD_APP_ENV = ("api", "worker")


def redis_auth_problem(services: dict[str, dict]) -> str | None:
    redis = services.get("redis")
    if redis is None:
        return None
    command = redis.get("command") or []
    if isinstance(command, str):
        command = command.split()
    if redis.get("entrypoint"):
        return "redis: a custom entrypoint could bypass --requirepass; use the image default"
    indexes = [i for i, arg in enumerate(command) if arg == "--requirepass"]
    if not indexes:
        return "redis: no --requirepass in its command, Redis must require a password"
    for index in indexes:
        if index + 1 >= len(command) or not str(command[index + 1]).strip():
            return "redis: --requirepass has an empty password"
    return None


def main() -> int:
    dev = "--dev" in sys.argv[1:]
    prod = "--prod" in sys.argv[1:]
    config = json.load(sys.stdin)
    problems: list[str] = []
    for name, svc in sorted(config.get("services", {}).items()):
        for port in svc.get("ports", []) or []:
            host_ip = str(port.get("host_ip", ""))
            published = port.get("published")
            label = f"{name}: {host_ip or '*'}:{published}->{port.get('target')}"
            print(label)
            if host_ip in WILDCARD:
                problems.append(f"{label} binds all interfaces")
            elif name in ALLOWED or (dev and name in DEV_LOOPBACK and host_ip == "127.0.0.1"):
                continue
            else:
                problems.append(f"{label} is not allowed to publish")
    redis_problem = redis_auth_problem(config.get("services", {}))
    print(f"redis: auth {'missing' if redis_problem else 'required'}")
    if redis_problem:
        problems.append(redis_problem)
    if prod:
        services = config.get("services", {})
        for name in PROD_APP_ENV:
            env = (services.get(name) or {}).get("environment") or {}
            app_env = env.get("APP_ENV")
            print(f"{name}: APP_ENV={app_env}")
            if app_env != "prod":
                problems.append(f"{name}: APP_ENV must be prod in the prod config, got {app_env!r}")
    for msg in problems:
        print(f"error: {msg}", file=sys.stderr)
    if not problems:
        print("ports ok")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
