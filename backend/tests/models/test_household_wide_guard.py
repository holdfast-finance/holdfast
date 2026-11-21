"""Request-path code never widens person visibility (ADR 0008).

``household_wide=True`` lets a session see and write every person's accounts. It's for
background jobs only. This grep-based guard fails if it appears anywhere a request can reach:
``app/api/``, ``app/services/``, ``app/domain/`` and ``app/security/``. Modules that may use it
(the db_context definitions, future worker/job modules) are listed in ``ALLOWED`` explicitly.
"""

from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
SCANNED = ("api", "services", "domain", "security")
# Paths relative to app/. Add a worker/job module here only with a comment saying why it needs
# the household-wide view. app/db_context.py (where the flag is defined) sits outside SCANNED.
ALLOWED: frozenset[str] = frozenset()
WIDE = re.compile(
    r"household_wide\s*=\s*True|set_config\(\s*'app\.household_wide'"
    r"|info\[\s*[\"']household_wide[\"']\s*\]\s*=\s*True"
)


def test_request_paths_never_set_household_wide() -> None:
    assert (APP / "api").is_dir() and (APP / "services").is_dir()
    hits = [
        f"{rel}:{n}"
        for top in SCANNED
        if (APP / top).is_dir()
        for path in sorted((APP / top).rglob("*.py"))
        if (rel := path.relative_to(APP).as_posix()) not in ALLOWED
        for n, line in enumerate(path.read_text().splitlines(), start=1)
        if WIDE.search(line)
    ]
    assert hits == [], "household_wide=True on a request path: " + ", ".join(hits)


def test_allowlist_names_real_files() -> None:
    assert all((APP / rel).is_file() for rel in ALLOWED)


def test_guard_pattern_catches_the_obvious_forms() -> None:
    for line in (
        "scoped_session(f, hid, pid, household_wide=True)",
        "household_wide = True",
        "SELECT set_config('app.household_wide', 'on', true)",
    ):
        assert WIDE.search(line), line
