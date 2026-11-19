#!/usr/bin/env python3
"""Run the merge gate locally against a PR head (ADR 0013).

Fetches ``pull/<n>/head`` into a temporary worktree, symlinks the main
checkout's ``.env`` the way ``wt-new.sh`` does, and never reads or prints
that file. Compose config uses a throwaway env and project name
``holdfast-ci-local`` so it does not attach to the dev stack.
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
ASIDE = ".env.ci-local-aside"

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


def link_env(src: Path | None, dest: Path) -> None:
    """Symlink ``src`` to ``dest``. Does not open either file."""
    if dest.exists() or dest.is_symlink():
        return
    if src is None:
        return
    dest.symlink_to(src)


def move_env_aside(worktree: Path) -> bool:
    env_path = worktree / ".env"
    if not (env_path.is_symlink() or env_path.exists()):
        return False
    os.rename(env_path, worktree / ASIDE)
    return True


def restore_env(worktree: Path, stashed: bool) -> None:
    env_path = worktree / ".env"
    if env_path.is_symlink() or env_path.exists():
        env_path.unlink()
    if stashed:
        os.rename(worktree / ASIDE, env_path)


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
    base_env = dict(environ or os.environ)
    rows: list[tuple[str, str]] = []

    def record(name: str, ok: bool) -> None:
        rows.append((name, "pass" if ok else "fail"))

    record("lint", _passed(run(["make", "lint"], worktree, None)))
    record("test-locked", _passed(run(["make", "test-locked"], worktree, None)))
    record("lint-imports", _passed(run(["make", "lint-imports"], worktree, None)))
    alembic = run(["uv", "run", "alembic", "heads"], worktree / "backend", None)
    record("alembic-head", _passed(alembic) and one_head(alembic.stdout))
    leaks = gitleaks_cmd(worktree, base, head, git_common(worktree))
    record("gitleaks", _passed(run(leaks, worktree, None)))

    stashed = move_env_aside(worktree)
    compose_ok = True
    try:
        keys = run([sys.executable, "scripts/gen-keys.py", "--write"], worktree, None)
        if not _passed(keys):
            compose_ok = False
        else:
            env = compose_env(base_env)
            for cmd in compose_cmds():
                if not _passed(run(cmd, worktree, env)):
                    compose_ok = False
                    break
    finally:
        restore_env(worktree, stashed)
    record("compose-config", compose_ok)

    gate_env = dict(base_env)
    gate_env["BASE_SHA"] = base
    gate_env["HEAD_SHA"] = head
    gate_env["LABELS"] = labels
    record("protected-paths", _passed(run(["bash", str(script)], worktree, gate_env)))
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


def main_checkout_env(start: Path) -> Path | None:
    proc = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    candidate = Path(proc.stdout.strip()).parent / ".env"
    if candidate.is_symlink() or candidate.exists():
        return candidate
    return None


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


def execute(pr: str, repo_root: Path | None = None) -> int:
    root = repo_root or Path.cwd()
    slug = repo_slug(root)
    base, head = resolve_pr(pr, slug)
    fetch_pr(root, pr, base, head)
    labels = load_labels(slug, pr)
    script = Path(__file__).resolve().parent / "check-protected-paths.sh"
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
        link_env(main_checkout_env(root), tmp / ".env")
        rows = run_steps(
            worktree=tmp, base=base, head=head, labels=labels, run=default_run, script=script,
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
