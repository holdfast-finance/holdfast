"""Only ``LedgerService`` writes ledger money fields (ADR 0011, money review).

AST scan of every module under ``app/`` except the writer. It flags:

- constructing ``Transaction`` / ``TransactionSplit``, or passing them to insert/update/delete;
- assigning (also ``+=``, ``setattr``) ``is_transfer``, ``transfer_group_id``, ``category_source``
  anywhere, and ``amount`` / ``category_id`` in modules that reference those models;
- the same names as ``.values(...)`` / ``set_=`` keywords or dict keys;
- raw SQL writing to the ``transaction`` or ``transaction_split`` tables.

Reads (``select(Transaction)``, ``Transaction.amount > 0``) are fine. Tests of the scanner itself
show a router-style write is caught.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
WRITER = "services/ledger_service.py"
ALLOWED: frozenset[str] = frozenset({WRITER})

MODELS = frozenset({"Transaction", "TransactionSplit"})
ALWAYS = frozenset({"is_transfer", "transfer_group_id", "category_source"})
IF_MODELS_TOUCHED = frozenset({"amount", "category_id"})
WRITE_FNS = frozenset({"insert", "pg_insert", "update", "delete", "merge"})
VALUE_CALLS = frozenset({"values", "update", "on_conflict_do_update"})
RAW_SQL = re.compile(
    r"\b(insert\s+into|update|delete\s+from)\s+\"?(transaction|transaction_split)\b", re.I
)


def _callee(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def violations(source: str) -> list[str]:
    tree = ast.parse(source)
    # Local names of the protected models and of sqlalchemy write functions (import aliases).
    model_names = set(MODELS)
    imported = False
    write_fns = set(WRITE_FNS)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name in MODELS:
                    imported = True
                    model_names.add(a.asname or a.name)
                if (node.module or "").startswith("sqlalchemy") and a.name in WRITE_FNS:
                    write_fns.add(a.asname or a.name)
    touches = imported or any(
        (isinstance(n, ast.Name) and n.id in model_names)
        or (isinstance(n, ast.Attribute) and n.attr in model_names)
        for n in ast.walk(tree)
    )
    protected = ALWAYS | (IF_MODELS_TOUCHED if touches else frozenset())

    def mentions_model(n: ast.AST) -> bool:
        return any(
            (isinstance(x, ast.Name) and x.id in model_names)
            or (isinstance(x, ast.Attribute) and x.attr in model_names)
            for x in ast.walk(n)
        )

    found: list[str] = []

    def hit(node: ast.AST, why: str) -> None:
        found.append(f"line {getattr(node, 'lineno', '?')}: {why}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            stack = list(targets)
            while stack:
                t = stack.pop()
                if isinstance(t, ast.Tuple | ast.List):
                    stack.extend(t.elts)
                elif isinstance(t, ast.Attribute) and t.attr in protected:
                    hit(node, f"assigns .{t.attr}")
        elif isinstance(node, ast.Call):
            name = _callee(node.func)
            if name in model_names:
                hit(node, f"constructs {name}")
            if name in write_fns and any(mentions_model(a) for a in node.args):
                hit(node, f"{name}() on a ledger model")
            if (
                name == "setattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in protected
            ):
                hit(node, f"setattr {node.args[1].value}")
            if name in VALUE_CALLS:
                for kw in node.keywords:
                    if kw.arg in protected:
                        hit(node, f"{name}({kw.arg}=...)")
                    if kw.arg == "set_" and isinstance(kw.value, ast.Dict):
                        for k in kw.value.keys:
                            if isinstance(k, ast.Constant) and k.value in protected:
                                hit(node, f"set_ key {k.value}")
                for arg in node.args:
                    if isinstance(arg, ast.Dict):
                        for k in arg.keys:
                            if isinstance(k, ast.Constant) and k.value in protected:
                                hit(node, f"{name}({{{k.value!r}: ...}})")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and RAW_SQL.search(node.value)
        ):
            hit(node, "raw SQL write to a ledger table")
    return found


def test_only_the_ledger_service_writes_ledger_fields() -> None:
    assert (APP / WRITER).is_file()
    bad = {}
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP).as_posix()
        if rel in ALLOWED:
            continue
        found = violations(path.read_text())
        if found:
            bad[rel] = found
    assert bad == {}, f"ledger fields written outside LedgerService: {bad}"


def test_the_writer_itself_is_flagged_so_the_scan_is_live() -> None:
    assert violations((APP / WRITER).read_text()), "scanner no longer sees the real writer"


def test_allowlist_names_real_files() -> None:
    assert all((APP / rel).is_file() for rel in ALLOWED)


# ---------------------------------------------------------------- the scanner bites

ROUTER_STYLE = """
from fastapi import APIRouter
router = APIRouter()

@router.post("/transactions/{tid}/transfer")
async def mark(tid, db):
    txn = await db.get(Txn, tid)
    txn.is_transfer = True
"""


def test_router_style_is_transfer_write_is_detected() -> None:
    assert violations(ROUTER_STYLE) == ["line 8: assigns .is_transfer"]


def test_scanner_detects_each_bypass_form() -> None:
    cases = {
        "from app.models import Transaction\nt = Transaction(amount=1)": "constructs",
        "from app.models import TransactionSplit as S\nx = S()": "constructs",
        "from app.models import Transaction\nt.amount += 1": "assigns .amount",
        "from app.models import Transaction\nt.category_id = c": "assigns .category_id",
        "a.transfer_group_id = g": "assigns .transfer_group_id",
        "t.category_source = 'user'": "assigns .category_source",
        "a, b = 1, 2\nx.is_transfer, y.z = 1, 2": "assigns .is_transfer",
        "setattr(t, 'is_transfer', True)": "setattr",
        "from app.models import Transaction\nupdate(Transaction).values(amount=1)": "update()",
        "from sqlalchemy import delete\nfrom app.models import TransactionSplit\n"
        "delete(TransactionSplit)": "delete()",
        "from sqlalchemy.dialects.postgresql import insert as ins\n"
        "from app.models import Transaction\nins(Transaction)": "ins()",
        "q.values(is_transfer=True)": "values(is_transfer=...)",
        "q.on_conflict_do_update(set_={'category_source': 'x'})": "set_ key",
        "q.values({'transfer_group_id': g})": "values({'transfer_group_id': ...})",
        "db.execute(text('UPDATE transaction SET amount = 0'))": "raw SQL",
        "db.execute(text('insert into transaction_split (id) values (1)'))": "raw SQL",
    }
    for src, expect in cases.items():
        found = violations(src)
        assert found and any(expect in f for f in found), (src, found)


def test_scanner_allows_reads_and_unrelated_amounts() -> None:
    clean = [
        "from app.models import Transaction\nselect(Transaction).where(Transaction.amount > 0)",
        "from app.models import Transaction\nTransaction.is_transfer.is_(False)",
        "run.opening_balance = 1\nbudget_line.amount = 2",  # no ledger model in sight
        "class Out:\n    is_transfer: bool\n    amount: str",
        "db.execute(text('select * from transaction'))",
    ]
    for src in clean:
        assert violations(src) == [], src
