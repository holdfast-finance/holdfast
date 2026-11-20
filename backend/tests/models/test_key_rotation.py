"""rotate-keys against real Postgres as holdfast_app (ADR 0015). Synthetic data only."""

from __future__ import annotations

import asyncio
import logging
import secrets
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.logging import configure_logging
from app.security import crypto, rotation
from app.security.secret_token import SecretToken
from app.services import audit
from tests.models.conftest import delete_household, set_ctx

pytestmark = pytest.mark.services


def legacy(plaintext: str, raw_key: str) -> bytes:
    return Fernet(raw_key.encode()).encrypt(plaintext.encode())


def sealed(plaintext: str, raw_key: str) -> bytes:
    return crypto.seal(SecretToken(plaintext), key=SecretStr(raw_key))


@dataclass
class World:
    household_id: uuid.UUID
    user_id: uuid.UUID
    connection_id: uuid.UUID
    setting_key: str
    old: str
    new: str
    canaries: dict[str, str]

    @property
    def ring(self) -> crypto.KeyRing:
        return crypto.load_ring(SecretStr(f"{self.new},{self.old}"))


@pytest.fixture
def world(app_engine: Engine, owner_engine: Engine) -> Iterator[World]:
    old, new = crypto.generate_key(), crypto.generate_key()
    canaries = {n: f"{n}-{secrets.token_hex(10)}" for n in ("setting", "token", "totp")}
    with app_engine.begin() as c:
        hid = c.execute(text("SELECT create_household('Example Rotation')")).scalar_one()
        set_ctx(c, hid, wide=True)
        c.execute(
            text("INSERT INTO household_setting (household_id, key, value_enc) VALUES (:h,'k',:v)"),
            {"h": hid, "v": legacy(canaries["setting"], old)},
        )
        conn_id = c.execute(
            text(
                "INSERT INTO bank_connection"
                " (household_id, provider, external_id, access_token_enc)"
                " VALUES (:h,'plaid',:e,:v) RETURNING id"
            ),
            {"h": hid, "e": f"item-{uuid.uuid4()}", "v": legacy(canaries["token"], old)},
        ).scalar_one()
        user_id = c.execute(
            text(
                "INSERT INTO app_user (household_id, email, totp_secret_enc)"
                " VALUES (:h,:m,:v) RETURNING id"
            ),
            {"h": hid, "m": f"{uuid.uuid4()}@example.test", "v": legacy(canaries["totp"], old)},
        ).scalar_one()
    yield World(hid, user_id, conn_id, "k", old, new, canaries)
    with owner_engine.begin() as c:
        set_ctx(c, hid, wide=True)
        c.execute(text("DELETE FROM app_user WHERE household_id = :h"), {"h": hid})
    delete_household(owner_engine, hid)


