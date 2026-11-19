#!/usr/bin/env python3
"""Fail if the rendered compose config publishes a port it shouldn't (CLAUDE.md rule 12).

    docker compose [-f ...] config --format json | python3 scripts/check-compose-ports.py [--dev]

Rules: only `caddy` publishes (plus `db`/`redis` on 127.0.0.1 with --dev), and never on 0.0.0.0 / :: /
an empty host IP (which Docker treats as all interfaces).
"""

from __future__ import annotations

import json
import sys

ALLOWED = {"caddy"}
DEV_LOOPBACK = {"db", "redis"}  # host-side tests only, loopback only
WILDCARD = {"", "0.0.0.0", "::", "[::]"}


def main() -> int:
    dev = "--dev" in sys.argv[1:]
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
    for msg in problems:
        print(f"error: {msg}", file=sys.stderr)
    if not problems:
        print("ports ok")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
