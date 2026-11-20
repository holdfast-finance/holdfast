#!/usr/bin/env python3
"""Print this UTC month's billed GitHub Actions minutes as ``used/2000``.

Lists workflow runs with ``gh api`` (``created>=YYYY-MM-01``) and sums
``billable.UBUNTU.total_ms`` from each run's ``/timing`` endpoint.
The total is rounded up to the next minute so the budget is not under-reported.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime

BUDGET = 2000
Runner = Callable[[list[str]], tuple[int, str, str]]


def month_start(now: datetime | None = None) -> str:
    current = now or datetime.now(UTC)
    return current.date().replace(day=1).isoformat()


def parse_ms(text: str) -> int:
    text = text.strip()
    if not text or text == "null":
        return 0
    return int(float(text))


def minutes_used(total_ms: int) -> int:
    if total_ms <= 0:
        return 0
    return (total_ms + 59_999) // 60_000


def format_budget(total_ms: int, budget: int = BUDGET) -> str:
    return f"{minutes_used(total_ms)}/{budget}"


def _runs_cmd(repo: str, start: str, *, filtered: bool) -> list[str]:
    cmd = [
        "gh", "api", "--paginate", f"repos/{repo}/actions/runs",
        "-f", "per_page=100",
    ]
    if filtered:
        cmd.extend(["-f", f"created=>={start}"])
    cmd.extend(["--jq", ".workflow_runs[] | [.id, .created_at] | @tsv"])
    return cmd


def select_ids(text: str, start: str) -> list[str]:
    ids: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        run_id, created = line.split("\t", 1)
        if created[:10] >= start:
            ids.append(run_id.strip())
    return ids


def _run(cmd: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True, env=os.environ)
    return proc.returncode, proc.stdout, proc.stderr


def compute(run: Runner, repo: str, start: str) -> tuple[str, list[str]]:
    code, out, _err = run(_runs_cmd(repo, start, filtered=True))
    if code != 0:
        code, out, _err = run(_runs_cmd(repo, start, filtered=False))
        if code != 0:
            raise SystemExit("ci-minutes: could not list workflow runs")
    warnings: list[str] = []
    total = 0
    parsed = 0
    ids = select_ids(out, start)
    for run_id in ids:
        code, timing, _err = run(
            [
                "gh", "api", f"repos/{repo}/actions/runs/{run_id}/timing",
                "--jq", ".billable.UBUNTU.total_ms // 0",
            ]
        )
        if code != 0:
            warnings.append(f"ci-minutes: timing unavailable for run {run_id}")
            continue
        total += parse_ms(timing)
        parsed += 1
    if parsed and total == 0:
        warnings.append(
            f"ci-minutes: {parsed} run(s) reported 0 billed ms; "
            "billable totals may not be filled in yet"
        )
    return format_budget(total), warnings


def repo_name(run: Runner) -> str:
    code, out, _err = run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"]
    )
    if code != 0 or not out.strip():
        raise SystemExit("ci-minutes: gh repo view failed")
    return out.strip()


def main() -> int:
    start = month_start()
    repo = repo_name(_run)
    text, warnings = compute(_run, repo, start)
    for warning in warnings:
        print(warning, file=sys.stderr)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
