"""Only the setup probe (and, from P4, the slot-guarded Link service) may mint a Link token.

``PlaidClient.link_token_create`` has no slot guard of its own. The setup check may call it because
it throws the token away (ADR 0023 §6). Any other caller must sit behind the guard, so adding one
means adding it here on purpose, in review.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
ALLOWED = {"services/plaid_setup.py"}
DEFINED_IN = "integrations/plaid/client.py"


def test_link_token_create_is_called_only_from_reviewed_modules() -> None:
    callers: set[str] = set()
    for path in APP.rglob("*.py"):
        rel = path.relative_to(APP).as_posix()
        if rel == DEFINED_IN:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Attribute) and node.attr == "link_token_create") or (
                isinstance(node, ast.Name) and node.id == "link_token_create"
            ):
                callers.add(rel)
    assert callers == ALLOWED
    # Building the endpoint path by hand would walk around the method and this test.
    for path in APP.rglob("*.py"):
        rel = path.relative_to(APP).as_posix()
        if rel != DEFINED_IN and rel not in ALLOWED:
            assert "/link/token/create" not in path.read_text(encoding="utf-8"), rel
