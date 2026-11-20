#!/usr/bin/env python3
"""Decide whether the backend and web CI jobs run (ADR 0013).

Pull requests and non-main pushes are path-gated. A push to main skips
both jobs only when the single new commit is a merged PR whose head
already contained ``before``. Anything uncertain runs both jobs.
Prints ``GITHUB_OUTPUT`` keys on stdout.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

BACKEND_EXACT = {
    ".github/workflows/ci.yml",
    ".github/audit-allowlist.yml",
    "scripts/audit_gate.py",
    "scripts/ci_plan.py",
    "scripts/ci_required_eval.py",
}
WEB_EXACT = {
    ".github/workflows/ci.yml",
    ".github/audit-allowlist.yml",
    "scripts/audit_gate.py",
    "scripts/ci_plan.py",
    "scripts/ci_required_eval.py",
}
BACKEND_PREFIXES = ("backend/", "infra/postgres/")
WEB_PREFIXES = ("web/",)


@dataclass(frozen=True)
class Plan:
    backend: bool
    web: bool
    backend_reason: str
    web_reason: str


def affected(paths: list[str], kind: str) -> bool:
    exact = BACKEND_EXACT if kind == "backend" else WEB_EXACT
    prefixes = BACKEND_PREFIXES if kind == "backend" else WEB_PREFIXES
    # A path that still starts with a quote was C-quoted by git: never trust it, run the job.
    return any(
        path.startswith('"') or path in exact or path.startswith(prefixes) for path in paths
    )


def plan(*, event: str, ref: str, paths: list[str] | None, uptodate: bool | None) -> Plan:
    """``uptodate`` True means the PR head's history contains ``before``."""
    if event == "push" and ref == "refs/heads/main":
        if uptodate is True:
            return Plan(False, False, "main-uptodate", "main-uptodate")
        reason = "run" if uptodate is False else "unknown"
        return Plan(True, True, reason, reason)
    if paths is None:
        return Plan(True, True, "unknown", "unknown")
    backend = affected(paths, "backend")
    web = affected(paths, "web")
    return Plan(
        backend,
        web,
        "run" if backend else "paths",
        "run" if web else "paths",
    )


def emit(decision: Plan) -> str:
    def flag(value: bool) -> str:
        return "true" if value else "false"

    lines = [
        f"backend={flag(decision.backend)}",
        f"web={flag(decision.web)}",
        f"backend_reason={decision.backend_reason}",
        f"web_reason={decision.web_reason}",
    ]
    return "\n".join(lines) + "\n"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        errors="surrogateescape",
    )


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool | None:
    if not ancestor or not descendant or set(ancestor) <= {"0"} or set(descendant) <= {"0"}:
        return None
    proc = _git(repo, "merge-base", "--is-ancestor", ancestor, descendant)
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    return None


def name_status_paths(repo: Path, range_expr: str) -> list[str] | None:
    """Changed paths (both sides of renames and copies); None when git or parsing fails.

    ``-z`` with ``core.quotePath=false`` gives raw NUL-separated names, so non-ASCII
    and quote characters cannot hide a path from ``affected``. ``None`` means unknown,
    which plans both jobs.
    """
    proc = _git(
        repo,
        "-c",
        "core.quotePath=false",
        "diff",
        "-z",
        "--name-status",
        "--find-renames",
        "--find-copies",
        range_expr,
    )
    if proc.returncode != 0:
        return None
    tokens = proc.stdout.split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()
    paths: list[str] = []
    i = 0
    while i < len(tokens):
        status = tokens[i]
        n = 2 if status[:1] in ("R", "C") else 1
        if not status or status[0] not in "ACDMRTUX":
            return None
        group = tokens[i + 1 : i + 1 + n]
        if len(group) != n or not all(group):
            return None
        paths.extend(group)
        i += 1 + n
    return paths


