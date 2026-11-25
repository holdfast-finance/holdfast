"""Codex apply_patch guard: blocks secrets, guard-machinery edits and malformed input (exit 2)."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / ".codex" / "hooks" / "patch.py"
FAKE_KEY = "sk-ant-" + "a" * 30


def run(stdin: str) -> int:
    return subprocess.run(
        [sys.executable, str(GUARD)], input=stdin, text=True, capture_output=True, check=False
    ).returncode


def patch(body: str) -> str:
    return f"*** Begin Patch\n{body}\n*** End Patch"


def payload(command: object) -> str:
    return json.dumps({"cwd": str(ROOT), "tool_input": {"command": command}})


class CodexPatchGuard(unittest.TestCase):
    def test_trailing_space_env_header_blocked(self) -> None:
        self.assertEqual(run(payload(patch("*** Update File: .env \n+X=1"))), 2)

    def test_leading_space_header_with_fake_key_blocked(self) -> None:
        self.assertEqual(run(payload(patch(f" *** Add File: notes.txt\n+{FAKE_KEY}"))), 2)

    def test_missing_command_blocked(self) -> None:
        self.assertEqual(run(json.dumps({"cwd": str(ROOT), "tool_input": {}})), 2)
        self.assertEqual(run(payload("")), 2)

    def test_bad_json_blocked(self) -> None:
        self.assertEqual(run("{not json"), 2)

    def test_githooks_blocked(self) -> None:
        self.assertEqual(run(payload(patch("*** Update File: .githooks/pre-push\n+exit 0"))), 2)

    def test_unknown_marker_blocked(self) -> None:
        self.assertEqual(run(payload(patch("*** Frobnicate File: a.txt\n+x"))), 2)

    def test_no_file_blocked(self) -> None:
        self.assertEqual(run(payload(patch("+x"))), 2)

    def test_clean_patch_allowed(self) -> None:
        self.assertEqual(run(payload(patch("*** Add File: docs/hello.md\n+hello"))), 0)


if __name__ == "__main__":
    unittest.main()
