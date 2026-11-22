"""Keep the household-wide database setting in db_context, and its keyword on an allowlist.

Two checks over ``backend/app``. The raw ``app.household_wide`` setting appears only in
``db_context.py``. And a ``household_wide=`` keyword on any call must be the literal ``False``
unless the module is in ``KEYWORD_ALLOWED``, each with the reason it needs the household-wide
view. ``tests/models/test_household_wide_guard.py`` (a line grep for literal ``True`` on request
paths) stays as a second, independent net.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = Path("backend/app")
API = APP / "api"
DB_CONTEXT = APP / "db_context.py"
# Modules that may pass household_wide= with anything but literal False. Add one only with a
# reason; a request path (app/api) never belongs here.
KEYWORD_ALLOWED: dict[Path, str] = {
    DB_CONTEXT: "defines the flag and forwards it into the session context",
    APP / "jobs" / "runner.py": "handler sessions and enqueue_household_wide (worker-only)",
    APP / "jobs" / "registry.py": "HouseholdSessions: a handler's side sessions, one household",
    APP / "services" / "jobs.py": (
        "_enqueue forwards the flag; only the runner passes True (tests/jobs/test_job_boundaries)"
    ),
    APP / "services" / "statement_import.py": (
        "import_statement forwards the import job's flag; request callers never pass it"
    ),
    APP
    / "importers"
    / "store_sweep.py": "orphan-file sweep reads every account (a background job)",
    APP / "security" / "rotation.py": "rotate-keys re-seals every person's connections (ADR 0015)",
}


def setting_violations(source: str, path: Path) -> list[str]:
    """Find raw setting references in application source at a repo-relative path."""
    if not path.is_relative_to(APP) or path == DB_CONTEXT:
        return []
    return [
        f"{path}:{line_number}: app.household_wide is reserved for db_context.py"
        for line_number, line in enumerate(source.split("\n"), start=1)
        if "app.household_wide" in line
    ]


def keyword_violations(source: str, path: Path) -> list[str]:
    """Find household_wide= call arguments other than literal False, outside the allowlist."""
    if path in KEYWORD_ALLOWED:
        return []
    hits = []
    for node in ast.walk(ast.parse(source, filename=str(path))):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != "household_wide":
                continue
            if isinstance(keyword.value, ast.Constant) and keyword.value.value is False:
                continue
            hits.append(f"{path}:{keyword.lineno}: household_wide must be literal False")
    return hits


def test_only_db_context_references_household_wide_setting() -> None:
    assert (ROOT / DB_CONTEXT).is_file()
    # Alembic lives outside app/, so its migrations are outside this scan.
    hits = [
        hit
        for path in sorted((ROOT / APP).rglob("*.py"))
        for hit in setting_violations(path.read_text(encoding="utf-8"), path.relative_to(ROOT))
    ]
    assert hits == [], "\n".join(hits)


def test_app_passes_only_literal_false_for_household_wide_outside_the_allowlist() -> None:
    assert (ROOT / API).is_dir()
    hits = [
        hit
        for path in sorted((ROOT / APP).rglob("*.py"))
        for hit in keyword_violations(path.read_text(encoding="utf-8"), path.relative_to(ROOT))
    ]
    assert hits == [], "\n".join(hits)


def test_keyword_allowlist_names_real_files_and_no_request_path() -> None:
    for path, reason in KEYWORD_ALLOWED.items():
        assert (ROOT / path).is_file(), path
        assert not path.is_relative_to(API), path
        assert reason.strip(), path


def test_setting_checker_catches_strings_and_comments() -> None:
    path = APP / "example.py"
    source = '\nsetting = "app.household_wide"\n# app.household_wide\n'
    assert setting_violations(source, path) == [
        f"{path}:2: app.household_wide is reserved for db_context.py",
        f"{path}:3: app.household_wide is reserved for db_context.py",
    ]


def test_setting_checker_allows_context_module_and_migrations() -> None:
    source = 'setting = "app.household_wide"'
    assert setting_violations(source, DB_CONTEXT) == []
    assert setting_violations(source, Path("backend/migrations/versions/example.py")) == []
    assert setting_violations('setting = "app.person_id"', APP / "example.py") == []


def test_keyword_checker_catches_nonliteral_false_values_anywhere_in_app() -> None:
    for path in (API / "example.py", APP / "services" / "example.py", APP / "jobs" / "other.py"):
        for value in ("True", "flag", "not x", "0"):
            source = f"scoped_session(\n    household_wide={value},\n)"
            assert keyword_violations(source, path) == [
                f"{path}:2: household_wide must be literal False",
            ], (path, value)


def test_keyword_checker_skips_allowlisted_modules() -> None:
    source = "scoped_session(f, h, None, household_wide=flag)"
    assert keyword_violations(source, APP / "jobs" / "runner.py") == []
    assert keyword_violations(source, APP / "services" / "jobs.py") == []
    assert keyword_violations(source, APP / "services" / "imports.py") != []


def test_keyword_checker_allows_literal_false_and_ignores_non_calls() -> None:
    source = """
scoped_session(household_wide=False)
scoped_session()
# scoped_session(household_wide=True)
example = "scoped_session(household_wide=True)"
def example_function(household_wide=True):
    pass
"""
    assert keyword_violations(source, API / "example.py") == []