def compute_paths(
    repo: Path,
    *,
    event: str,
    before: str,
    after: str,
    pr_base: str,
    pr_head: str,
) -> list[str] | None:
    """Changed paths, or None when the diff is missing or the branch is behind."""
    if event == "workflow_dispatch" or event not in {"pull_request", "push"}:
        return None
    if event == "pull_request":
        if is_ancestor(repo, pr_base, pr_head) is not True:
            return None
        return name_status_paths(repo, f"{pr_base}...{pr_head}")
    if is_ancestor(repo, before, after) is not True:
        return None
    return name_status_paths(repo, f"{before}..{after}")


def probe_uptodate(
    repo: Path,
    before: str,
    after: str,
    *,
    load_pulls: Callable[[str], list[dict[str, object]]],
    fetch_pr: Callable[[object, str], bool],
) -> bool | None:
    """True/False when proven; None when the push is not a single known PR merge."""
    if is_ancestor(repo, before, after) is not True:
        return None
    counted = _git(repo, "rev-list", "--count", f"{before}..{after}")
    if counted.returncode != 0 or counted.stdout.strip() != "1":
        return None
    try:
        pulls = load_pulls(after)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    merged = [
        pull for pull in pulls
        if pull.get("merge_commit_sha") == after and pull.get("merged_at")
    ]
    if len(merged) != 1:
        return None
    head = merged[0].get("head_sha")
    number = merged[0].get("number")
    if not isinstance(head, str) or not head or number is None:
        return None
    if not fetch_pr(number, head):
        return None
    return is_ancestor(repo, before, head)


def default_load_pulls(repo_name: str, sha: str) -> list[dict[str, object]]:
    proc = subprocess.run(
        [
            "gh", "api", "--paginate", f"repos/{repo_name}/commits/{sha}/pulls",
            "--header", "Accept: application/vnd.github+json",
            "--jq", ".[] | {merge_commit_sha, merged_at, number, head_sha: .head.sha}",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ,
    )
    if proc.returncode != 0:
        raise OSError(proc.returncode)
    rows: list[dict[str, object]] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line and line != "null":
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows


def default_fetch_pr(repo: Path, number: object, head: str) -> bool:
    proc = subprocess.run(
        ["git", "fetch", "--no-tags", "origin", f"pull/{number}/head"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return False
    fetched = subprocess.run(
        ["git", "rev-parse", "FETCH_HEAD"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    return fetched.returncode == 0 and fetched.stdout.strip() == head


def decide(
    repo: Path,
    *,
    event: str,
    ref: str,
    before: str,
    after: str,
    pr_base: str,
    pr_head: str,
    repo_name: str,
    load_pulls: Callable[[str], list[dict[str, object]]] | None = None,
    fetch_pr: Callable[[object, str], bool] | None = None,
) -> Plan:
    uptodate: bool | None = None
    if event == "push" and ref == "refs/heads/main":
        pulls = load_pulls or (lambda sha: default_load_pulls(repo_name, sha))
        fetch = fetch_pr or (lambda number, head: default_fetch_pr(repo, number, head))
        uptodate = probe_uptodate(repo, before, after, load_pulls=pulls, fetch_pr=fetch)
    paths = None
    if not (event == "push" and ref == "refs/heads/main"):
        paths = compute_paths(
            repo, event=event, before=before, after=after, pr_base=pr_base, pr_head=pr_head,
        )
    return plan(event=event, ref=ref, paths=paths, uptodate=uptodate)


def main() -> int:
    decision = decide(
        Path("."),
        event=os.environ.get("EVENT_NAME", ""),
        ref=os.environ.get("REF_NAME", ""),
        before=os.environ.get("BEFORE", ""),
        after=os.environ.get("AFTER", ""),
        pr_base=os.environ.get("PR_BASE", ""),
        pr_head=os.environ.get("PR_HEAD", ""),
        repo_name=os.environ.get("REPO", ""),
    )
    text = emit(decision)
    sys.stderr.write(text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
