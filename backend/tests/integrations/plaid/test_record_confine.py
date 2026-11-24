"""R1 review fixes: output confined to the fixtures, no overwrite, Item always removed."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "record_plaid_sandbox.py"
ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "plaid"
ENV = {"PLAID_ENV": "sandbox", "PLAID_CLIENT_ID": "x", "PLAID_SECRET": "y"}

_spec = importlib.util.spec_from_file_location("record_plaid_sandbox", SCRIPT)
assert _spec is not None and _spec.loader is not None
rec = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rec)


def test_out_inside_the_fixture_tree_is_accepted() -> None:
    assert rec.resolve_out(ROOT / "recorded-1", ROOT) == ROOT / "recorded-1"


def test_default_root_is_this_repos_fixture_tree() -> None:
    assert Path(rec.FIXTURE_ROOT).resolve() == ROOT.resolve()


def test_out_with_dotdot_is_refused() -> None:
    with pytest.raises(rec.RefusedError):
        rec.resolve_out(ROOT / ".." / "plaid-elsewhere", ROOT)
    with pytest.raises(rec.RefusedError):
        rec.resolve_out(ROOT / "a" / ".." / ".." / "x", ROOT)


def test_the_root_itself_is_refused() -> None:
    with pytest.raises(rec.RefusedError):
        rec.resolve_out(ROOT, ROOT)


def test_out_outside_the_tree_is_refused(tmp_path: Path) -> None:
    with pytest.raises(rec.RefusedError):
        rec.resolve_out(tmp_path / "o", ROOT)
    with pytest.raises(rec.RefusedError):
        rec.resolve_out(Path("/etc"), ROOT)
    assert rec.main(["--out", str(tmp_path / "o")], ENV) == 2
    assert not (tmp_path / "o").exists()


def test_symlinked_out_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "fixtures" / "plaid"
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside)
    with pytest.raises(rec.RefusedError):
        rec.resolve_out(root / "link", root)
    with pytest.raises(rec.RefusedError):
        rec.resolve_out(root / "link" / "sub", root)


def test_pages_are_never_overwritten(tmp_path: Path) -> None:
    (tmp_path / "sync_page1.json").write_text("keep")
    with pytest.raises(FileExistsError):
        rec.write_pages(tmp_path, [{"accounts": []}], rec.Pseudonyms())
    assert (tmp_path / "sync_page1.json").read_text() == "keep"


async def test_item_is_removed_even_when_paging_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakeClient:
        def __init__(self, *_a: object, **_k: object) -> None: ...

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_a: object) -> None: ...

        async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
            calls.append(path)
            if path == "/transactions/sync":
                raise RuntimeError("boom")
            return {"public_token": "p"}

        async def item_public_token_exchange(self, _t: object) -> Any:
            from app.security.secret_token import SecretToken

            class Exchanged:
                access_token = SecretToken("tok")

            return Exchanged()

    import app.integrations.plaid.client as client_mod

    monkeypatch.setattr(client_mod, "PlaidClient", FakeClient)
    out = ROOT / "never-written"
    with pytest.raises(RuntimeError):
        await rec.record(out, ENV)
    assert calls[-1] == "/item/remove"
    assert not out.exists()
