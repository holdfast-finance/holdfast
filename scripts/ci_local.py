#!/usr/bin/env python3
"""Run the merge gate locally against a PR head (ADR 0013).

Fetches ``pull/<n>/head`` into a temporary worktree and runs the same
commands CI runs. Those commands are fixed in this file: the pull
request's Makefile is never executed. Throwaway keys are written into
the worktree with the trusted ``gen-keys.py`` before any pull-request
code runs. The operator ``.env`` is not read and is not symlinked.
Compose uses project name ``holdfast-ci-local`` so it does not attach
to the dev stack.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

GITLEAKS_IMAGE = (
    "ghcr.io/gitleaks/gitleaks@sha256:"
    "c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f"
)
COMPOSE_PROJECT = "holdfast-ci-local"

Runner = Callable[[list[str], Path, Mapping[str, str] | None], "CmdResult"]


@dataclass
class CmdResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def one_head(stdout: str) -> bool:
    return sum("(head)" in line for line in stdout.splitlines()) == 1


def gitleaks_cmd(worktree: Path, base: str, head: str, git_common: Path) -> list[str]:
    return [
        "docker", "run", "--rm",
        "-v", f"{git_common}:{git_common}",
        "-v", f"{worktree}:/repo",
        "-w", "/repo",
        GITLEAKS_IMAGE,
        "git", "--no-banner", "--redact", "-v",
        f"--log-opts={base}..{head}",
        "/repo",
    ]


def compose_cmds() -> list[list[str]]:
    prod_files = ["-f", "docker-compose.yml", "-f", "infra/docker-compose.prod.yml"]
    dev_files = ["-f", "docker-compose.yml", "-f", "docker-compose.dev.yml"]
    return [
        ["docker", "compose", "config", "-q"],
        ["docker", "compose", *dev_files, "config", "-q"],
        ["docker", "compose", *prod_files, "config", "-q"],
    ]


def compose_env(base: Mapping[str, str]) -> dict[str, str]:
    env = dict(base)
    env["COMPOSE_PROJECT_NAME"] = COMPOSE_PROJECT
    env["TAILSCALE_BIND_IP"] = "100.64.0.1"
    env["APP_HOST"] = "holdfast.example.com"
    env["CF_API_TOKEN"] = "ci-placeholder"
    return env


def build_summary(head: str, base: str, rows: list[tuple[str, str]]) -> str:
    overall = "pass" if rows and all(result == "pass" for _name, result in rows) else "fail"
    lines = [
        "## ci-local",
        "",
        f"Head: `{head}`",
        f"Base: `{base}`",
        "",
        "| Step | Result |",
        "|---|---|",
        *[f"| {name} | {result} |" for name, result in rows],
        "",
        f"Result: {overall}",
    ]
    return "\n".join(lines) + "\n"


def install_throwaway_env(worktree: Path) -> bool:
    """Write a mode-0600 ``.env`` with the trusted key generator.

    Does not follow a symlink. A symlinked ``.env.example`` is refused
    so this never opens the operator env. Runs before any pull-request
    command.
    """
    example = worktree / ".env.example"
    target = worktree / ".env"
    if example.is_symlink() or not example.is_file():
        print("ci-local: .env.example missing or a symlink; refusing", file=sys.stderr)
        return False
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.exists():
        print("ci-local: .env is not a file; refusing", file=sys.stderr)
        return False
    script = Path(__file__).resolve().parent / "gen-keys.py"
    result = default_run(
        [
            "python3", "-I", str(script),
            "--write", "--force",
            "--example", str(example),
            "--output", str(target),
        ],
        worktree,
        None,
    )
    if result.returncode != 0 or target.is_symlink() or not target.is_file():
        print("ci-local: throwaway .env was not created", file=sys.stderr)
        return False
    if (target.stat().st_mode & 0o777) != 0o600:
        print("ci-local: throwaway .env mode is not 0600", file=sys.stderr)
        return False
    return True


def git_common(cwd: Path) -> Path:
    proc = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(proc.stdout.strip())


def _passed(result: CmdResult) -> bool:
    return result.returncode == 0


def run_steps(
    *,
    worktree: Path,
    base: str,
    head: str,
    labels: str,
    run: Runner,
    script: Path,
    environ: Mapping[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Fixed CI commands. Never invokes ``make`` or a file from the pull request's scripts."""
    base_env = dict(environ) if environ is not None else dict(os.environ)
    rows: list[tuple[str, str]] = []
    backend = worktree / "backend"
    web = worktree / "web"

    def record(name: str, ok: bool) -> None:
        rows.append((name, "pass" if ok else "fail"))

    def passed(cmd: list[str], cwd: Path, env: Mapping[str, str] | None = None) -> bool:
        return _passed(run(cmd, cwd, env))

    ruff_ok = passed(["uv", "run", "ruff", "check", "."], backend)
    ruff_ok = ruff_ok and passed(["uv", "run", "ruff", "format", "--check", "."], backend)
    record("ruff", ruff_ok)
    record("mypy", passed(["uv", "run", "mypy", "app"], backend))
    record("pytest", passed(["uv", "run", "pytest", "-q"], backend))
    record("web-lint", passed(["pnpm", "lint"], web))
    record("web-typecheck", passed(["pnpm", "typecheck"], web))
    record("web-test", passed(["pnpm", "test", "--run"], web))
    record("lint-imports", passed(["uv", "run", "lint-imports"], backend))
    alembic = run(["uv", "run", "alembic", "heads"], backend, None)
    record("alembic-head", _passed(alembic) and one_head(alembic.stdout))
    leaks = gitleaks_cmd(worktree, base, head, git_common(worktree))
    record("gitleaks", passed(leaks, worktree))

    compose_ok = True
    env = compose_env(base_env)
    for cmd in compose_cmds():
        if not passed(cmd, worktree, env):
            compose_ok = False
            break
    record("compose-config", compose_ok)

    gate_env = dict(base_env)
    gate_env["BASE_SHA"] = base
    gate_env["HEAD_SHA"] = head
    gate_env["LABELS"] = labels
    record("protected-paths", passed(["bash", str(script)], worktree, gate_env))
    return rows


