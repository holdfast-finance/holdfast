"""Invite replace and accept stay one live token per person under concurrency.

Reissue drops the previous token. Overlapping creates leave one. An accept that has already
consumed its token does not erase a newer person index. Accept is single-use.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis
from redis.exceptions import NoScriptError

from app.security.tokens import token_hash_hex
from app.services.context import RequestContext
from app.services.invites import INVITE_TTL_SECONDS, create_invite
from app.services.sessions import resolve_token
from tests.api.conftest import Owner, make_client, session_cookie

# ADR 0010 key shape. The digest is sha256 hex; the person index points at that digest.
_TOKEN_PREFIX = "hf:invite:"
_PERSON_PREFIX = "hf:invite:person:"


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def _parts(args: tuple[Any, ...]) -> list[str]:
    return [_text(arg) for arg in args]


def _command(args: tuple[Any, ...]) -> str:
    parts = _parts(args)
    return parts[0].upper() if parts else ""


def _keys(args: tuple[Any, ...]) -> list[str]:
    parts = _parts(args)
    cmd = _command(args)
    if cmd in {"EVAL", "EVALSHA"} and len(parts) >= 3:
        try:
            count = int(parts[2])
        except ValueError:
            return []
        return parts[3 : 3 + count]
    if cmd == "DEL":
        return parts[1:]
    if cmd in {"GET", "GETDEL", "SET"} and len(parts) >= 2:
        return [parts[1]]
    return []


def _is_person_key(key: str) -> bool:
    return key.startswith(_PERSON_PREFIX)


def _is_token_key(key: str) -> bool:
    if not key.startswith(_TOKEN_PREFIX):
        return False
    digest = key.removeprefix(_TOKEN_PREFIX)
    return len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)


def _is_replace_op(args: tuple[Any, ...]) -> bool:
    """The replace script, or the old GETDEL of the person index that started a create."""
    cmd = _command(args)
    keys = _keys(args)
    if cmd == "GETDEL" and any(_is_person_key(key) for key in keys):
        return True
    return cmd in {"EVAL", "EVALSHA"} and any(_is_person_key(key) for key in keys)


def _is_consume_op(args: tuple[Any, ...]) -> bool:
    """The accept script, or the old GETDEL of the token key. Not the replace script."""
    cmd = _command(args)
    keys = _keys(args)
    if any(_is_person_key(key) for key in keys):
        return False
    if cmd == "GETDEL":
        return any(_is_token_key(key) for key in keys)
    return cmd in {"EVAL", "EVALSHA"} and any(_is_token_key(key) for key in keys)


async def _add_person(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/api/household/people", json={"display_name": "Sam Example", "filing_role": "spouse"}
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def _issue(client: httpx.AsyncClient, person_id: str, email: str = "sam@example.com") -> str:
    response = await client.post(f"/api/household/people/{person_id}/invite", json={"email": email})
    assert response.status_code == 200, response.text
    url = str(response.json()["invite_url"])
    return url.split("#", 1)[1]


async def _issue_as(app: FastAPI, cookie: str, person_id: str, email: str) -> str:
    async with make_client(app) as client:
        client.cookies.set("hf_session", cookie)
        return await _issue(client, person_id, email)


async def _accept(app: FastAPI, token: str) -> httpx.Response:
    async with make_client(app) as client:
        return await client.post("/api/invites/accept", json={"token": token})


async def _token_keys(redis: Redis) -> set[str]:
    found: set[str] = set()
    async for raw in redis.scan_iter(f"{_TOKEN_PREFIX}*"):
        name = _text(raw)
        if not name.startswith(_PERSON_PREFIX):
            found.add(name)
    return found


def _person_index(person_id: str) -> str:
    return f"{_PERSON_PREFIX}{person_id}"


async def test_reissue_invalidates_the_old_token(app: FastAPI, owner: Owner) -> None:
    sam = await _add_person(owner.client)
    first = await _issue(owner.client, sam["id"])
    second = await _issue(owner.client, sam["id"])
    assert first != second
    redis: Redis = app.state.redis
    assert await _token_keys(redis) == {f"{_TOKEN_PREFIX}{token_hash_hex(second)}"}
    index = await redis.get(_person_index(sam["id"]))
    assert index is not None
    assert _text(index) == token_hash_hex(second)

    old = await _accept(app, first)
    assert old.status_code == 404
    assert old.json()["error"]["code"] == "invalid_invite"
    # A miss must not drop the live invite's index.
    kept = await redis.get(_person_index(sam["id"]))
    assert kept is not None
    assert _text(kept) == token_hash_hex(second)
    assert (await _accept(app, second)).status_code == 200


async def test_overlapping_creates_leave_exactly_one_valid_token(
    app: FastAPI, owner: Owner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both creates pass their first Redis write before either continues.

    A multi-call replace then stores two token keys. One script finishes entirely before the
    other runs, so only the later invite remains.
    """
    sam = await _add_person(owner.client)
    cookie = session_cookie(owner.client)
    assert cookie is not None
    redis: Redis = app.state.redis
    barrier = asyncio.Barrier(2)
    arrived: set[asyncio.Task[Any]] = set()
    original = redis.execute_command

    async def execute_command(*args: Any, **kwargs: Any) -> Any:
        try:
            result = await original(*args, **kwargs)
        except NoScriptError:
            raise
        except Exception:
            await barrier.abort()
            raise
        task = asyncio.current_task()
        if task is not None and task not in arrived and _is_replace_op(args):
            arrived.add(task)
            try:
                await asyncio.wait_for(barrier.wait(), 5)
            except TimeoutError:
                await barrier.abort()
                raise
        return result

    monkeypatch.setattr(redis, "execute_command", execute_command)
    tasks = [
        asyncio.create_task(_issue_as(app, cookie, sam["id"], "sam@example.com")),
        asyncio.create_task(_issue_as(app, cookie, sam["id"], "sam@example.com")),
    ]
    try:
        tokens = list(await asyncio.gather(*tasks))
    except Exception:
        await barrier.abort()
        for task in tasks:
            task.cancel()
        raise
    assert len(arrived) == 2
    assert len(tokens) == 2 and tokens[0] != tokens[1]
    keys = await _token_keys(redis)
    assert len(keys) == 1
    index = await redis.get(_person_index(sam["id"]))
    assert index is not None
    digest = _text(index)
    assert keys == {f"{_TOKEN_PREFIX}{digest}"}
    live = next(token for token in tokens if token_hash_hex(token) == digest)
    dead = next(token for token in tokens if token != live)
    assert (await _accept(app, dead)).status_code == 404
    assert (await _accept(app, live)).status_code == 200


