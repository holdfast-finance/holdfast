"""Keep the household-wide job paths in the worker, and handlers on their context (ADR 0016).

- ``app.services.jobs._enqueue`` with the household-wide flag checks a payload's references as
  a household-wide job, so any row of the household passes. Only the runner calls it, through
  ``app.jobs.runner.enqueue_household_wide``, and only ``app/jobs`` and ``app/worker`` may name
  that. import-linter enforces the module side (only those import ``app.jobs.runner``); these
  scans catch the names, including in a package the contract doesn't list yet.
- Handlers get their sessions from ``JobContext`` (bound to the run's household). import-linter
  keeps ``app.db`` and ``app.db_context`` out of ``app/jobs/handlers``; the scan below rejects the
  ways around that: reaching into ``HouseholdSessions._factory``, the engine (``get_bind``,
  ``.bind``) or the RLS settings (``set_config``, in code or in SQL text).
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = Path("app")
HANDLERS = APP / "jobs" / "handlers"
WORKER_SIDE = (APP / "jobs", APP / "worker")
PRIVATE_ALLOWED = {APP / "services" / "jobs.py", APP / "jobs" / "runner.py"}
HANDLER_FORBIDDEN = ("_factory", "get_bind", "bind", "set_config")


def _names(tree: ast.AST) -> list[tuple[int, str]]:
    """Every identifier a module uses: names, attributes and imported names, with lines."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            found.append((node.lineno, node.attr))
        elif isinstance(node, ast.Name):
            found.append((node.lineno, node.id))
        elif isinstance(node, ast.ImportFrom | ast.Import):
            found.extend((node.lineno, alias.name.rsplit(".", 1)[-1]) for alias in node.names)
    return found


def name_uses(source: str, path: Path, name: str) -> list[int]:
    return [line for line, n in _names(ast.parse(source, filename=str(path))) if n == name]


def private_enqueue_uses(source: str, path: Path) -> list[str]:
    """References to ``_enqueue`` outside services/jobs.py and the runner."""
    if path in PRIVATE_ALLOWED:
        return []
    return [
        f"{path}:{line}: _enqueue is worker-only; use jobs.enqueue"
        for line in name_uses(source, path, "_enqueue")
    ]


def household_wide_enqueue_uses(source: str, path: Path) -> list[str]:
    """References to ``enqueue_household_wide`` outside app/jobs and app/worker."""
    if any(path.is_relative_to(top) for top in WORKER_SIDE):
        return []
    return [
        f"{path}:{line}: enqueue_household_wide is for app/jobs and app/worker only"
        for line in name_uses(source, path, "enqueue_household_wide")
    ]


def handler_escapes(source: str, path: Path) -> list[str]:
    """In a handler module: names that reach past JobContext to raw sessions or RLS settings."""
    if not path.is_relative_to(HANDLERS):
        return []
    tree = ast.parse(source, filename=str(path))
    hits = [
        f"{path}:{line}: handlers may not use {name}"
        for line, name in _names(tree)
        if name in HANDLER_FORBIDDEN
    ]
    hits += [
        f"{path}:{node.lineno}: handlers may not use set_config"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "set_config" in node.value
    ]
    return hits


def _scan(check: object) -> list[str]:
    assert callable(check)
    return [
        hit
        for path in sorted((ROOT / APP).rglob("*.py"))
        for hit in check(path.read_text(encoding="utf-8"), path.relative_to(ROOT))
    ]


def test_only_the_runner_uses_the_household_wide_enqueue() -> None:
    hits = _scan(private_enqueue_uses)
    assert hits == [], "\n".join(hits)


def test_only_jobs_and_worker_name_enqueue_household_wide() -> None:
    hits = _scan(household_wide_enqueue_uses)
    assert hits == [], "\n".join(hits)


def test_handlers_never_reach_past_their_context() -> None:
    assert (ROOT / HANDLERS).is_dir()
    hits = _scan(handler_escapes)
    assert hits == [], "\n".join(hits)


def test_the_checkers_find_planted_uses() -> None:
    planted = Path("app/api/sneaky.py")
    for source in (
        "from app.services import jobs\nawait jobs._enqueue(f, q, s, p)\n",
        "from app.services.jobs import _enqueue\n",
        "enqueue = _enqueue\n",
    ):
        assert private_enqueue_uses(source, planted), source
    assert private_enqueue_uses("await jobs._enqueue()\n", APP / "jobs" / "runner.py") == []

    for path in (planted, APP / "services" / "x.py", APP / "security" / "x.py"):
        for source in (
            "from app.jobs.runner import enqueue_household_wide\n",
            "from app.jobs import runner\nawait runner.enqueue_household_wide(f, q, s, p)\n",
        ):
            assert household_wide_enqueue_uses(source, path), (path, source)
    for path in (APP / "jobs" / "handlers" / "x.py", APP / "worker" / "main.py"):
        source = "from app.jobs.runner import enqueue_household_wide\n"
        assert household_wide_enqueue_uses(source, path) == []


def test_the_handler_checker_finds_escapes() -> None:
    handler = HANDLERS / "example.py"
    for source in (
        "f = ctx.factory._factory\n",
        "engine = ctx.session.get_bind()\n",
        "engine = ctx.session.bind\n",
        "await ctx.session.execute(text(\"SELECT set_config('app.household_id', :h, true)\"))\n",
        "from sqlalchemy import func\nfunc.set_config('app.person_id', '', True)\n",
    ):
        assert handler_escapes(source, handler), source
    clean = "async with ctx.factory() as s:\n    await s.execute(select(Account))\n"
    assert handler_escapes(clean, handler) == []
    # Outside handlers the checker doesn't apply (the runner builds the context).
    assert handler_escapes("f = x._factory\n", APP / "jobs" / "registry.py") == []
