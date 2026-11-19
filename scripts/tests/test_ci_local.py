"""ci-local step list, summary, and the .env symlink it must not read."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import ci_local  # noqa: E402

SENTINEL = "SENTINEL_NOT_A_SECRET"
BASE = "a" * 40
HEAD = "b" * 40


def git(cwd: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True)


class LocalGateTests(unittest.TestCase):
    def test_usage(self) -> None:
        self.assertEqual(ci_local.main(["ci_local.py"]), 2)
        self.assertEqual(ci_local.main(["ci_local.py", "nope"]), 2)

    def test_steps_summary_and_env_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "wt"
            worktree.mkdir()
            git(worktree, "init", "-q", "-b", "main")
            (worktree / "README.md").write_text("hi\n")
            git(worktree, "add", "-A")
            git(worktree, "commit", "-qm", "base")
            secret = root / "real.env"
            secret.write_text(SENTINEL + "\n")
            ci_local.link_env(secret, worktree / ".env")
            self.assertTrue((worktree / ".env").is_symlink())
            seen: list[tuple[list[str], dict | None]] = []

            def run(cmd: list[str], cwd: Path, env: dict | None) -> ci_local.CmdResult:
                seen.append((cmd, None if env is None else dict(env)))
                blob = " ".join(cmd)
                self.assertNotIn(SENTINEL, blob)
                self.assertNotIn("-p", cmd)
                self.assertNotIn("--publish", cmd)
                if "gen-keys.py" in blob:
                    aside = worktree / ".env.ci-local-aside"
                    self.assertTrue(aside.is_symlink())
                    self.assertFalse((worktree / ".env").exists())
                if cmd[:2] == ["docker", "compose"]:
                    assert env is not None
                    self.assertEqual(env["COMPOSE_PROJECT_NAME"], "holdfast-ci-local")
                if cmd[:2] == ["docker", "run"]:
                    self.assertIn(f"--log-opts={BASE}..{HEAD}", cmd)
                    self.assertIn(ci_local.GITLEAKS_IMAGE, cmd)
                if cmd and cmd[0] == "bash":
                    assert env is not None
                    self.assertEqual(env["BASE_SHA"], BASE)
                    self.assertEqual(env["HEAD_SHA"], HEAD)
                    self.assertEqual(env["LABELS"], "bulwark-approved")
                if "alembic" in cmd:
                    return ci_local.CmdResult(0, "abc (head)\n")
                return ci_local.CmdResult(0)

            rows = ci_local.run_steps(
                worktree=worktree,
                base=BASE,
                head=HEAD,
                labels="bulwark-approved",
                run=run,
                script=ROOT / "scripts" / "check-protected-paths.sh",
                environ={"COMPOSE_PROJECT_NAME": "holdfast"},
            )
            self.assertTrue((worktree / ".env").is_symlink())
            self.assertEqual(os.readlink(worktree / ".env"), str(secret))
            self.assertEqual(secret.read_text(), SENTINEL + "\n")
            names = [name for name, _result in rows]
            self.assertEqual(
                names,
                [
                    "lint", "test-locked", "lint-imports", "alembic-head",
                    "gitleaks", "compose-config", "protected-paths",
                ],
            )
            self.assertTrue(all(result == "pass" for _name, result in rows))
            summary = ci_local.build_summary(HEAD, BASE, rows)
            self.assertIn(f"Head: `{HEAD}`", summary)
            self.assertIn("Result: pass", summary)
            self.assertNotIn(SENTINEL, summary)
            commands = [cmd for cmd, _env in seen]
            self.assertEqual(commands[0], ["make", "lint"])
            self.assertEqual(commands[1], ["make", "test-locked"])
            self.assertEqual(commands[2], ["make", "lint-imports"])

    def test_two_alembic_heads_fail_and_later_steps_still_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            git(worktree, "init", "-q")
            (worktree / "README.md").write_text("hi\n")
            git(worktree, "add", "-A")
            git(worktree, "commit", "-qm", "base")

            def run(cmd: list[str], cwd: Path, env: dict | None) -> ci_local.CmdResult:
                if "alembic" in cmd:
                    return ci_local.CmdResult(0, "aaa (head)\nbbb (head)\n")
                return ci_local.CmdResult(0)

            rows = ci_local.run_steps(
                worktree=worktree, base=BASE, head=HEAD, labels="", run=run,
                script=ROOT / "scripts" / "check-protected-paths.sh",
            )
            results = dict(rows)
            self.assertEqual(results["alembic-head"], "fail")
            self.assertEqual(results["gitleaks"], "pass")
            summary = ci_local.build_summary(HEAD, BASE, rows)
            self.assertIn("Result: fail", summary)
            self.assertIn(f"Head: `{HEAD}`", summary)


if __name__ == "__main__":
    unittest.main()
