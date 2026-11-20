"""Seeded main-guard cases: no PR, red ci-required, force-push, clean push."""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import main_guard  # noqa: E402

AFTER = "b" * 40
HEAD = "d" * 40
BEFORE = "a" * 40


class FakeGH:
    def __init__(self, compare=None, pulls=None, runs=None, compare_error=False) -> None:
        self.compare_doc = compare
        self._pulls = pulls or {}
        self._runs = runs or {}
        self.compare_error = compare_error
        self.issues: list[tuple[str, str]] = []
        self.labels = 0
        self.timeline_events: list[dict] = []
        self.compared = 0

    def compare(self, repo: str, before: str, after: str) -> dict:
        self.compared += 1
        if self.compare_error or self.compare_doc is None:
            raise main_guard.ApiError("compare", 1)
        return self.compare_doc

    def pulls(self, repo: str, sha: str) -> list:
        return self._pulls[sha]

    def check_runs(self, repo: str, sha: str) -> list:
        return self._runs[sha]

    def timeline(self, repo: str, number: int) -> list:
        return self.timeline_events

    def ensure_label(self, repo: str) -> None:
        self.labels += 1

    def find_open_issue(self, repo: str, after: str) -> int | None:
        return None

    def create_issue(self, repo: str, title: str, body: str) -> int:
        self.issues.append((title, body))
        return 7


def _ahead(commits: list[str]) -> dict:
    return {
        "status": "ahead",
        "ahead_by": len(commits),
        "behind_by": 0,
        "total_commits": len(commits),
        "commits": [{"sha": sha} for sha in commits],
    }


def _run(gh: FakeGH, before: str = BEFORE) -> tuple[int, str]:
    buf = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        code = main_guard.execute(before, AFTER, "o/r", gh)
    return code, buf.getvalue() + err.getvalue()


def _merged() -> dict:
    return {
        "merge_commit_sha": AFTER,
        "merged_at": "2026-11-19T00:00:00Z",
        "number": 4,
        "head_sha": HEAD,
    }


def _job(
    name: str, conclusion: str, started: str, workflow: str | None = None,
    event: str | None = None, pr: int | None = 4, base: str = "main",
) -> dict:
    if workflow is None:
        workflow = (
            ".github/workflows/ci.yml" if name == "ci-required"
            else ".github/workflows/review-gate.yml"
        )
    if event is None:
        event = "pull_request_target" if name == "review-gate" else "pull_request"
    return {
        "name": name,
        "conclusion": conclusion,
        "started_at": started,
        "workflow": workflow,
        "event": event,
        "pull_requests": [{"number": pr, "base_ref": base}] if pr is not None else [],
    }


