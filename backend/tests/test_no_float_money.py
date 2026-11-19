"""AST lint: no floats in money paths (domain, integrations, importers, services, tax)."""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
SCANNED = ("domain", "integrations", "importers", "services", "tax")


def violations(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "float":
                found.append(f"line {node.lineno}: float() call")
            if node.func.id == "Decimal" and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, float):
                    found.append(f"line {node.lineno}: Decimal(float literal)")
        elif isinstance(node, ast.BinOp):
            for side in (node.left, node.right):
                if isinstance(side, ast.Constant) and isinstance(side.value, float):
                    found.append(f"line {node.lineno}: float literal in arithmetic")
        elif isinstance(node, ast.AnnAssign | ast.arg):
            ann = node.annotation
            if ann is not None and any(
                isinstance(n, ast.Name) and n.id == "float" for n in ast.walk(ann)
            ):
                found.append(f"line {node.lineno}: float annotation")
    return found


def test_money_paths_are_float_free() -> None:
    bad: list[str] = []
    for pkg in SCANNED:
        root = APP / pkg
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            bad += [f"{path.relative_to(APP)}: {v}" for v in violations(path.read_text())]
    assert not bad, "\n".join(bad)


def test_lint_catches_seeded_violations(tmp_path: Path) -> None:
    seeded = tmp_path / "bad.py"
    seeded.write_text(
        "from decimal import Decimal\n"
        "def f(x: float) -> Decimal:\n"
        "    y: float = 1\n"
        "    z = Decimal('1') * 1.5\n"
        "    return Decimal(float(x)) + Decimal(2.5)\n"
    )
    joined = "\n".join(violations(seeded.read_text()))
    for needle in (
        "float annotation",
        "float literal in arithmetic",
        "float() call",
        "Decimal(float",
    ):
        assert needle in joined
    assert violations("x = Decimal('1.5') + 2\n") == []
