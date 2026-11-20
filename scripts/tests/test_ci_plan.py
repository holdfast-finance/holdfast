"""Path gate and push-to-main skip decisions for scripts/ci_plan.py."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import ci_plan  # noqa: E402


def git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True,
    ).stdout.strip()


def write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def workflow_heredocs(text: str) -> list[str]:
    """Python bodies of ``python3 -I - <<'PY'`` steps in ci.yml."""
    marker = "python3 -I - <<'PY'"
    chunks: list[str] = []
    start = 0
    while True:
        idx = text.find(marker, start)
        if idx == -1:
            return chunks
        body_start = text.find("\n", idx) + 1
        end = text.find("\n          PY\n", body_start)
        if end == -1:
            raise AssertionError("unclosed heredoc")
        lines: list[str] = []
        for line in text[body_start:end].split("\n"):
            if line == "":
                lines.append("")
            else:
                if not line.startswith("          "):
                    raise AssertionError(line)
                lines.append(line[10:])
        chunks.append("\n".join(lines) + "\n")
        start = end + 1


class PlanTests(unittest.TestCase):
    def test_docs_only_pr_skips_heavy_jobs(self) -> None:
        decision = ci_plan.plan(
            event="pull_request", ref="refs/heads/docs", paths=["docs/STATUS.md"], uptodate=None,
        )
        self.assertFalse(decision.backend)
        self.assertFalse(decision.web)
        self.assertEqual(decision.backend_reason, "paths")
        self.assertEqual(decision.web_reason, "paths")

    def test_backend_and_web_paths_stay_separate(self) -> None:
        backend = ci_plan.plan(
            event="pull_request", ref="refs/heads/b", paths=["backend/app/main.py"], uptodate=None,
        )
        web = ci_plan.plan(
            event="pull_request", ref="refs/heads/w", paths=["web/src/main.ts"], uptodate=None,
        )
        self.assertTrue(backend.backend)
        self.assertFalse(backend.web)
        self.assertFalse(web.backend)
        self.assertTrue(web.web)

    def test_unknown_diff_runs_both(self) -> None:
        decision = ci_plan.plan(
            event="pull_request", ref="refs/heads/x", paths=None, uptodate=True,
        )
        self.assertTrue(decision.backend and decision.web)
        self.assertEqual(decision.backend_reason, "unknown")

    def test_main_uptodate_skips_even_when_backend_changed(self) -> None:
        decision = ci_plan.plan(
            event="push", ref="refs/heads/main", paths=["backend/app/main.py"], uptodate=True,
        )
        self.assertFalse(decision.backend)
        self.assertFalse(decision.web)
        self.assertEqual(decision.backend_reason, "main-uptodate")

    def test_main_behind_or_unknown_runs_both(self) -> None:
        behind = ci_plan.plan(
            event="push", ref="refs/heads/main", paths=["docs/STATUS.md"], uptodate=False,
        )
        unknown = ci_plan.plan(
            event="push", ref="refs/heads/main", paths=["docs/STATUS.md"], uptodate=None,
        )
        self.assertTrue(behind.backend and behind.web)
        self.assertEqual(behind.backend_reason, "run")
        self.assertTrue(unknown.backend and unknown.web)
        self.assertEqual(unknown.backend_reason, "unknown")

    def test_phase_branch_stays_path_gated(self) -> None:
        decision = ci_plan.plan(
            event="push", ref="refs/heads/phase/1", paths=["docs/STATUS.md"], uptodate=True,
        )
        self.assertFalse(decision.backend)
        self.assertEqual(decision.backend_reason, "paths")

    def test_emit_keys(self) -> None:
        text = ci_plan.emit(ci_plan.Plan(False, True, "paths", "run"))
        self.assertEqual(
            text,
            "backend=false\nweb=true\nbackend_reason=paths\nweb_reason=run\n",
        )

    def test_probe_and_paths_on_a_real_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git(repo, "init", "-q", "-b", "main")
            write(repo, "README.md", "o\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "o")
            origin = git(repo, "rev-parse", "HEAD")
            git(repo, "checkout", "-qb", "pr")
            write(repo, "README.md", "pr\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "pr")
            head = git(repo, "rev-parse", "HEAD")
            git(repo, "checkout", "-q", "main")
            write(repo, "docs/STATUS.md", "docs\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "before")
            before = git(repo, "rev-parse", "HEAD")
            write(repo, "backend/app.py", "x = 1\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "after")
            after = git(repo, "rev-parse", "HEAD")

            def pulls(sha: str) -> list[dict[str, object]]:
                self.assertEqual(sha, after)
                return [{
                    "merge_commit_sha": after,
                    "merged_at": "2026-11-19T00:00:00Z",
                    "number": 9,
                    "head_sha": head,
                }]

            # PR head branched before `before`, so the squash was not up to date.
            self.assertIs(
                ci_plan.probe_uptodate(
                    repo, before, after, load_pulls=pulls, fetch_pr=lambda _n, _h: True,
                ),
                False,
            )
            behind = ci_plan.decide(
                repo, event="push", ref="refs/heads/main", before=before, after=after,
                pr_base="", pr_head="", repo_name="o/r", load_pulls=pulls,
                fetch_pr=lambda _n, _h: True,
            )
            self.assertTrue(behind.backend and behind.web)

            # A head that contains `before` is up to date: skip backend and web.
            git(repo, "checkout", "-q", "-b", "current", before)
            write(repo, "web/src/app.ts", "export {}\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "uptodate-head")
            current = git(repo, "rev-parse", "HEAD")

            def pulls_current(sha: str) -> list[dict[str, object]]:
                return [{
                    "merge_commit_sha": after,
                    "merged_at": "2026-11-19T00:00:00Z",
                    "number": 9,
                    "head_sha": current,
                }]

            self.assertIs(
                ci_plan.probe_uptodate(
                    repo, before, after, load_pulls=pulls_current, fetch_pr=lambda _n, _h: True,
                ),
                True,
            )
            skipped = ci_plan.decide(
                repo, event="push", ref="refs/heads/main", before=before, after=after,
                pr_base="", pr_head="", repo_name="o/r", load_pulls=pulls_current,
                fetch_pr=lambda _n, _h: True,
            )
            self.assertFalse(skipped.backend)
            self.assertFalse(skipped.web)
            self.assertEqual(skipped.backend_reason, "main-uptodate")

            docs = ci_plan.compute_paths(
                repo, event="pull_request", before="", after="", pr_base=origin, pr_head=head,
            )
            self.assertEqual(docs, ["README.md"])
            docs_plan = ci_plan.plan(
                event="pull_request", ref="refs/heads/pr", paths=docs, uptodate=None,
            )
            self.assertFalse(docs_plan.backend)
            self.assertFalse(docs_plan.web)

            # PR head does not contain the current base: when in doubt, run.
            self.assertIsNone(ci_plan.compute_paths(
                repo, event="pull_request", before="", after="", pr_base=before, pr_head=head,
            ))

    def test_ci_jobs_are_checks_backend_web(self) -> None:
        text = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertLess(text.index("pyproject.toml"), text.index("python3 -I - <<'PY'"))
        self.assertNotIn("python3 scripts/ci_plan.py", text)
        self.assertNotIn("python3 scripts/ci_required_eval.py", text)
        self.assertNotIn("actions/checkout", text.split("\n  ci-required:", 1)[1])
        heredocs = workflow_heredocs(text)
        self.assertEqual(len(heredocs), 2)
        self.assertEqual(heredocs[0], (ROOT / "scripts" / "ci_plan.py").read_text())
        self.assertEqual(heredocs[1], (ROOT / "scripts" / "ci_required_eval.py").read_text())
        self.assertIn("needs: [checks, backend, web]", text)
        ids: list[str] = []
        in_jobs = False
        for line in text.splitlines():
            if line.startswith("jobs:"):
                in_jobs = True
                continue
            if in_jobs and line and not line.startswith(" "):
                break
            if in_jobs and line.startswith("  ") and not line.startswith("   ") and line.endswith(":"):
                ids.append(line.strip()[:-1])
        self.assertEqual(ids, ["checks", "backend", "web", "ci-required"])

    def test_shadow_modules_do_not_hijack_trusted_gate(self) -> None:
        """scripts/json.py and scripts/subprocess.py must not decide the gate."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git(repo, "init", "-q", "-b", "main")
            write(repo, "README.md", "docs\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "base")
            base = git(repo, "rev-parse", "HEAD")
            write(repo, "backend/app.py", "x = 1\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "backend")
            head = git(repo, "rev-parse", "HEAD")
            write(
                repo, "scripts/subprocess.py",
                "class SubprocessError(Exception):\n    pass\n"
                "class CompletedProcess:\n"
                "    def __init__(self):\n"
                "        self.returncode = 0\n"
                "        self.stdout = 'M\\0README.md\\0'\n"
                "        self.stderr = ''\n"
                "def run(*_a, **_k):\n"
                "    return CompletedProcess()\n",
            )
            write(
                repo, "scripts/json.py",
                "class JSONDecodeError(Exception):\n    pass\n"
                "def loads(*_a, **_k):\n"
                "    return {'checks': {'result': 'success', 'outputs': {\n"
                "        'backend': 'false', 'web': 'false',\n"
                "        'backend_reason': 'paths', 'web_reason': 'paths'}},\n"
                "        'backend': {'result': 'skipped'}, 'web': {'result': 'skipped'}}\n"
                "def dumps(*_a, **_k):\n"
                "    return '{}'\n",
            )
            shutil.copy(ROOT / "scripts" / "ci_plan.py", repo / "scripts" / "ci_plan.py")
            shutil.copy(ROOT / "scripts" / "ci_required_eval.py", repo / "scripts" / "ci_required_eval.py")
            plan_env = {
                "PATH": os.environ["PATH"],
                "HOME": os.environ.get("HOME", ""),
                "EVENT_NAME": "pull_request",
                "REF_NAME": "refs/heads/pr",
                "BEFORE": "",
                "AFTER": head,
                "PR_BASE": base,
                "PR_HEAD": head,
                "REPO": "example/holdfast",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            heredocs = workflow_heredocs((ROOT / ".github/workflows/ci.yml").read_text())
            poisoned = subprocess.run(
                ["python3", "scripts/ci_plan.py"], cwd=repo, env=plan_env,
                capture_output=True, text=True, check=False,
            )
            self.assertIn("backend=false", poisoned.stdout, poisoned.stderr)
            control = subprocess.run(
                ["python3", "-I", "scripts/ci_plan.py"], cwd=repo, env=plan_env,
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(control.returncode, 0, control.stderr)
            self.assertIn("backend=true", control.stdout)
            trusted = subprocess.run(
                ["python3", "-I", "-"], cwd=repo, env=plan_env, input=heredocs[0],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(trusted.returncode, 0, trusted.stderr)
            self.assertIn("backend=true", trusted.stdout)

            failed = {
                "checks": {"result": "success", "outputs": {
                    "backend": "true", "web": "false",
                    "backend_reason": "run", "web_reason": "paths",
                }},
                "backend": {"result": "failure"},
                "web": {"result": "skipped"},
            }
            eval_env = {
                "PATH": os.environ["PATH"],
                "HOME": os.environ.get("HOME", ""),
                "RESULTS": json.dumps(failed),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            poisoned_eval = subprocess.run(
                ["python3", "scripts/ci_required_eval.py"], cwd=repo, env=eval_env,
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(poisoned_eval.returncode, 0, poisoned_eval.stderr)
            control_eval = subprocess.run(
                ["python3", "-I", "scripts/ci_required_eval.py"], cwd=repo, env=eval_env,
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(control_eval.returncode, 0)
            self.assertIn("failure", control_eval.stdout + control_eval.stderr)
            trusted_eval = subprocess.run(
                ["python3", "-I", "-"], cwd=repo, env=eval_env, input=heredocs[1],
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(trusted_eval.returncode, 0)
            self.assertIn("failure", trusted_eval.stdout + trusted_eval.stderr)


class QuotedPathTests(unittest.TestCase):
    """Non-ASCII and quote characters must not hide a path from the planner."""

    NAMES = ["\u00e9.py", 'a"b.py']

    def _repo(self, tmp: str) -> Path:
        repo = Path(tmp)
        git(repo, "init", "-q", "-b", "main")
        write(repo, "README.md", "o\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "o")
        return repo

    def test_added_quoted_names_are_listed_raw_and_plan_the_right_job(self) -> None:
        for top, kind in (("backend/migrations/versions", "backend"), ("web/src", "web")):
            for name in self.NAMES:
                with self.subTest(top=top, name=name), tempfile.TemporaryDirectory() as tmp:
                    repo = self._repo(tmp)
                    base = git(repo, "rev-parse", "HEAD")
                    write(repo, f"{top}/{name}", "x\n")
                    git(repo, "add", "-A")
                    git(repo, "commit", "-qm", "q")
                    paths = ci_plan.name_status_paths(repo, f"{base}..HEAD")
                    self.assertEqual(paths, [f"{top}/{name}"])
                    self.assertTrue(ci_plan.affected(paths or [], kind))

    def test_rename_and_copy_list_both_sides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp)
            write(repo, "docs/\u00e9.md", "same content\nline two\nline three\n")
            write(repo, "docs/src.md", "copy me content\nline two\nline three\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "seed")
            base = git(repo, "rev-parse", "HEAD")
            (repo / "backend").mkdir()
            git(repo, "mv", "docs/\u00e9.md", "backend/\u00e9.py")
            write(repo, "web/src/a\"b.ts", "copy me content\nline two\nline three\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "rc")
            paths = ci_plan.name_status_paths(repo, f"{base}..HEAD")
            assert paths is not None
            self.assertIn("docs/\u00e9.md", paths)
            self.assertIn("backend/\u00e9.py", paths)
            self.assertTrue(ci_plan.affected(paths, "backend"))
            self.assertTrue(ci_plan.affected(paths, "web"))
            self.assertFalse(any(p.startswith('"') for p in paths))

    def test_copy_entry_parses_two_paths(self) -> None:
        fake = subprocess.CompletedProcess([], 0, "C100\0docs/a.md\0backend/b.py\0M\0README.md\0", "")
        with unittest.mock.patch.object(ci_plan, "_git", return_value=fake):
            self.assertEqual(
                ci_plan.name_status_paths(Path("."), "x"),
                ["docs/a.md", "backend/b.py", "README.md"],
            )

    def test_unparseable_entries_mean_unknown(self) -> None:
        for out in ("Z\0a\0", "R100\0only-one\0", "M\0", "M\0\0"):
            with self.subTest(out=out):
                fake = subprocess.CompletedProcess([], 0, out, "")
                with unittest.mock.patch.object(ci_plan, "_git", return_value=fake):
                    self.assertIsNone(ci_plan.name_status_paths(Path("."), "x"))

    def test_quoted_path_always_affects_both_jobs(self) -> None:
        for kind in ("backend", "web"):
            self.assertTrue(ci_plan.affected(['"docs/\\303\\251.md"'], kind))
        self.assertFalse(ci_plan.affected(["docs/x.md"], "backend"))


class HeredocParityTests(unittest.TestCase):
    """Each inline workflow copy must be byte-identical to the tested script."""

    def test_ci_yml_heredocs(self) -> None:
        text = (ROOT / ".github/workflows/ci.yml").read_text()
        docs = workflow_heredocs(text)
        self.assertEqual(
            docs[0], (ROOT / "scripts/ci_plan.py").read_text(), "ci.yml planner drifted from ci_plan.py"
        )
        self.assertEqual(
            docs[1],
            (ROOT / "scripts/ci_required_eval.py").read_text(),
            "ci.yml evaluator drifted from ci_required_eval.py",
        )

    def test_main_guard_heredoc(self) -> None:
        text = (ROOT / ".github/workflows/main-guard.yml").read_text()
        docs = workflow_heredocs(text)
        self.assertEqual(len(docs), 1)
        self.assertEqual(
            docs[0], (ROOT / "scripts/main_guard.py").read_text(), "main-guard.yml drifted from main_guard.py"
        )

    def test_main_guard_permissions(self) -> None:
        text = (ROOT / ".github/workflows/main-guard.yml").read_text()
        self.assertIn("  actions: read\n", text)
        self.assertNotIn("checks: read", text)


if __name__ == "__main__":
    unittest.main()