class MainGuardTests(unittest.TestCase):
    def test_commit_with_no_pr_opens_issue(self) -> None:
        gh = FakeGH(_ahead([AFTER]), pulls={AFTER: []}, runs={})
        code, out = _run(gh)
        self.assertEqual(code, 1, out)
        self.assertEqual(gh.labels, 1)
        self.assertIn(AFTER, gh.issues[0][1])
        self.assertIn("found 0", gh.issues[0][1])
        self.assertIn("does not change git history", gh.issues[0][1])
        self.assertNotIn("git revert", gh.issues[0][1])

    def test_red_ci_required_opens_issue(self) -> None:
        runs = {
            HEAD: [
                _job("ci-required", "success", "2026-11-19T00:00:00Z"),
                _job("ci-required", "failure", "2026-11-19T01:00:00Z"),
                _job("review-gate", "success", "2026-11-19T01:00:00Z"),
            ]
        }
        gh = FakeGH(_ahead([AFTER]), pulls={AFTER: [_merged()]}, runs=runs)
        code, out = _run(gh)
        self.assertEqual(code, 1, out)
        body = gh.issues[0][1]
        self.assertIn(AFTER, body)
        self.assertIn(HEAD, body)
        self.assertIn("ci-required", body)
        self.assertIn("failure", body)

    def test_force_push_opens_issue_without_reading_pulls(self) -> None:
        gh = FakeGH(
            {"status": "diverged", "ahead_by": 1, "behind_by": 2, "total_commits": 1, "commits": []},
        )
        gh.pulls = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("pulls"))  # type: ignore[method-assign]
        code, out = _run(gh)
        self.assertEqual(code, 1, out)
        self.assertIn("force-push", gh.issues[0][1])
        self.assertIn(AFTER, gh.issues[0][1])
        self.assertIn(BEFORE, gh.issues[0][1])

    def test_null_before_is_force_push(self) -> None:
        gh = FakeGH()
        code, out = _run(gh, before="0" * 40)
        self.assertEqual(code, 1, out)
        self.assertEqual(gh.compared, 0)
        self.assertIn("all-zero", gh.issues[0][1])

    def test_clean_push_passes(self) -> None:
        runs = {
            HEAD: [
                _job("ci-required", "failure", "2026-11-19T00:00:00Z"),
                _job("ci-required", "success", "2026-11-19T02:00:00Z"),
                _job("review-gate", "success", "2026-11-19T02:00:00Z"),
            ]
        }
        gh = FakeGH(_ahead([AFTER]), pulls={AFTER: [_merged()]}, runs=runs)
        code, out = _run(gh)
        self.assertEqual(code, 0, out)
        self.assertEqual(gh.issues, [])
        self.assertIn("ok", out)

    def test_newer_same_name_from_another_workflow_is_ignored(self) -> None:
        runs = {
            HEAD: [
                _job("ci-required", "failure", "2026-11-19T00:00:00Z"),
                _job(
                    "ci-required", "success", "2026-11-19T03:00:00Z",
                    ".github/workflows/fork-ci.yml",
                ),
                _job("review-gate", "success", "2026-11-19T00:00:00Z"),
                _job(
                    "review-gate", "failure", "2026-11-19T04:00:00Z",
                    ".github/workflows/evil.yml",
                ),
            ]
        }
        chosen = main_guard.latest_run(runs[HEAD], "ci-required", 4)
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual(chosen["conclusion"], "failure")
        self.assertEqual(chosen["workflow"], ".github/workflows/ci.yml")
        gh = FakeGH(_ahead([AFTER]), pulls={AFTER: [_merged()]}, runs=runs)
        code, out = _run(gh)
        self.assertEqual(code, 1, out)
        body = gh.issues[0][1]
        self.assertIn("ci-required", body)
        self.assertIn("failure", body)
        self.assertNotIn("fork-ci.yml", body)

    def _gate(self, jobs: list[dict]) -> tuple[int, str]:
        runs = {HEAD: [_job("ci-required", "success", "2026-11-19T00:00:00Z")] + jobs}
        gh = FakeGH(_ahead([AFTER]), pulls={AFTER: [_merged()]}, runs=runs)
        return _run(gh)

    def test_forged_pull_request_event_review_gate_cannot_override(self) -> None:
        code, out = self._gate([
            _job("review-gate", "failure", "2026-11-19T01:00:00Z"),
            _job("review-gate", "success", "2026-11-19T05:00:00Z", event="pull_request"),
        ])
        self.assertEqual(code, 1, out)
        self.assertIn("failure", out)

    def test_review_gate_for_other_pr_or_base_is_ignored(self) -> None:
        code, out = self._gate([
            _job("review-gate", "failure", "2026-11-19T01:00:00Z"),
            _job("review-gate", "success", "2026-11-19T05:00:00Z", pr=5),
            _job("review-gate", "success", "2026-11-19T06:00:00Z", base="other"),
        ])
        self.assertEqual(code, 1, out)

    def test_review_gate_empty_pull_requests_fails_closed(self) -> None:
        code, out = self._gate([_job("review-gate", "success", "2026-11-19T01:00:00Z", pr=None)])
        self.assertEqual(code, 1, out)
        self.assertIn("missing", out)

    def test_forged_ci_required_for_other_pr_or_base_is_ignored(self) -> None:
        runs = {HEAD: [
            _job("review-gate", "success", "2026-11-19T00:00:00Z"),
            _job("ci-required", "failure", "2026-11-19T01:00:00Z"),
            _job("ci-required", "success", "2026-11-19T05:00:00Z", pr=3, base="x"),
            _job("ci-required", "success", "2026-11-19T06:00:00Z", base="x"),
        ]}
        gh = FakeGH(_ahead([AFTER]), pulls={AFTER: [_merged()]}, runs=runs)
        code, out = _run(gh)
        self.assertEqual(code, 1, out)
        self.assertIn("ci-required", out)
        self.assertIn("failure", out)

    def test_ci_required_empty_pull_requests_and_push_fail_closed(self) -> None:
        for job in (
            _job("ci-required", "success", "2026-11-19T01:00:00Z", pr=None),
            _job("ci-required", "success", "2026-11-19T01:00:00Z", event="push"),
        ):
            runs = {HEAD: [_job("review-gate", "success", "2026-11-19T00:00:00Z"), job]}
            gh = FakeGH(_ahead([AFTER]), pulls={AFTER: [_merged()]}, runs=runs)
            code, out = _run(gh)
            self.assertEqual(code, 1, out)
            self.assertIn("missing", out)

    def test_base_ref_changed_fails(self) -> None:
        runs = {HEAD: [
            _job("review-gate", "success", "2026-11-19T00:00:00Z"),
            _job("ci-required", "success", "2026-11-19T00:00:00Z"),
        ]}
        gh = FakeGH(_ahead([AFTER]), pulls={AFTER: [_merged()]}, runs=runs)
        gh.timeline_events = [{"event": "labeled"}, {"event": "base_ref_changed"}]
        code, out = _run(gh)
        self.assertEqual(code, 1, out)
        self.assertIn("base was changed", out)

    def test_timeline_api_error_fails_closed(self) -> None:
        gh = FakeGH(_ahead([AFTER]), pulls={AFTER: [_merged()]}, runs={HEAD: []})
        gh.timeline = lambda *_a, **_k: (_ for _ in ()).throw(  # type: ignore[method-assign]
            main_guard.ApiError("timeline", 1))
        code, out = _run(gh)
        self.assertEqual(code, 1, out)

    def test_workflow_path_must_equal_exactly(self) -> None:
        run = _job("ci-required", "success", "x", "evil/.github/workflows/ci.yml")
        self.assertFalse(main_guard.workflow_matches("ci-required", run))

    def test_check_runs_jq_keeps_workflow_path(self) -> None:
        seen: list[list[str]] = []

        def runner(cmd: list[str], input_text: str | None = None) -> tuple[int, str, str]:
            seen.append(cmd)
            joined = " ".join(cmd)
            if "actions/runs?" in joined:
                return 0, (
                    '{"id": 9, "path": ".github/workflows/ci.yml", "event": "pull_request", '
                    '"created_at": "2026-11-19T00:00:00Z", "name": "CI", "conclusion": "failure", '
                    '"pull_requests": [{"number": 4, "base_ref": "main"}]}\n'
                    '{"id": 10, "path": ".github/workflows/fork.yml", "event": "pull_request", '
                    '"created_at": "2026-11-19T03:00:00Z", "name": "Fork", "conclusion": "success", '
                    '"pull_requests": [{"number": 4, "base_ref": "main"}]}\n'
                ), ""
            if "/runs/9/jobs" in joined:
                return 0, (
                    '{"name": "ci-required", "conclusion": "failure", '
                    '"started_at": "2026-11-19T00:00:00Z", "completed_at": "2026-11-19T00:01:00Z"}\n'
                ), ""
            if "/runs/10/jobs" in joined:
                return 0, (
                    '{"name": "ci-required", "conclusion": "success", '
                    '"started_at": "2026-11-19T03:00:00Z", "completed_at": "2026-11-19T03:01:00Z"}\n'
                ), ""
            return 1, "", "unexpected"

        rows = main_guard.GhApi(runner).check_runs("o/r", "abc")
        jq = " ".join(part for cmd in seen for part in cmd if part.startswith("."))
        self.assertIn("path", jq)
        self.assertIn("name", jq)
        self.assertNotIn("rulesets", jq)
        chosen = main_guard.latest_run(rows, "ci-required", 4)
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual(chosen["conclusion"], "failure")
        self.assertEqual(chosen["workflow"], ".github/workflows/ci.yml")

    def test_pulls_command_does_not_call_rulesets(self) -> None:
        seen: list[list[str]] = []

        def runner(cmd: list[str], input_text: str | None = None) -> tuple[int, str, str]:
            seen.append(cmd)
            return 0, "", ""

        self.assertEqual(main_guard.GhApi(runner).pulls("o/r", "abc"), [])
        blob = " ".join(" ".join(cmd) for cmd in seen)
        self.assertIn("repos/o/r/commits/abc/pulls", blob)
        self.assertNotIn("rulesets", blob)

    def test_workflow_embeds_script_and_does_not_checkout(self) -> None:
        wf = (ROOT / ".github/workflows/main-guard.yml").read_text()
        script = (ROOT / "scripts/main_guard.py").read_text()
        start = wf.index("python3 -I - <<'PY'\n") + len("python3 -I - <<'PY'\n")
        end = wf.index("\n          PY\n", start)
        lines: list[str] = []
        for line in wf[start:end].split("\n"):
            if line == "":
                lines.append("")
            else:
                self.assertTrue(line.startswith("          "), line)
                lines.append(line[10:])
        embedded = "\n".join(lines) + "\n"
        self.assertEqual(embedded, script)
        self.assertNotIn("actions/checkout", wf)
        self.assertNotIn("/rulesets", wf)
        self.assertNotIn("git revert", wf)
        for key in ("contents: read", "issues: write", "checks: read", "pull-requests: read"):
            self.assertIn(key, wf)
        self.assertNotIn("contents: write", wf)


if __name__ == "__main__":
    unittest.main()
