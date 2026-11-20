"""ci-local runs fixed commands and never links or reads an operator .env."""

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

    def test_steps_are_fixed_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            git(worktree, "init", "-q", "-b", "main")
            (worktree / "README.md").write_text("hi\n")
            git(worktree, "add", "-A")
            git(worktree, "commit", "-qm", "base")
            seen: list[tuple[list[str], dict | None]] = []

            def run(cmd: list[str], cwd: Path, env: dict | None) -> ci_local.CmdResult:
                seen.append((cmd, None if env is None else dict(env)))
                blob = " ".join(cmd)
                self.assertNotIn(SENTINEL, blob)
                self.assertNotIn("make", cmd)
                self.assertFalse(any(part == "make" or part.endswith("/make") for part in cmd))
                self.assertNotIn("-p", cmd)
                self.assertNotIn("--publish", cmd)
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
                    self.assertEqual(cmd[1], str(ROOT / "scripts" / "check-protected-paths.sh"))
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
            names = [name for name, _result in rows]
            self.assertEqual(
                names,
                [
                    "ruff", "mypy", "pytest", "web-lint", "web-typecheck", "web-test",
                    "lint-imports", "alembic-head", "gitleaks", "compose-config",
                    "protected-paths",
                ],
            )
            self.assertTrue(all(result == "pass" for _name, result in rows))
            summary = ci_local.build_summary(HEAD, BASE, rows)
            self.assertIn(f"Head: `{HEAD}`", summary)
            self.assertIn("Result: pass", summary)
            self.assertNotIn(SENTINEL, summary)
            commands = [cmd for cmd, _env in seen]
            self.assertEqual(commands[0], ["uv", "run", "ruff", "check", "."])
            self.assertEqual(commands[1], ["uv", "run", "ruff", "format", "--check", "."])
            self.assertEqual(commands[2], ["uv", "run", "mypy", "app"])
            self.assertEqual(commands[3], ["uv", "run", "pytest", "-q"])
            self.assertNotIn("gen-keys.py", " ".join(" ".join(cmd) for cmd in commands))

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

    def test_symlinked_example_is_not_opened(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "wt"
            work.mkdir()
            real = root / "real.env"
            real.write_bytes(b"SENTINEL_NOT_A_SECRET=1\n")
            before = (real.stat().st_ino, real.stat().st_size, real.stat().st_mtime_ns)
            (work / ".env.example").symlink_to(real)
            self.assertFalse(ci_local.install_throwaway_env(work))
            self.assertFalse((work / ".env").exists())
            self.assertEqual(
                (real.stat().st_ino, real.stat().st_size, real.stat().st_mtime_ns), before,
            )

    def test_hostile_makefile_is_not_executed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            origin = root / "src"
            origin.mkdir()
            git(origin, "init", "-q", "-b", "main")
            (origin / ".env.example").write_text(
                "FERNET_KEY=\nSESSION_SECRET=\nPOSTGRES_PASSWORD=\n"
                "POSTGRES_OWNER_PASSWORD=\nPOSTGRES_APP_PASSWORD=\n"
                "DATABASE_URL=postgresql+psycopg://holdfast_app:CHANGE_ME@db:5432/holdfast\n"
                "MIGRATIONS_DATABASE_URL=postgresql+psycopg://holdfast_owner:CHANGE_ME@db:5432/holdfast\n",
            )
            (origin / "Makefile").write_text(
                ".PHONY: lint test test-locked lint-imports\n"
                "lint test test-locked lint-imports:\n"
                "\ttouch HOSTILE_RAN\n",
            )
            (origin / "README.md").write_text("hi\n")
            git(origin, "add", "-A")
            git(origin, "commit", "-qm", "base")
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=origin, check=True, capture_output=True, text=True,
            ).stdout.strip()
            real = origin / ".env"
            real.write_bytes(b"SENTINEL_NOT_A_SECRET=1\n")
            before = (real.stat().st_ino, real.stat().st_size, real.stat().st_mtime_ns)
            seen: list[list[str]] = []
            checked = {"env": False}

            def run(cmd: list[str], cwd: Path, env: dict | None) -> ci_local.CmdResult:
                seen.append(list(cmd))
                self.assertFalse(any(part == "make" or part.endswith("/make") for part in cmd))
                self.assertFalse(any("SENTINEL_NOT_A_SECRET" in part or part.endswith("real.env") for part in cmd))
                if cmd and (cmd[0] == "make" or cmd[0].endswith("/make")):
                    subprocess.run(cmd, cwd=cwd, check=False)
                for parent in [cwd, *cwd.parents]:
                    if not (parent / "Makefile").is_file():
                        continue
                    envp = parent / ".env"
                    self.assertTrue(envp.is_file())
                    self.assertFalse(envp.is_symlink())
                    self.assertEqual(envp.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(envp.read_bytes().find(b"SENTINEL_NOT_A_SECRET"), -1)
                    self.assertNotEqual(envp.stat().st_ino, before[0])
                    self.assertFalse((parent / "HOSTILE_RAN").exists())
                    self.assertFalse((parent / "backend" / "HOSTILE_RAN").exists())
                    checked["env"] = True
                    break
                if "alembic" in cmd and "heads" in cmd:
                    return ci_local.CmdResult(0, "abc (head)\n")
                return ci_local.CmdResult(0)

            saved = (
                ci_local.repo_slug, ci_local.resolve_pr, ci_local.fetch_pr, ci_local.load_labels,
            )
            fetched = {"n": 0}

            def fetch(*_args: object, **_kwargs: object) -> None:
                fetched["n"] += 1

            ci_local.repo_slug = lambda _start: "example/holdfast"  # type: ignore[assignment]
            ci_local.resolve_pr = lambda _pr, _repo: (head, head)  # type: ignore[assignment]
            ci_local.fetch_pr = fetch  # type: ignore[assignment]
            ci_local.load_labels = lambda _repo, _pr: ""  # type: ignore[assignment]
            try:
                code = ci_local.execute("7", origin, run=run)
            finally:
                (
                    ci_local.repo_slug, ci_local.resolve_pr, ci_local.fetch_pr, ci_local.load_labels,
                ) = saved
            self.assertEqual(code, 0)
            self.assertEqual(fetched["n"], 1)
            self.assertTrue(checked["env"])
            self.assertTrue(seen)
            self.assertFalse(any("make" in cmd for cmd in seen))
            self.assertEqual(
                (real.stat().st_ino, real.stat().st_size, real.stat().st_mtime_ns), before,
            )


if __name__ == "__main__":
    unittest.main()
