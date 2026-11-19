"""ci-required treats a reasoned skip as pass and a missing manifest as fail."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import ci_required_eval  # noqa: E402


def needs(checks_result: str, outputs: dict, backend: str, web: str) -> dict:
    return {
        "checks": {"result": checks_result, "outputs": outputs},
        "backend": {"result": backend, "outputs": {}},
        "web": {"result": web, "outputs": {}},
    }


class RequiredEvalTests(unittest.TestCase):
    def test_path_skip_passes(self) -> None:
        outputs = {
            "backend": "false", "web": "false",
            "backend_reason": "paths", "web_reason": "paths",
        }
        self.assertEqual(
            ci_required_eval.evaluate(needs("success", outputs, "skipped", "skipped")),
            [],
        )

    def test_main_uptodate_skip_passes(self) -> None:
        outputs = {
            "backend": "false", "web": "false",
            "backend_reason": "main-uptodate", "web_reason": "main-uptodate",
        }
        self.assertEqual(
            ci_required_eval.evaluate(needs("success", outputs, "skipped", "skipped")),
            [],
        )

    def test_missing_manifest_is_checks_failure(self) -> None:
        errors = ci_required_eval.evaluate(needs("failure", {}, "skipped", "skipped"))
        self.assertEqual(errors, ["checks: failure"])

    def test_required_job_skipped_fails(self) -> None:
        outputs = {
            "backend": "true", "web": "false",
            "backend_reason": "run", "web_reason": "paths",
        }
        errors = ci_required_eval.evaluate(needs("success", outputs, "skipped", "skipped"))
        self.assertTrue(any(item.startswith("backend:") for item in errors))

    def test_skip_without_reason_fails(self) -> None:
        outputs = {"backend": "false", "web": "false", "backend_reason": "", "web_reason": "paths"}
        errors = ci_required_eval.evaluate(needs("success", outputs, "skipped", "skipped"))
        self.assertTrue(any("accepted reason" in item for item in errors))

    def test_green_required_jobs_pass(self) -> None:
        outputs = {
            "backend": "true", "web": "true",
            "backend_reason": "run", "web_reason": "unknown",
        }
        self.assertEqual(
            ci_required_eval.evaluate(needs("success", outputs, "success", "success")),
            [],
        )


if __name__ == "__main__":
    unittest.main()
