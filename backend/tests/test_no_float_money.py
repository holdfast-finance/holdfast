"""AST lint: no floats in money paths.

Flags every float literal, every use of the name or attribute ``float`` (calls, annotations,
isinstance...), ``Decimal.from_float`` and any ``from_float`` attribute. Escape hatch for a
legitimate non-money float (a timeout, say): a trailing ``# money-lint: allow <reason>`` comment
on the offending line. Keep these rare and justified.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
SCANNED = ("domain", "integrations", "importers", "services", "tax", "models", "schemas", "api")
PRAGMA = re.compile(r"#\s*money-lint:\s*allow\s+\S+")


def violations(source: str) -> list[str]:
    lines = source.splitlines()
    found: list[str] = []

    def add(node: ast.AST, what: str) -> None:
        lineno = getattr(node, "lineno", 0)
        if 0 < lineno <= len(lines) and PRAGMA.search(lines[lineno - 1]):
            return
        found.append(f"line {lineno}: {what}")

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            add(node, "float literal")
        elif isinstance(node, ast.Name) and node.id == "float":
            add(node, "use of float")
        elif isinstance(node, ast.Attribute) and node.attr in ("float", "from_float"):
            add(node, f"use of {node.attr}")
    return found


def test_scanned_dirs_exist() -> None:
    missing = [d for d in SCANNED if not (APP / d).is_dir()]
    assert not missing, f"update SCANNED, these app dirs are gone: {missing}"


def test_money_paths_are_float_free() -> None:
    bad: list[str] = []
    for pkg in SCANNED:
        for path in sorted((APP / pkg).rglob("*.py")):
            bad += [f"{path.relative_to(APP)}: {v}" for v in violations(path.read_text())]
    assert not bad, "\n".join(bad)


@pytest.mark.parametrize(
    ("source", "needle"),
    [
        ("x = 1.5\n", "float literal"),
        ("x = -1.5\n", "float literal"),
        ("x += 0.5\n", "float literal"),
        ("x = y * 2.0\n", "float literal"),
        ("x = Decimal('1') * 1.5\n", "float literal"),
        ("x = float(y)\n", "use of float"),
        ("def f(x: float): ...\n", "use of float"),
        ("def f() -> float: ...\n", "use of float"),
        ("def f() -> list[float]: ...\n", "use of float"),
        ("x: float = 1\n", "use of float"),
        ("x = isinstance(y, float)\n", "use of float"),
        ("x = builtins.float(y)\n", "use of float"),
        ("x = Decimal.from_float(y)\n", "use of from_float"),
        ("x = decimal.Decimal.from_float(y)\n", "use of from_float"),
        ("x = decimal.Decimal(2.5)\n", "float literal"),
        ("x = Decimal(value=2.5)\n", "float literal"),
        ("f(rate=0.1)\n", "float literal"),
    ],
)
def test_lint_catches_bypass(source: str, needle: str) -> None:
    found = violations(source)
    assert any(needle in v for v in found), found


def test_lint_allows_clean_code_and_pragma() -> None:
    assert violations("x = Decimal('1.5') + 2\n") == []
    assert violations("T = 2.0  # money-lint: allow http timeout seconds\n") == []
    assert violations("T = 2.0  # money-lint: allow\n") != []


def test_seeded_file_on_disk(tmp_path: Path) -> None:
    seeded = tmp_path / "bad.py"
    seeded.write_text("def f(x: float) -> float:\n    return x * 1.5\n")
    assert len(violations(seeded.read_text())) >= 3
