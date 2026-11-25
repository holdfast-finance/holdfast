"""check-private-data.sh range mode: every pushed commit counts (ADR 0019, ADR 0039)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check-private-data.sh"


class RangeModeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "t")
        self.git("config", "commit.gpgsign", "false")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit_file(self, name: str, msg: str) -> str:
        (self.repo / name).write_text("x\n")
        self.git("add", "-f", name)
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD")

    def check(self, rev_range: str, deny: str | None = None) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "HOLDFAST_PRIVATE_DENYLIST": deny or "/nonexistent"}
        return subprocess.run(
            ["bash", str(SCRIPT), "range", rev_range],
            cwd=self.repo,
            env=env,
            capture_output=True,
            text=True,
        )

    def test_clean_range_passes(self) -> None:
        base = self.commit_file("readme.md", "root")
        self.commit_file("notes.md", "notes")
        self.assertEqual(self.check(f"{base}..HEAD").returncode, 0)

    def test_file_added_then_deleted_is_still_refused(self) -> None:
        base = self.commit_file("readme.md", "root")
        self.commit_file("statement.pdf", "add")
        self.git("rm", "-q", "statement.pdf")
        self.git("commit", "-q", "-m", "delete")
        result = self.check(f"{base}..HEAD")
        self.assertEqual(result.returncode, 1)
        self.assertIn("statement.pdf", result.stderr)

    def test_orphan_single_commit_is_scanned_including_root(self) -> None:
        sha = self.commit_file("o.pdf", "only commit")
        self.assertEqual(self.check(sha).returncode, 1)

    def test_file_added_in_a_merge_is_refused(self) -> None:
        self.commit_file("readme.md", "root")
        self.git("checkout", "-q", "-b", "side")
        self.commit_file("side.md", "side")
        self.git("checkout", "-q", "main")
        base = self.commit_file("main.md", "main")
        self.git("merge", "-q", "--no-commit", "--no-ff", "side")
        (self.repo / "m.pdf").write_text("x\n")
        self.git("add", "-f", "m.pdf")
        self.git("commit", "-q", "-m", "merge")
        self.assertEqual(self.check(f"{base}..HEAD").returncode, 1)

    def test_denylist_string_added_then_removed_is_refused(self) -> None:
        base = self.commit_file("readme.md", "root")
        (self.repo / "n.md").write_text("synthetic-secret-name\n")
        self.git("add", "n.md")
        self.git("commit", "-q", "-m", "add")
        (self.repo / "n.md").write_text("clean\n")
        self.git("commit", "-q", "-am", "scrub")
        deny = self.repo.parent / f"{self.repo.name}-deny.txt"
        deny.write_text("synthetic-secret-name\n")
        try:
            self.assertEqual(self.check(f"{base}..HEAD", str(deny)).returncode, 1)
        finally:
            deny.unlink()


if __name__ == "__main__":
    unittest.main()
