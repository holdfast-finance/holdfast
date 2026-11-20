"""wt-new resume/refname and the shared test-lock path. Stdlib only.

Runs against a temporary git repo with a local origin. Does not fetch holdfast's origin.
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WT_NEW = ROOT / "scripts" / "wt-new.sh"


def _git_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    # Ignore the developer global config so url.insteadOf cannot point this repo at GitHub.
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ALLOW_PROTOCOL"] = "file"
    if extra:
        env.update(extra)
    return env


def git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    cmd = [
        "git",
        "-c",
        "user.name=Harbor",
        "-c",
        "user.email=agents@holdfastfinance.app",
        "-c",
        "protocol.file.allow=always",
        "-c",
        "init.defaultBranch=main",
        *args,
    ]
    run_env = _git_env(env)
    if "GIT_CONFIG_GLOBAL" not in run_env:
        cfg = cwd / ".empty-gitconfig"
        if not cfg.exists():
            cfg.write_text("")
        run_env["GIT_CONFIG_GLOBAL"] = str(cfg)
    result = subprocess.run(cmd, cwd=cwd, env=run_env, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def commit_file(repo: Path, name: str, body: str, env: dict[str, str]) -> str:
    (repo / name).write_text(body)
    git(repo, "add", name, env=env)
    git(repo, "commit", "-qm", f"add {name}", env=env)
    return git(repo, "rev-parse", "HEAD", env=env)


def new_repo(root: Path) -> tuple[Path, dict[str, str]]:
    origin = root / "origin.git"
    repo = root / "repo"
    origin.mkdir()
    repo.mkdir()
    empty = root / "gitconfig"
    empty.write_text("")
    env = {"GIT_CONFIG_GLOBAL": str(empty)}
    git(origin, "init", "-q", "--bare", "-b", "main", env=env)
    git(repo, "init", "-q", "-b", "main", env=env)
    git(repo, "remote", "add", "origin", origin.as_uri(), env=env)
    return repo, env


def run_wt(repo: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(WT_NEW), *args],
        cwd=repo,
        env=_git_env(env),
        capture_output=True,
        text=True,
    )


class WtNewTests(unittest.TestCase):
    def test_bad_refname_rejected_before_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, env = new_repo(Path(tmp))
            commit_file(repo, "README", "base\n", env)
            git(repo, "push", "-q", "origin", "main", env=env)
            for name in ("bad..name", "HEAD", "-leading", "has space"):
                with self.subTest(name=name):
                    result = run_wt(repo, env, name)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn("not a valid refname", result.stderr)
                    self.assertNotIn("fetch", result.stderr.lower())
                    self.assertFalse((repo / ".claude").exists(), result.stderr)

    def test_resume_keeps_unpushed_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, env = new_repo(Path(tmp))
            commit_file(repo, "README", "base\n", env)
            git(repo, "push", "-q", "origin", "main", env=env)
            git(repo, "checkout", "-qb", "feature", env=env)
            git(repo, "push", "-q", "-u", "origin", "feature", env=env)
            unpushed = commit_file(repo, "local-only.txt", "not pushed\n", env)
            git(repo, "checkout", "-q", "main", env=env)
            commit_file(repo, "later.txt", "on main\n", env)
            git(repo, "push", "-q", "origin", "main", env=env)
            remote_tip = git(repo, "rev-parse", "origin/feature", env=env)
            self.assertNotEqual(unpushed, remote_tip)

            result = run_wt(repo, env, "--resume", "feature")
            self.assertEqual(result.returncode, 0, result.stderr)
            worktree = repo / ".claude" / "worktrees" / "feature"
            self.assertEqual(result.stdout.strip(), str(worktree))
            self.assertEqual(git(worktree, "rev-parse", "HEAD", env=env), unpushed)
            self.assertEqual(git(repo, "rev-parse", "refs/heads/feature", env=env), unpushed)
            self.assertIn("1 commit(s) behind origin/main (no ff applied)", result.stderr)

    def test_resume_does_not_fast_forward_local_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, env = new_repo(Path(tmp))
            base = commit_file(repo, "README", "base\n", env)
            git(repo, "push", "-q", "origin", "main", env=env)
            git(repo, "checkout", "-qb", "feature", env=env)
            git(repo, "push", "-q", "-u", "origin", "feature", env=env)
            commit_file(repo, "remote-only.txt", "pushed ahead\n", env)
            git(repo, "push", "-q", "origin", "feature", env=env)
            git(repo, "checkout", "-q", "main", env=env)
            git(repo, "branch", "-f", "feature", base, env=env)
            self.assertNotEqual(git(repo, "rev-parse", "origin/feature", env=env), base)

            result = run_wt(repo, env, "--resume", "feature")
            self.assertEqual(result.returncode, 0, result.stderr)
            worktree = repo / ".claude" / "worktrees" / "feature"
            self.assertEqual(git(worktree, "rev-parse", "HEAD", env=env), base)
            self.assertEqual(git(repo, "rev-parse", "refs/heads/feature", env=env), base)

    def test_resume_creates_local_branch_from_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, env = new_repo(Path(tmp))
            commit_file(repo, "README", "base\n", env)
            git(repo, "push", "-q", "origin", "main", env=env)
            git(repo, "checkout", "-qb", "feature", env=env)
            remote_tip = commit_file(repo, "on-origin.txt", "shared\n", env)
            git(repo, "push", "-q", "-u", "origin", "feature", env=env)
            git(repo, "checkout", "-q", "main", env=env)
            git(repo, "branch", "-D", "feature", env=env)

            result = run_wt(repo, env, "--resume", "feature")
            self.assertEqual(result.returncode, 0, result.stderr)
            worktree = repo / ".claude" / "worktrees" / "feature"
            self.assertEqual(git(worktree, "rev-parse", "HEAD", env=env), remote_tip)
            self.assertEqual(git(repo, "rev-parse", "refs/heads/feature", env=env), remote_tip)


class TestLockPathTests(unittest.TestCase):
    def _fake_flock(self, bindir: Path) -> None:
        flock = bindir / "flock"
        flock.write_text(
            "#!/bin/sh\n"
            'printf "%s\\n" "$1" > "$HOLDFAST_FLOCK_LOG"\n'
            "exit 0\n"
        )
        flock.chmod(flock.stat().st_mode | stat.S_IEXEC)

    def _run_make(self, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["make", "test-locked"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

    def test_lock_path_honors_xdg_runtime_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xdg = root / "xdg-runtime"
            xdg.mkdir()
            bindir = root / "bin"
            bindir.mkdir()
            self._fake_flock(bindir)
            log = root / "flock-log"
            env = os.environ.copy()
            env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
            env["XDG_RUNTIME_DIR"] = str(xdg)
            env["HOLDFAST_FLOCK_LOG"] = str(log)
            env.pop("MAKEFLAGS", None)
            result = self._run_make(env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(log.read_text().strip(), str(xdg / "holdfast-make-test.lock"))

    def test_lock_path_defaults_to_tmp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bindir = root / "bin"
            bindir.mkdir()
            self._fake_flock(bindir)
            log = root / "flock-log"
            env = os.environ.copy()
            env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
            env.pop("XDG_RUNTIME_DIR", None)
            env["HOLDFAST_FLOCK_LOG"] = str(log)
            env.pop("MAKEFLAGS", None)
            result = self._run_make(env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(log.read_text().strip(), "/tmp/holdfast-make-test.lock")

    def test_dev_ps1_uses_same_lock_path_and_waits(self) -> None:
        text = (ROOT / "scripts" / "dev.ps1").read_text()
        self.assertNotIn("GetTempPath", text)
        start = text.index("'test-locked' {")
        block = text[start:]
        self.assertIn("XDG_RUNTIME_DIR", block)
        self.assertIn("holdfast-make-test.lock", block)
        self.assertIn("HOLDFAST_TEST_LOCK_WAIT", block)
        self.assertIn("timed out after", block)
        self.assertNotIn("GetTempPath", block)


if __name__ == "__main__":
    unittest.main()
