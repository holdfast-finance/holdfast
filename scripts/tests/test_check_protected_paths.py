"""Seeded-diff self-tests for scripts/check-protected-paths.sh and scripts/audit_gate.py (stdlib only)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check-protected-paths.sh"
sys.path.insert(0, str(ROOT / "scripts"))
import audit_gate  # noqa: E402

TESTS_PY = "def test_a():\n    assert 1\n\n\ndef test_b():\n    assert 2\n"
TESTS_TS = "it('a', () => {});\nit('b', () => {});\n"


def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


def write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


class GateCase(unittest.TestCase):
    def run_gate(self, change, labels: str = "") -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            git(repo, "init", "-q", "-b", "main")
            write(repo, "backend/tests/test_x.py", TESTS_PY)
            write(repo, "web/src/x.test.ts", TESTS_TS)
            write(repo, "backend/migrations/versions/0001.py", "x = 1\n")
            write(repo, "README.md", "hi\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "base")
            base = git(repo, "rev-parse", "HEAD")
            git(repo, "checkout", "-qb", "pr")
            change(repo)
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "change")
            head = git(repo, "rev-parse", "HEAD")
            env = {**os.environ, "BASE_SHA": base, "HEAD_SHA": head, "LABELS": labels}
            r = subprocess.run(["bash", str(SCRIPT)], cwd=repo, env=env, capture_output=True, text=True)
            return r.returncode, r.stdout + r.stderr

    def blocked(self, change, needle: str) -> None:
        code, out = self.run_gate(change)
        self.assertEqual(code, 1, out)
        self.assertIn(needle, out)
        code, out = self.run_gate(change, labels="bulwark-approved")
        self.assertEqual(code, 0, out)

    def test_clean_change_passes(self) -> None:
        code, out = self.run_gate(lambda r: write(r, "README.md", "bye\n"))
        self.assertEqual(code, 0, out)

    def test_protected_paths(self) -> None:
        for rel in [".github/workflows/ci.yml", "infra/postgres/init/00-roles.sh", ".gitleaks.toml",
                    ".gitleaksignore", "backend/pyproject.toml", "backend/tests/conftest.py",
                    "conftest.py", "docker-compose.yml", "scripts/check-protected-paths.sh"]:
            with self.subTest(rel=rel):
                self.blocked(lambda r, rel=rel: write(r, rel, "x\n"), f"protected path touched: {rel}")

    def test_quoted_path_bypass(self) -> None:
        for rel in [".github/workflows/caf\u00e9.yml", 'infra/postgres/a"b.sh', "web/vitest.config.ts",
                    "web/playwright.config.ts", "web/package.json"]:
            with self.subTest(rel=rel):
                self.blocked(lambda r, rel=rel: write(r, rel, "x\n"), f"protected path touched: {rel}")

    def test_migration_privilege_patterns(self) -> None:
        for pat in ["ALTER ROLE holdfast_app BYPASSRLS", "GRANT ALL ON t TO holdfast_app",
                    "CREATE FUNCTION f() SECURITY DEFINER"]:
            with self.subTest(pat=pat):
                self.blocked(lambda r, pat=pat: write(r, "backend/migrations/versions/0002.py", f'op.execute("{pat}")\n'),
                             "role/privilege change")

    def test_rls_weakened(self) -> None:
        self.blocked(lambda r: write(r, "backend/migrations/versions/0002.py", 'op.execute("DROP POLICY p ON t")\n'),
                     "RLS")

    def test_test_file_deleted(self) -> None:
        self.blocked(lambda r: (r / "backend/tests/test_x.py").unlink(), "test deleted")

    def test_net_test_function_loss(self) -> None:
        self.blocked(lambda r: write(r, "backend/tests/test_x.py", "def test_a():\n    assert 1\n"),
                     "net loss of 1 'def test_'")

    def test_renamed_test_function_is_not_a_loss(self) -> None:
        code, out = self.run_gate(lambda r: write(r, "backend/tests/test_x.py", TESTS_PY.replace("test_b", "test_c")))
        self.assertEqual(code, 0, out)

    def test_python_skip_markers(self) -> None:
        for line in ["@pytest.mark.xfail\n", "x = pytest.importorskip('y')\n", "collect_ignore = ['a']\n",
                     "@pytest.mark.skip\n"]:
            with self.subTest(line=line):
                self.blocked(lambda r, line=line: write(r, "backend/tests/test_x.py", TESTS_PY + line), "skip/xfail")

    def test_web_skip_and_loss(self) -> None:
        self.blocked(lambda r: write(r, "web/src/x.test.ts", TESTS_TS + "it.skip('c', () => {});\n"), "web tests")
        self.blocked(lambda r: write(r, "web/src/x.test.ts", TESTS_TS + "describe.only('c', () => {});\n"), "web tests")
        self.blocked(lambda r: write(r, "web/src/x.test.ts", "it('a', () => {});\n"), "net loss of 1 web test")


class AuditGateCase(unittest.TestCase):
    def load(self, body: str):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "allow.yml"
            p.write_text(body)
            return audit_gate.load_allowlist(p, "pip")

    def entry(self, ident: str, days: int) -> str:
        return f"allow:\n  - id: {ident}\n    scope: pip\n    expires: {date.today() + timedelta(days=days)}\n"

    def test_valid_entry(self) -> None:
        allowed, errors = self.load(self.entry("GHSA-1", 30))
        self.assertEqual((allowed, errors), ({"GHSA-1"}, []))

    def test_expiry_cap(self) -> None:
        allowed, errors = self.load(self.entry("GHSA-1", 91))
        self.assertFalse(allowed)
        self.assertTrue(errors)
        self.assertTrue(self.load(self.entry("GHSA-1", 90))[0])

    def test_expired(self) -> None:
        self.assertTrue(self.load(self.entry("GHSA-1", -1))[1])

    def test_empty_id_rejected(self) -> None:
        allowed, errors = self.load(self.entry("", 30))
        self.assertFalse(allowed)
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
