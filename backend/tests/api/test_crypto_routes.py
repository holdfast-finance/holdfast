"""``/api/crypto/wallets*`` through the real app: auth, CSRF, validation, isolation, no echo.

Keys and addresses are published BIP84 vectors or invented; secrets are shape-only filler.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from tests.api.conftest import Owner, make_client, seed_household

pytestmark = pytest.mark.services

ZPUB = "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1ADqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs"  # noqa: E501
FIRST = "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"
PAYLOAD = {
    "label": "Cold storage",
    "chain": "bitcoin",
    "key_or_address": f"[73c5da0a/84h/0h/0h]{ZPUB}",
}
Json = dict[str, Any]


async def test_crypto_routes_need_a_session(client: httpx.AsyncClient) -> None:
    for call in (
        client.get("/api/crypto/wallets"),
        client.post("/api/crypto/wallets", json=PAYLOAD),
    ):
        assert (await call).status_code in (401, 403)


async def test_wallet_lifecycle_never_returns_the_key(owner: Owner) -> None:
    c = owner.client
    r = await c.post("/api/crypto/wallets", json={**PAYLOAD, "gap_limit": 3})
    assert r.status_code == 201, r.text
    wallet: Json = r.json()
    assert wallet["first_receive_address"] == FIRST and wallet["address_count"] == 6
    assert wallet["status"] == "active" and wallet["kind"] == "watch_xpub"
    for text in (r.text, (await c.get("/api/crypto/wallets")).text):
        assert "pub6" not in text and "descriptor" not in text and "_enc" not in text
    assert (await c.get("/api/crypto/wallets")).json()["items"][0]["id"] == wallet["id"]
    assert (await c.get(f"/api/crypto/wallets/{wallet['id']}")).json()["label"] == "Cold storage"
    page = (await c.get(f"/api/crypto/wallets/{wallet['id']}/transactions")).json()
    assert page == {"items": [], "next_cursor": None}
    assert (await c.delete(f"/api/crypto/wallets/{wallet['id']}")).status_code == 204
    assert (await c.get(f"/api/crypto/wallets/{wallet['id']}")).status_code == 404


async def test_a_duplicate_is_a_conflict(owner: Owner) -> None:
    c = owner.client
    assert (await c.post("/api/crypto/wallets", json=PAYLOAD)).status_code == 201
    again = await c.post("/api/crypto/wallets", json={**PAYLOAD, "key_or_address": ZPUB})
    assert again.status_code == 409 and again.json()["error"]["code"] == "conflict"


@pytest.mark.parametrize(
    "secret",
    [
        "xprv" + "A" * 107,
        "K" + "w" * 51,
        "cd" * 32,
        " ".join(["lorem", "ipsum", "dolor", "sit"] * 3),
    ],
)
async def test_a_secret_is_refused_without_echo_in_response_or_logs(
    owner: Owner, secret: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    for field in ("key_or_address", "label"):
        r = await owner.client.post("/api/crypto/wallets", json={**PAYLOAD, field: secret})
        assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
        assert "seed phrase or a private key" in r.json()["error"]["message"]
        assert secret not in r.text
    assert secret not in caplog.text and "xprv" not in caplog.text
    assert (await owner.client.get("/api/crypto/wallets")).json() == {"items": []}


async def test_oversized_and_malformed_input_never_echoes(owner: Owner) -> None:
    long = "q" * 5000
    r = await owner.client.post("/api/crypto/wallets", json={**PAYLOAD, "key_or_address": long})
    assert r.status_code == 422 and long not in r.text
    bad = await owner.client.post("/api/crypto/wallets", json={**PAYLOAD, "chain": "solana"})
    assert bad.status_code == 422
    assert (await owner.client.get("/api/crypto/wallets/not-a-uuid")).status_code == 422


async def test_state_changing_calls_need_the_csrf_header(app: FastAPI, owner: Owner) -> None:
    cookie = owner.client.cookies.get("hf_session") or next(iter(owner.client.cookies.values()))
    bare = make_client(app, csrf=False)
    bare.cookies.set("hf_session", cookie)
    try:
        r = await bare.post("/api/crypto/wallets", json=PAYLOAD)
        assert r.status_code == 403
    finally:
        await bare.aclose()


async def test_another_household_cannot_see_or_delete_the_wallet(
    app: FastAPI, owner: Owner
) -> None:
    created = (await owner.client.post("/api/crypto/wallets", json=PAYLOAD)).json()
    _, _, token = await seed_household(app, "Other House", "Jo Example", "jo@example.com")
    other = make_client(app)
    other.cookies.set("hf_session", token)
    try:
        assert (await other.get("/api/crypto/wallets")).json() == {"items": []}
        assert (await other.get(f"/api/crypto/wallets/{created['id']}")).status_code == 404
        assert (await other.delete(f"/api/crypto/wallets/{created['id']}")).status_code == 404
    finally:
        await other.aclose()
    assert (await owner.client.get(f"/api/crypto/wallets/{created['id']}")).status_code == 200
