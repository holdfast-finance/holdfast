#!/usr/bin/env python3
"""Fail if the rendered compose config publishes a port it shouldn't (CLAUDE.md rule 12).

    docker compose [-f ...] config --format json | python3 scripts/check-compose-ports.py [--dev|--prod|--instance|--sandbox]

Rules: only `caddy` publishes (plus `db`/`redis` on 127.0.0.1 with --dev), and never on 0.0.0.0 / :: /
an empty host IP (which Docker treats as all interfaces). With --prod, the backend services must
also render APP_ENV=prod (so /api/docs is off whatever .env says). In every mode `redis` must
render `--requirepass` with a non-empty value: the dev override publishes it on loopback, and an
open Redis lets any local process reset lockouts, drop locks or queue jobs.

The two H2 projects add their own rules. Both: caddy publishes on 127.0.0.1 only and never on 443
(the dev stack's port), and nothing else publishes. --instance (holdfast-instance, real data): the
data mounts (postgres, redis, documents, caddy) are bind mounts under $HOLDFAST_INSTANCE_DIR, an
absolute directory outside the repository, never a named volume that a prune could reach.
--sandbox (holdfast-sandbox, throwaway): api and worker render PLAID_ENV=sandbox,
HOLDFAST_SANDBOX=true and no production secret.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ALLOWED = {"caddy"}
DEV_LOOPBACK = {"db", "redis"}  # host-side tests only, loopback only
WILDCARD = {"", "0.0.0.0", "::", "[::]"}
PROD_APP_ENV = ("api", "worker")
# Where each stateful service keeps data in its container (the instance keeps all of it on bind mounts).
DATA_TARGETS = {
    "db": ("/var/lib/postgresql/data",),
    "redis": ("/data",),
    "api": ("/data/documents",),
    "worker": ("/data/documents",),
    "caddy": ("/data", "/config"),
}
REPO_ROOT = Path(__file__).resolve().parent.parent


def _repo_roots() -> list[Path]:
    """This tree plus the main checkout when run from a linked worktree."""
    roots = [REPO_ROOT]
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        roots.append(Path(common).resolve().parent)
    except (OSError, subprocess.CalledProcessError):
        pass
    return roots


def loopback_problems(services: dict[str, dict]) -> list[str]:
    """H2 projects: caddy on 127.0.0.1 and off 443 (the dev stack's port)."""
    problems = []
    published = [(p, n) for n, svc in services.items() for p in svc.get("ports") or []]
    if not any(n == "caddy" for _, n in published):
        problems.append("caddy publishes no port; the project would be unreachable")
    for port, name in published:
        if str(port.get("host_ip", "")) != "127.0.0.1":
            problems.append(f"{name}: must publish on 127.0.0.1 only")
        if str(port.get("published")) == "443":
            problems.append(f"{name}: published port 443 collides with the dev stack")
    return problems


def instance_problems(services: dict[str, dict], instance_dir: str | None) -> list[str]:
    problems = loopback_problems(services)
    root = Path(instance_dir).resolve() if instance_dir else None
    if root is None or not root.is_absolute():
        problems.append("HOLDFAST_INSTANCE_DIR must be set to an absolute path")
    elif any(root == r or r in root.parents for r in _repo_roots()):
        problems.append(f"{root} is inside the repository (ADR 0019)")
    for name, targets in DATA_TARGETS.items():
        mounts = {v.get("target"): v for v in services.get(name, {}).get("volumes") or []}
        for target in targets:
            mount = mounts.get(target)
            where = f"{name}:{target}"
            if mount is None:
                problems.append(f"{where}: no mount, data would live in the container")
            elif mount.get("type") != "bind":
                problems.append(f"{where}: is a {mount.get('type')} mount, must be a bind mount")
            elif root is not None and Path(str(mount.get("source"))).parent != root:
                problems.append(f"{where}: source is not directly under HOLDFAST_INSTANCE_DIR")
    return problems


def sandbox_problems(services: dict[str, dict]) -> list[str]:
    problems = loopback_problems(services)
    for name in ("api", "worker"):
        env = (services.get(name) or {}).get("environment") or {}
        if env.get("PLAID_ENV") != "sandbox":
            problems.append(f"{name}: PLAID_ENV must render sandbox, got {env.get('PLAID_ENV')!r}")
        if env.get("HOLDFAST_SANDBOX") != "true":
            problems.append(f"{name}: HOLDFAST_SANDBOX must render true")
        if env.get("PLAID_SECRET_PRODUCTION"):
            problems.append(f"{name}: the sandbox must not hold a production secret")
    return problems


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
    instance = "--instance" in sys.argv[1:]
    sandbox = "--sandbox" in sys.argv[1:]
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
    services = config.get("services", {})
    if instance:
        problems += instance_problems(services, os.environ.get("HOLDFAST_INSTANCE_DIR"))
        for name in PROD_APP_ENV:
            env = (services.get(name) or {}).get("environment") or {}
            if env.get("APP_ENV") != "prod":
                problems.append(f"{name}: APP_ENV must be prod in the instance")
    if sandbox:
        problems += sandbox_problems(services)
    if prod:
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
