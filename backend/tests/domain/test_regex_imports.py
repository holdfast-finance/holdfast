"""ADR 0024: no backtracking regex engine near rules, and RE2 only in rule_regex.py."""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
FORBIDDEN = {"re", "regex", "sre_compile", "sre_parse", "_sre", "fnmatch"}
GUARDED = (
    "domain/rule_regex.py",
    "domain/rules.py",
    "domain/merchant.py",
    "services/rules.py",
)


def _imported(source: str) -> set[str]:
    """Top-level names imported, including string-argument dynamic imports."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            dynamic = (isinstance(func, ast.Name) and func.id == "__import__") or (
                isinstance(func, ast.Attribute) and func.attr == "import_module"
            )
            arg = node.args[0]
            if dynamic and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                names.add(arg.value.split(".")[0])
    return names


def test_detector_sees_every_import_form() -> None:
    src = "import re\nfrom fnmatch import x\n__import__('regex')\nimportlib.import_module('_sre.x')"
    assert _imported(src) == {"re", "fnmatch", "regex", "_sre"}


def test_guarded_modules_never_import_a_regex_engine() -> None:
    for rel in GUARDED:
        path = APP / rel
        if path.exists():  # services/rules.py arrives in C3e
            assert not _imported(path.read_text()) & FORBIDDEN, rel


def test_the_guarded_modules_exist() -> None:
    for rel in GUARDED[:3]:
        assert (APP / rel).exists(), rel


def test_re2_is_imported_only_by_rule_regex() -> None:
    offenders = [
        str(p.relative_to(APP))
        for p in APP.rglob("*.py")
        if p.name != "rule_regex.py" and "re2" in _imported(p.read_text())
    ]
    assert offenders == []
