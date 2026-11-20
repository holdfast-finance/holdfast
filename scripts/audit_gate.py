#!/usr/bin/env python3
"""Fail on dependency advisories that are not in the expiring allowlist.

usage: audit_gate.py {pip|pnpm} <audit.json> <allowlist.yml>

pip: any advisory from pip-audit JSON blocks (pip-audit reports no severity).
pnpm: advisories of severity high or critical block.
Stdlib only: the allowlist is parsed with a tiny reader for the documented list-of-maps shape.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

MAX_ALLOW_DAYS = 90


def load_allowlist(path: Path, scope: str) -> tuple[set[str], list[str]]:
    entries: list[dict[str, str]] = []
    for raw in path.read_text().splitlines():
        line = re.sub(r"(^|\s)#.*$", "", raw).rstrip()
        if not line.strip() or line.strip() in ("allow:", "allow: []"):
            continue
        m = re.match(r"^\s*(- )?(\w+):\s*(.*)$", line)
        if not m:
            continue
        if m.group(1):
            entries.append({})
        if entries:
            entries[-1][m.group(2)] = m.group(3).strip().strip("\"'")
    allowed: set[str] = set()
    errors: list[str] = []
    for e in entries:
        ident = e.get("id", "").strip()
        if not ident:
            errors.append("allowlist entry has an empty id (it would match any advisory without an id)")
            continue
        try:
            expires = date.fromisoformat(e.get("expires", ""))
        except ValueError:
            errors.append(f"allowlist entry {ident} has no valid expires date")
            continue
        if expires < date.today():
            errors.append(f"allowlist entry {ident} expired on {expires}")
            continue
        if expires > date.today() + timedelta(days=MAX_ALLOW_DAYS):
            errors.append(f"allowlist entry {ident} expires {expires}, more than {MAX_ALLOW_DAYS} days out")
            continue
        if e.get("scope", scope) == scope:
            allowed.add(ident)
    return allowed, errors


def pip_findings(data: dict) -> list[tuple[str, set[str]]]:
    if not isinstance(data, dict) or not isinstance(data.get("dependencies"), list):
        raise ValueError("pip-audit output lacks a 'dependencies' list")
    out = []
    for dep in data.get("dependencies", []):
        for v in dep.get("vulns", []):
            ids = {v["id"], *v.get("aliases", [])}
            out.append((f"{dep['name']} {dep.get('version', '')}: {v['id']}", ids))
    return out


def pnpm_findings(data: dict) -> list[tuple[str, set[str]]]:
    if not isinstance(data, dict) or "error" in data or not isinstance(data.get("advisories"), dict):
        raise ValueError("pnpm audit output lacks an 'advisories' object (or reports an error)")
    out = []
    for adv in data["advisories"].values():
        if adv.get("severity") in ("high", "critical"):
            ids = {str(adv.get("github_advisory_id", "")), *map(str, adv.get("cves", []))} - {""}
            label = f"{adv.get('module_name')}: {adv.get('title')} [{adv.get('severity')}]"
            out.append((label, ids))
    return out


def main() -> int:
    scope, audit, allow = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    allowed, errors = load_allowlist(allow, scope)
    try:
        data = json.loads(audit.read_text())
    except (OSError, ValueError) as exc:
        print(f"::error::could not read audit output {audit}: {exc}")
        return 1
    try:
        findings = pip_findings(data) if scope == "pip" else pnpm_findings(data)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f"::error::malformed {scope} audit output, failing closed: {exc}")
        return 1
    for label, ids in findings:
        if ids & allowed:
            print(f"allowlisted: {label}")
        else:
            errors.append(f"{label} ({', '.join(sorted(i for i in ids if i))})")
    for e in errors:
        print(f"::error::{e}")
    print(f"{scope} audit: {len(findings)} finding(s), {len(errors)} blocking")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
