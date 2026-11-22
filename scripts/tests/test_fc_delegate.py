"""fc-delegate.sh fails closed and passes every hide. Stdlib only.

Uses fake `firstchair` and `bwrap` executables on PATH; never starts a real worker.
"""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "fc-delegate.sh"
BASH = shutil.which("bash") or "/bin/bash"

# The fake records each call's argv (one JSON list per line) and answers a dry run with the
# "confined" value from FAKE_CONFINED.
FAKE_FIRSTCHAIR = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({"argv": sys.argv[1:], "display": os.environ.get("DISPLAY")}) + "\\n")
if "--dry-run" in sys.argv:
    print(json.dumps({"confined": os.environ.get("FAKE_CONFINED", "true") == "true"}))
"""


def _exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class FcDelegateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        _exe(self.bin / "firstchair", FAKE_FIRSTCHAIR)
        _exe(self.bin / "bwrap", "#!/bin/sh\nexit 0\n")
        self.runtime = self.tmp / "run user"
        self.runtime.mkdir()
        for name in ("bus", "systemd", "at-spi", "keyring"):
            (self.runtime / name).mkdir()
        (self.runtime / ".mutter-Xwaylandauth.ABC123").write_text("cookie")
        (self.runtime / "vscode-git-1.sock").write_text("")
        self.private = self.tmp / "private data"
        self.private.mkdir()
        self.log = self.tmp / "calls.jsonl"

    def run_script(self, *args: str, **env_over: str) -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.tmp),
            "FAKE_LOG": str(self.log),
            "FC_RUNTIME_DIR": str(self.runtime),
            "HOLDFAST_PRIVATE_DIR": str(self.private),
            "DISPLAY": ":0",
        }
        env.update(env_over)
        return subprocess.run(
            [BASH, str(SCRIPT), *args], env=env, capture_output=True, text=True, check=False
        )

    def calls(self) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_passes_every_hide_once_and_keeps_paths_with_spaces_whole(self) -> None:
        result = self.run_script("codex", "--mode", "read", "--name", "x", "task text")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertEqual(len(calls), 2)  # dry run, then the real run
        argv = calls[1]["argv"]
        assert isinstance(argv, list)
        self.assertNotIn("--dry-run", argv)
        self.assertEqual(argv[:3], ["delegate", "run", "codex"])
        self.assertIn("--confine", argv)
        hidden = [argv[i + 1] for i, a in enumerate(argv) if a == "--hide"]
        self.assertEqual(len(hidden), len(set(hidden)))
        for expected in (
            self.private,
            self.runtime / "bus",
            self.runtime / "systemd",
            self.runtime / "at-spi",
            self.runtime / "keyring",
            self.runtime / ".mutter-Xwaylandauth.ABC123",
            self.runtime / "vscode-git-1.sock",
        ):
            self.assertIn(str(expected.resolve()), hidden)
        self.assertIsNone(calls[1]["display"])

    def test_session_and_repo_go_before_the_subcommands(self) -> None:
        result = self.run_script("grok", "task", FIRSTCHAIR_SESSION="s1", FC_REPO="/r")
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = self.calls()[1]["argv"]
        self.assertEqual(argv[:6], ["--session", "s1", "delegate", "--repo", "/r", "run"])

    def test_missing_private_directory_refuses(self) -> None:
        result = self.run_script("codex", "task", HOLDFAST_PRIVATE_DIR=str(self.tmp / "nope"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not exist", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_private_none_is_explicit_opt_out(self) -> None:
        result = self.run_script("codex", "task", HOLDFAST_PRIVATE_DIR="none")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_bubblewrap_refuses(self) -> None:
        # A PATH holding only the tools the script needs, so a system bwrap cannot be found.
        only = self.tmp / "nobwrap"
        only.mkdir()
        _exe(only / "firstchair", FAKE_FIRSTCHAIR)
        for tool in ("env", "id", "realpath", "python3"):
            found = shutil.which(tool)
            assert found is not None
            (only / tool).symlink_to(found)
        result = self.run_script("codex", "task", PATH=str(only))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("bubblewrap", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_unconfined_dry_run_refuses_to_start(self) -> None:
        result = self.run_script("codex", "task", FAKE_CONFINED="false")
        self.assertEqual(result.returncode, 2)
        self.assertIn("confined", result.stderr)
        self.assertEqual(len(self.calls()), 1)  # only the dry run

    def test_caller_cannot_pass_its_own_hides_or_a_prefix_of_them(self) -> None:
        for flag in ("--hide", "--hid", "--hi", "--hide=/x", "--confine", "--conf", "--con"):
            with self.subTest(flag=flag):
                result = self.run_script("codex", flag, "/x", "task")
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(self.calls(), [])

    def test_ordinary_flags_are_not_mistaken_for_hide_prefixes(self) -> None:
        result = self.run_script("codex", "--mode", "read", "--name", "n", "--help-me", "task")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_planted_module_in_the_working_directory_is_not_imported(self) -> None:
        planted = self.tmp / "planted"
        planted.mkdir()
        (planted / "json.py").write_text(
            "import sys\n"
            "def loads(_s):\n"
            "    return {'confined': True}\n"
        )
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.tmp),
            "FAKE_LOG": str(self.log),
            "FC_RUNTIME_DIR": str(self.runtime),
            "HOLDFAST_PRIVATE_DIR": str(self.private),
            "FAKE_CONFINED": "false",
        }
        result = subprocess.run(
            [BASH, str(SCRIPT), "codex", "task"],
            env=env,
            cwd=planted,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(len(self.calls()), 1)  # only the dry run

    def test_runs_from_any_directory(self) -> None:
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.tmp),
            "FAKE_LOG": str(self.log),
            "FC_RUNTIME_DIR": str(self.runtime),
            "HOLDFAST_PRIVATE_DIR": str(self.private),
        }
        result = subprocess.run(
            [BASH, str(SCRIPT), "codex", "task"],
            env=env,
            cwd=tempfile.gettempdir(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
