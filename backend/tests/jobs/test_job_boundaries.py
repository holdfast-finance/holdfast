"""Keep the household-wide enqueue path in the worker (ADR 0016, Bulwark #32).

``app.services.jobs._enqueue(household_wide=True)`` checks a payload's references as a
household-wide job, so any row of the household passes. Only the worker's own schedulers may do
that, through ``app.jobs.runner.enqueue_household_wide``. import-linter keeps ``app.api`` and
``app.services`` from importing the runner; this test keeps everything else from calling
``_enqueue`` directly.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = Path("app")
ALLOWED = {APP / "services" / "jobs.py", APP / "jobs" / "runner.py"}
NAME = "_enqueue"


def private_enqueue_uses(source: str, path: Path) -> list[str]:
    """Every reference to ``_enqueue`` (call, attribute, import) in a module outside ALLOWED."""
    if path in ALLOWED:
        return []
    hits: list[str] = []
    for node in ast.walk(ast.parse(source, filename=str(path))):
        line = getattr(node, "lineno", 0)
        if (
            (isinstance(node, ast.Attribute) and node.attr == NAME)
            or (isinstance(node, ast.Name) and node.id == NAME)
            or (isinstance(node, ast.ImportFrom) and any(a.name == NAME for a in node.names))
        ):
            hits.append(f"{path}:{line}: {NAME} is worker-only; use jobs.enqueue")
    return hits


def test_only_the_runner_uses_the_household_wide_enqueue() -> None:
    hits = [
        hit
        for path in sorted((ROOT / APP).rglob("*.py"))
        for hit in private_enqueue_uses(path.read_text(encoding="utf-8"), path.relative_to(ROOT))
    ]
    assert hits == [], "\n".join(hits)


def test_the_checker_finds_planted_uses() -> None:
    planted = Path("app/api/sneaky.py")
    for source in (
        "from app.services import jobs\nawait jobs._enqueue(f, q, s, p)\n",
        "from app.services.jobs import _enqueue\n",
        "enqueue = _enqueue\n",
    ):
        assert private_enqueue_uses(source, planted), source
    assert private_enqueue_uses("await jobs._enqueue()\n", APP / "jobs" / "runner.py") == []