def default_run(cmd: list[str], cwd: Path, env: Mapping[str, str] | None) -> CmdResult:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        env=None if env is None else dict(env),
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    return CmdResult(proc.returncode, proc.stdout, proc.stderr)


def resolve_pr(pr: str, repo: str) -> tuple[str, str]:
    proc = subprocess.run(
        ["gh", "pr", "view", pr, "--repo", repo, "--json", "baseRefOid,headRefOid"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"ci-local: could not read PR {pr}")
    data = json.loads(proc.stdout)
    return str(data["baseRefOid"]), str(data["headRefOid"])


def fetch_pr(repo_root: Path, pr: str, base: str, head: str) -> None:
    fetch_head = subprocess.run(
        ["git", "fetch", "--no-tags", "origin", f"pull/{pr}/head"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if fetch_head.returncode != 0:
        raise SystemExit(f"ci-local: fetch failed for PR {pr}")
    subprocess.run(
        ["git", "fetch", "--no-tags", "origin", base],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    have = subprocess.run(
        ["git", "cat-file", "-e", f"{head}^{{commit}}"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if have.returncode != 0:
        raise SystemExit("ci-local: PR head is not in the local object store")


def load_labels(repo: str, pr: str) -> str:
    proc = subprocess.run(
        ["gh", "api", "--paginate", f"repos/{repo}/issues/{pr}/labels", "--jq", ".[].name"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"ci-local: could not read labels for PR {pr}")
    names = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return ",".join(names)


def repo_slug(start: Path) -> str:
    proc = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
        cwd=start,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise SystemExit("ci-local: gh repo view failed")
    return proc.stdout.strip()


def execute(pr: str, repo_root: Path | None = None, run: Runner | None = None) -> int:
    root = repo_root or Path.cwd()
    slug = repo_slug(root)
    base, head = resolve_pr(pr, slug)
    fetch_pr(root, pr, base, head)
    labels = load_labels(slug, pr)
    script = Path(__file__).resolve().parent / "check-protected-paths.sh"
    runner = run or default_run
    tmp = Path(tempfile.mkdtemp(prefix="holdfast-ci-local-"))
    added = subprocess.run(
        ["git", "-C", str(root), "worktree", "add", "--detach", str(tmp), head],
        check=False,
        capture_output=True,
        text=True,
    )
    if added.returncode != 0:
        print(added.stderr, file=sys.stderr)
        raise SystemExit("ci-local: could not add a worktree")
    try:
        if not install_throwaway_env(tmp):
            rows = [("throwaway-env", "fail")]
        else:
            rows = run_steps(
                worktree=tmp, base=base, head=head, labels=labels, run=runner, script=script,
            )
    finally:
        subprocess.run(
            ["git", "-C", str(root), "worktree", "remove", "--force", str(tmp)],
            check=False,
            capture_output=True,
            text=True,
        )
    summary = build_summary(head, base, rows)
    sys.stdout.write(summary)
    return 0 if all(result == "pass" for _name, result in rows) else 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv if argv is None else argv)
    if len(args) != 2 or not args[1].isdigit():
        print("usage: ci_local.py <PR>", file=sys.stderr)
        return 2
    return execute(args[1])


if __name__ == "__main__":
    sys.exit(main())
