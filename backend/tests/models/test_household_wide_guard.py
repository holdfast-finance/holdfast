"""Request handlers never widen person visibility (ADR 0008).

``household_wide=True`` lets a session see and write every person's accounts. It's for
background jobs only. This grep-based guard fails if it appears anywhere under ``app/api/``.
"""

from __future__ import annotations

import re
from pathlib import Path

API = Path(__file__).resolve().parents[2] / "app" / "api"
WIDE = re.compile(r"household_wide\s*=\s*True|set_config\(\s*'app\.household_wide'")


def test_api_never_sets_household_wide() -> None:
    assert API.is_dir()
    hits = [
        f"{path.relative_to(API.parent.parent)}:{n}"
        for path in sorted(API.rglob("*.py"))
        for n, line in enumerate(path.read_text().splitlines(), start=1)
        if WIDE.search(line)
    ]
    assert hits == [], "household_wide=True in request handlers: " + ", ".join(hits)


def test_guard_pattern_catches_the_obvious_forms() -> None:
    for line in (
        "scoped_session(f, hid, pid, household_wide=True)",
        "household_wide = True",
        "SELECT set_config('app.household_wide', 'on', true)",
    ):
        assert WIDE.search(line), line
