#!/usr/bin/env python3
"""Aggregate CI into the stable ``ci-required`` check.

``checks`` must succeed (a missing manifest fails that job). ``backend`` and
``web`` may be skipped only when the plan says so (``paths`` or
``main-uptodate``). Any other skip, failure, or cancellation fails the gate.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

SKIP_REASONS = {"paths", "main-uptodate"}
HEAVY = ("backend", "web")


def evaluate(needs: dict[str, Any]) -> list[str]:
    checks = needs.get("checks") or {}
    if checks.get("result") != "success":
        return [f"checks: {checks.get('result')}"]
    outputs = checks.get("outputs") or {}
    errors: list[str] = []
    for job in HEAVY:
        result = (needs.get(job) or {}).get("result")
        decision = outputs.get(job)
        reason = outputs.get(f"{job}_reason") or ""
        if decision == "true":
            if result != "success":
                errors.append(f"{job}: {result} (required, plan {reason or 'unset'})")
        elif decision == "false":
            if reason not in SKIP_REASONS:
                errors.append(f"{job}: skipped without an accepted reason ({reason!r})")
            elif result not in ("success", "skipped"):
                errors.append(f"{job}: {result} (skip reason {reason})")
        else:
            errors.append(f"{job}: missing plan decision")
    return errors


def main() -> int:
    needs = json.loads(os.environ["RESULTS"])
    errors = evaluate(needs)
    for error in errors:
        print(f"::error::{error}")
    if errors:
        return 1
    checks = needs["checks"]["outputs"]
    print(
        "ci-required: pass "
        f"(backend {checks.get('backend')} {checks.get('backend_reason')}; "
        f"web {checks.get('web')} {checks.get('web_reason')})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
