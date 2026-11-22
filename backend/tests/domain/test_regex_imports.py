"""ADR 0024: no backtracking regex engine near rules, and RE2 only in rule_regex.py."""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
FORBIDDEN = {"re", "regex"}


def _imported(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_rule_modules_never_import_re_or_regex() -> None:
    for rel in ("domain/rule_regex.py", "domain/rules.py", "services/rules.py"):
        path = APP / rel
        if path.exists():  # services/rules.py arrives in C3e
            assert not _imported(path) & FORBIDDEN, rel


def test_the_guarded_modules_exist() -> None:
    assert (APP / "domain/rule_regex.py").exists()
    assert (APP / "domain/rules.py").exists()


def test_re2_is_imported_only_by_rule_regex() -> None:
    offenders = [
        str(p.relative_to(APP))
        for p in APP.rglob("*.py")
        if p.name != "rule_regex.py" and {"re2", "google"} & _imported(p) and "re2" in p.read_text()
    ]
    assert offenders == []
