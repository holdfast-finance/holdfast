"""Hard rule 11, enforced by scan: no code path in the crypto packages can take a seed phrase or a
private key, and nothing depends on the spend flag.

Walks the crypto source files with ``ast`` and fails on any identifier, parameter, attribute,
keyword, dict key or table column that names secret material (``mnemonic``, ``seed``, ``xprv``,
``private_key``, ``wif`` ...), and on any reference to ``FEATURE_CRYPTO_SPEND`` / PSBT / signing.
The guard module is the one place that talks about private keys, to refuse them.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import app.models  # noqa: F401  (registers every table)
from app.db import Base

APP = Path(__file__).resolve().parents[1] / "app"

CRYPTO_SOURCES = sorted(
    {
        *APP.glob("domain/crypto/**/*.py"),
        *APP.glob("integrations/crypto/**/*.py"),
        *APP.glob("services/crypto*.py"),
        *APP.glob("api/routes_crypto*.py"),
        *APP.glob("schemas/crypto*.py"),
        APP / "models" / "crypto.py",
    }
)
ALLOWED = {APP / "domain" / "crypto" / "guard.py"}

SECRET_NAME = re.compile(
    r"(?<![a-z])(mnemonic|seed|bip39|xprv|yprv|zprv|tprv|privkey|priv_key|private_key|"
    r"secret_key|secret_exponent|wif|entropy)(?![a-z])",
    re.IGNORECASE,
)
SPEND_NAME = re.compile(r"(crypto_spend|psbt|sign_tx|sign_transaction|signer|broadcast)", re.I)


def _names(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.append(node.id)
        elif isinstance(node, ast.Attribute):
            found.append(node.attr)
        elif isinstance(node, ast.arg | ast.keyword) and node.arg:
            found.append(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.append(node.name)
        elif isinstance(node, ast.alias):
            found.append(node.name.split(".")[-1])
            if node.asname:
                found.append(node.asname)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            # A short string that is itself a name (dict key, column, alias): "mnemonic".
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,40}", node.value)
        ):
            found.append(node.value)
    return found


def test_the_scan_sees_the_crypto_packages() -> None:
    names = {p.relative_to(APP).as_posix() for p in CRYPTO_SOURCES}
    assert "domain/crypto/bitcoin.py" in names and "models/crypto.py" in names


@pytest.mark.parametrize(
    "path",
    [p for p in CRYPTO_SOURCES if p not in ALLOWED],
    ids=lambda p: p.relative_to(APP).as_posix(),
)
def test_no_input_path_can_hold_secret_material(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad = sorted({n for n in _names(tree) if SECRET_NAME.search(n) or SPEND_NAME.search(n)})
    assert bad == [], f"{path.name} names secret or spend material: {bad}"


def test_the_guard_only_refuses_it_never_returns_or_stores_it() -> None:
    tree = ast.parse((APP / "domain" / "crypto" / "guard.py").read_text(encoding="utf-8"))
    functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    returns_text = [
        f.name
        for f in functions
        if f.returns is not None and ast.unparse(f.returns) not in {"None", "bool"}
    ]
    assert returns_text == [], "guard functions must return only None or bool"


def test_no_table_has_a_column_for_secret_material() -> None:
    offenders = [
        f"{t.name}.{c.name}"
        for t in Base.metadata.tables.values()
        for c in t.c
        if SECRET_NAME.search(c.name) or SPEND_NAME.search(c.name)
    ]
    assert offenders == []


def test_nothing_in_the_crypto_packages_reads_the_spend_flag() -> None:
    for path in CRYPTO_SOURCES:
        assert "feature_crypto_spend" not in path.read_text(encoding="utf-8").lower(), path.name