async def test_create_after_accept_starts_keeps_the_newer_index(
    app: FastAPI, owner: Owner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the token is consumed, a new invite is stored before accept returns.

    The old accept then deleted the person index unconditionally. The newer token stayed
    valid and the next replace could not find it.
    """
    sam = await _add_person(owner.client)
    first = await _issue(owner.client, sam["id"])
    cookie = session_cookie(owner.client)
    assert cookie is not None
    principal = await resolve_token(app.state.session_factory, cookie)
    assert principal is not None
    ctx = RequestContext(
        factory=app.state.session_factory,
        redis=app.state.redis,
        settings=app.state.settings,
        ip=None,
        user_agent=None,
    )
    redis: Redis = app.state.redis
    original = redis.execute_command
    newer: list[str] = []
    armed = True

    async def execute_command(*args: Any, **kwargs: Any) -> Any:
        nonlocal armed
        result = await original(*args, **kwargs)
        if armed and _is_consume_op(args):
            armed = False
            url = await create_invite(ctx, principal, uuid.UUID(sam["id"]), "sam.later@example.com")
            newer.append(url.split("#", 1)[1])
        return result

    monkeypatch.setattr(redis, "execute_command", execute_command)
    accepted = await _accept(app, first)
    assert accepted.status_code == 200, accepted.text
    assert len(newer) == 1
    digest = token_hash_hex(newer[0])
    index = await redis.get(_person_index(sam["id"]))
    assert index is not None
    assert _text(index) == digest
    assert await redis.get(f"{_TOKEN_PREFIX}{digest}") is not None
    # The person now has an account, so the newer token is still real but unusable.
    reused = await _accept(app, newer[0])
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "conflict"


async def test_accept_clears_the_person_index_only_for_the_consumed_digest(
    app: FastAPI, owner: Owner
) -> None:
    """A token key can outlive an index that already points at a newer invite.

    Accepting that older token must not delete the newer index or its token key.
    """
    sam = await _add_person(owner.client)
    first = await _issue(owner.client, sam["id"])
    redis: Redis = app.state.redis
    first_digest = token_hash_hex(first)
    payload = await redis.get(f"{_TOKEN_PREFIX}{first_digest}")
    assert payload is not None
    second = await _issue(owner.client, sam["id"])
    assert await redis.get(f"{_TOKEN_PREFIX}{first_digest}") is None
    await redis.set(f"{_TOKEN_PREFIX}{first_digest}", payload, ex=INVITE_TTL_SECONDS)

    accepted = await _accept(app, first)
    assert accepted.status_code == 200, accepted.text
    second_digest = token_hash_hex(second)
    index = await redis.get(_person_index(sam["id"]))
    assert index is not None
    assert _text(index) == second_digest
    assert await redis.get(f"{_TOKEN_PREFIX}{second_digest}") is not None
    stale = await _accept(app, first)
    assert stale.status_code == 404
    still = await _accept(app, second)
    assert still.status_code == 409
    assert still.json()["error"]["code"] == "conflict"


async def test_accept_is_single_use(app: FastAPI, owner: Owner) -> None:
    sam = await _add_person(owner.client)
    token = await _issue(owner.client, sam["id"])
    first, second = await asyncio.gather(_accept(app, token), _accept(app, token))
    statuses = sorted([first.status_code, second.status_code])
    assert statuses == [200, 404]
    missed = second if second.status_code == 404 else first
    assert missed.json()["error"]["code"] == "invalid_invite"
    redis: Redis = app.state.redis
    assert await redis.get(_person_index(sam["id"])) is None
    assert await _token_keys(redis) == set()
    assert (await _accept(app, token)).status_code == 404
