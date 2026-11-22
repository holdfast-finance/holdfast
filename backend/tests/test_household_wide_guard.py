"""Keep the household-wide database setting in db_context and off API calls."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = Path("backend/app")
API = APP / "api"
DB_CONTEXT = APP / "db_context.py"


def setting_violations(source: str, path: Path) -> list[str]:
    """Find raw setting references in application source at a repo-relative path."""
    if not path.is_relative_to(APP) or path == DB_CONTEXT:
        return []
    return [
        f"{path}:{line_number}: app.household_wide is reserved for db_context.py"
        for line_number, line in enumerate(source.split("\n"), start=1)
        if "app.household_wide" in line
    ]


def api_keyword_violations(source: str, path: Path) -> list[str]:
    """Find explicit household_wide arguments on calls in an API module."""
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


def test_api_passes_only_literal_false_for_household_wide() -> None:
    assert (ROOT / API).is_dir()
    hits = [
        hit
        for path in sorted((ROOT / API).rglob("*.py"))
        for hit in api_keyword_violations(path.read_text(encoding="utf-8"), path.relative_to(ROOT))
    ]
    assert hits == [], "\n".join(hits)


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


def test_api_keyword_checker_catches_nonliteral_false_values() -> None:
    path = API / "example.py"
    for value in ("True", "flag", "not x", "0"):
        source = f"scoped_session(\n    household_wide={value},\n)"
        assert api_keyword_violations(source, path) == [
            f"{path}:2: household_wide must be literal False",
        ], value


def test_api_keyword_checker_allows_literal_false_and_ignores_non_calls() -> None:
    source = """
scoped_session(household_wide=False)
scoped_session()
# scoped_session(household_wide=True)
example = "scoped_session(household_wide=True)"
def example_function(household_wide=True):
    pass
"""
    assert api_keyword_violations(source, API / "example.py") == []
