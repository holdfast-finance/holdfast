"""Path gate and push-to-main skip decisions for scripts/ci_plan.py."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
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
        self.assertLess(text.index("pyproject.toml"), text.index("scripts/ci_plan.py"))
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


if __name__ == "__main__":
    unittest.main()
