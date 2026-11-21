"""The matcher must not be reachable from a request or a scheduler until C6 adds a rejection
record (see app/services/transfer_pairing.py)."""

from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
NAMES = re.compile(r"pair_transfers|apply_transfer_pair|transfer_pairing")
GUARDED = ("api", "jobs", "worker", "agent")
SINGLE_FILES = ("main.py",)


def test_matcher_is_not_wired_to_api_or_jobs() -> None:
    assert all((APP / top).is_dir() for top in GUARDED)
    hits = [
        f"{path.relative_to(APP).as_posix()}:{n}"
        for top in GUARDED
        for path in sorted((APP / top).rglob("*.py"))
        for n, line in enumerate(path.read_text().splitlines(), start=1)
        if NAMES.search(line)
    ]
    for name in SINGLE_FILES:
        for n, line in enumerate((APP / name).read_text().splitlines(), start=1):
            if NAMES.search(line):
                hits.append(f"{name}:{n}")
    assert hits == [], "transfer pairing wired to a request or job path: " + ", ".join(hits)
