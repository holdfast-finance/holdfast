"""Billed-minute total for make ci-minutes."""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import ci_minutes  # noqa: E402


class MinutesTests(unittest.TestCase):
    def test_month_start_and_rounding(self) -> None:
        self.assertEqual(
            ci_minutes.month_start(datetime(2026, 11, 19, tzinfo=UTC)),
            "2026-11-01",
        )
        self.assertEqual(ci_minutes.format_budget(0), "0/2000")
        self.assertEqual(ci_minutes.format_budget(60_000), "1/2000")
        self.assertEqual(ci_minutes.format_budget(60_001), "2/2000")

    def test_sums_ubuntu_ms_for_this_month_only(self) -> None:
        def run(cmd: list[str]) -> tuple[int, str, str]:
            joined = " ".join(cmd)
            if joined.endswith("/timing --jq .billable.UBUNTU.total_ms // 0") or "timing" in joined:
                self.assertIn("/runs/1/timing", joined)
                self.assertNotIn("/runs/2/timing", joined)
                return 0, "60001\n", ""
            self.assertIn("created=>=2026-11-01", cmd)
            return 0, "1\t2026-11-02T00:00:00Z\n2\t2026-10-31T23:00:00Z\n", ""

        text, warnings = ci_minutes.compute(run, "o/r", "2026-11-01")
        self.assertEqual(text, "2/2000")
        self.assertEqual(warnings, [])

    def test_zero_billable_ms_warns(self) -> None:
        def run(cmd: list[str]) -> tuple[int, str, str]:
            if "timing" in " ".join(cmd):
                return 0, "0\n", ""
            return 0, "4\t2026-11-02T00:00:00Z\n", ""

        text, warnings = ci_minutes.compute(run, "o/r", "2026-11-01")
        self.assertEqual(text, "0/2000")
        self.assertEqual(len(warnings), 1)
        self.assertIn("0 billed ms", warnings[0])

    def test_retries_without_created_filter(self) -> None:
        calls = {"list": 0}

        def run(cmd: list[str]) -> tuple[int, str, str]:
            joined = " ".join(cmd)
            if "timing" in joined:
                return 1, "", "unavailable"
            calls["list"] += 1
            if calls["list"] == 1:
                self.assertIn("created=>=2026-11-01", cmd)
                return 1, "", "bad filter"
            return 0, "9\t2026-11-03T00:00:00Z\n", ""

        text, warnings = ci_minutes.compute(run, "o/r", "2026-11-01")
        self.assertEqual(text, "0/2000")
        self.assertEqual(calls["list"], 2)
        self.assertEqual(warnings, ["ci-minutes: timing unavailable for run 9"])


if __name__ == "__main__":
    unittest.main()