@pytest.fixture
async def factory(app_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine: AsyncEngine = create_async_engine(app_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def read(app_engine: Engine, w: World, table: str) -> bytes | None:
    col, where, params = {
        "household_setting": (
            "value_enc",
            "household_id = :h AND key = 'k'",
            {"h": w.household_id},
        ),
        "bank_connection": ("access_token_enc", "id = :i", {"i": w.connection_id}),
        "app_user": ("totp_secret_enc", "id = :i", {"i": w.user_id}),
    }[table]
    with app_engine.begin() as c:
        set_ctx(c, w.household_id, wide=True)
        row = c.execute(text(f"SELECT {col} FROM {table} WHERE {where}"), params).scalar_one()  # noqa: S608
    return None if row is None else bytes(row)


def write(app_engine: Engine, w: World, table: str, value: bytes | None) -> None:
    col, where, params = {
        "household_setting": (
            "value_enc",
            "household_id = :h AND key = 'k'",
            {"h": w.household_id},
        ),
        "bank_connection": ("access_token_enc", "id = :i", {"i": w.connection_id}),
        "app_user": ("totp_secret_enc", "id = :i", {"i": w.user_id}),
    }[table]
    with app_engine.begin() as c:
        set_ctx(c, w.household_id, wide=True)
        c.execute(text(f"UPDATE {table} SET {col} = :v WHERE {where}"), {**params, "v": value})  # noqa: S608


TABLES = ("household_setting", "bank_connection", "app_user")
CANARY = {"household_setting": "setting", "bank_connection": "token", "app_user": "totp"}


async def run(
    factory: async_sessionmaker[AsyncSession], w: World, hook: rotation.Hook | None = None
) -> rotation.Counts:
    return await rotation.rotate(factory, w.ring, hook=hook, only=[w.household_id])


async def test_legacy_rows_rotate_to_the_primary_kid_and_second_run_is_a_no_op(
    app_engine: Engine, factory: async_sessionmaker[AsyncSession], world: World
) -> None:
    counts = await run(factory, world)
    assert (counts.rotated, counts.failed, counts.conflicted, counts.residual) == (3, 0, 0, 0)
    assert counts.clean
    new_kid = crypto.key_id(world.new)
    for t in TABLES:
        blob = read(app_engine, world, t)
        assert blob is not None and blob.startswith(f"hf1.{new_kid}.".encode())
        assert crypto.open_with(blob, world.ring).reveal() == world.canaries[CANARY[t]]
    before = {t: read(app_engine, world, t) for t in TABLES}
    again = await run(factory, world)
    assert (again.rotated, again.already_current, again.failed) == (0, 3, 0)
    assert {t: read(app_engine, world, t) for t in TABLES} == before


async def test_hf1_row_under_a_retired_kid_is_resealed(
    app_engine: Engine, factory: async_sessionmaker[AsyncSession], world: World
) -> None:
    write(app_engine, world, "bank_connection", sealed("old-kid-canary", world.old))
    counts = await run(factory, world)
    assert counts.rotated == 3 and counts.clean
    blob = read(app_engine, world, "bank_connection")
    assert blob is not None and crypto.is_current(blob, world.ring)
    assert crypto.open_with(blob, world.ring).reveal() == "old-kid-canary"


async def test_null_totp_is_skipped_and_empty_fails_unchanged(
    app_engine: Engine, factory: async_sessionmaker[AsyncSession], world: World
) -> None:
    write(app_engine, world, "app_user", None)
    counts = await run(factory, world)
    assert (counts.skipped_null, counts.rotated, counts.failed) == (1, 2, 0)
    assert read(app_engine, world, "app_user") is None
    write(app_engine, world, "app_user", b"")
    counts = await run(factory, world)
    assert counts.failed == 1 and not counts.clean
    assert read(app_engine, world, "app_user") == b""


async def test_unknown_kid_and_unknown_version_fail_the_row_and_leave_bytes(
    app_engine: Engine, factory: async_sessionmaker[AsyncSession], world: World
) -> None:
    stranger = crypto.generate_key()
    foreign = sealed("foreign-canary", stranger)  # kid not in the ring
    # hf1 under the old kid's name but sealed by another key: no trial decrypt, no legacy path.
    relabelled = foreign.replace(
        crypto.key_id(stranger).encode(), crypto.key_id(world.old).encode()
    )
    write(app_engine, world, "household_setting", foreign)
    write(app_engine, world, "bank_connection", relabelled)
    write(app_engine, world, "app_user", b"hf2.0123456789abcdef.whatever")
    counts = await run(factory, world)
    assert (counts.failed, counts.rotated) == (3, 0)
    assert counts.residual == 3 and not counts.clean
    assert read(app_engine, world, "household_setting") == foreign
    assert read(app_engine, world, "bank_connection") == relabelled
    assert read(app_engine, world, "app_user") == b"hf2.0123456789abcdef.whatever"


async def test_legacy_bytes_no_key_can_open_fail_the_row(
    app_engine: Engine, factory: async_sessionmaker[AsyncSession], world: World
) -> None:
    junk = legacy("x", crypto.generate_key())
    write(app_engine, world, "bank_connection", junk)
    counts = await run(factory, world)
    assert counts.failed == 1 and counts.rotated == 2 and not counts.clean
    assert read(app_engine, world, "bank_connection") == junk


def _drift(app_engine_blobs: list[bytes], table_sql: str, params: dict[str, Any]) -> rotation.Hook:
    """Simulate a lost lock: commit a different value, so the swap's predicate fails."""
    queue = list(app_engine_blobs)

    async def hook(attempt: int, table: str, s: AsyncSession) -> None:
        if table != "bank_connection" or not queue:
            return
        await s.execute(text(table_sql), {**params, "v": queue.pop(0)})
        await s.commit()

    return hook


async def test_failed_swap_rereads_and_seals_only_the_bytes_it_locked(
    app_engine: Engine, factory: async_sessionmaker[AsyncSession], world: World
) -> None:
    winner = legacy("winner-canary", world.old)
    hook = _drift(
        [winner],
        "UPDATE bank_connection SET access_token_enc = :v WHERE id = :i",
        {"i": world.connection_id},
    )
    counts = await run(factory, world, hook)
    assert counts.conflicted == 0 and counts.clean
    blob = read(app_engine, world, "bank_connection")
    assert blob is not None and crypto.is_current(blob, world.ring)
    assert crypto.open_with(blob, world.ring).reveal() == "winner-canary"
    assert world.canaries["token"] != "winner-canary"


async def test_failed_swap_against_an_already_current_winner_writes_nothing(
    app_engine: Engine, factory: async_sessionmaker[AsyncSession], world: World
) -> None:
    winner = sealed("winner-canary", world.new)
    hook = _drift(
        [winner],
        "UPDATE bank_connection SET access_token_enc = :v WHERE id = :i",
        {"i": world.connection_id},
    )
    counts = await run(factory, world, hook)
    assert counts.clean and counts.conflicted == 0
    assert read(app_engine, world, "bank_connection") == winner


async def test_second_failed_swap_is_a_conflict_and_leaves_the_row(
    app_engine: Engine, factory: async_sessionmaker[AsyncSession], world: World
) -> None:
    first, second = legacy("w1", world.old), legacy("w2", world.old)
    hook = _drift(
        [first, second],
        "UPDATE bank_connection SET access_token_enc = :v WHERE id = :i",
        {"i": world.connection_id},
    )
    counts = await run(factory, world, hook)
    assert counts.conflicted == 1 and not counts.clean
    assert read(app_engine, world, "bank_connection") == second  # untouched, never re-sealed
    assert counts.residual == 1


async def test_two_concurrent_rotations_leave_one_envelope_with_the_right_plaintext(
    app_engine: Engine, factory: async_sessionmaker[AsyncSession], world: World
) -> None:
    a, b = await asyncio.gather(run(factory, world), run(factory, world))
    assert a.rotated + b.rotated == 3
    assert a.failed + b.failed + a.conflicted + b.conflicted == 0
    for t in TABLES:
        blob = read(app_engine, world, t)
        assert blob is not None and crypto.is_current(blob, world.ring)
        assert crypto.open_with(blob, world.ring).reveal() == world.canaries[CANARY[t]]


async def test_plaintext_never_reaches_logs_or_stdout(
    app_engine: Engine,
    factory: async_sessionmaker[AsyncSession],
    world: World,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging()
    caplog.set_level(logging.DEBUG)
    write(app_engine, world, "app_user", b"hf2.broken")  # exercise the failure path too
    await run(factory, world)
    seen = caplog.text + capsys.readouterr().out + capsys.readouterr().err
    for canary in world.canaries.values():
        assert canary not in seen
    assert world.old not in seen and world.new not in seen


async def test_cli_run_audits_counts_only_and_signals_unclean(
    app_engine: Engine,
    app_url: str,
    world: World,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from tests.conftest import make_settings

    original = rotation.rotate

    async def only_mine(*a: Any, **kw: Any) -> rotation.Counts:
        return await original(*a, only=[world.household_id], **kw)

    recorded: list[dict[str, Any]] = []

    async def fake_record(factory: Any, action: str, **kw: Any) -> None:
        recorded.append({"action": action, **kw})

    settings = make_settings(
        database_url=SecretStr(app_url), fernet_key=SecretStr(f"{world.new},{world.old}")
    )
    monkeypatch.setattr(rotation, "get_settings", lambda: settings)
    monkeypatch.setattr(rotation, "rotate", only_mine)
    monkeypatch.setattr(audit, "record", fake_record)
    assert await rotation._run() == 0
    assert recorded[0]["action"] == "keys.rotate" and recorded[0]["household_id"] is None
    assert recorded[0]["outcome"] == "success"
    assert set(recorded[0]["detail"]) == {
        "rotated",
        "already_current",
        "skipped_null",
        "failed",
        "conflicted",
        "gone",
        "residual",
    }
    assert all(isinstance(v, int) for v in recorded[0]["detail"].values())
    assert "remove retired keys" in capsys.readouterr().out
    write(app_engine, world, "app_user", b"junk")
    assert await rotation._run() == 1
    assert recorded[1]["outcome"] == "failure"


async def test_cli_refuses_a_duplicate_key_ring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.conftest import make_settings

    key = crypto.generate_key()
    settings = make_settings(fernet_key=SecretStr(f"{key},{key}"))
    monkeypatch.setattr(rotation, "get_settings", lambda: settings)
    assert await rotation._run() == 2


def test_runtime_role_is_still_not_superuser_and_has_no_bypassrls(app_engine: Engine) -> None:
    with app_engine.connect() as c:
        row = c.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
    assert tuple(row) == (False, False)
