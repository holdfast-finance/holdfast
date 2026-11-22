"""API test fixtures: the real app against real Postgres (as holdfast_app) and Redis.

- The schema is migrated to head as ``holdfast_owner``; the app itself connects as
  ``holdfast_app``, so RLS applies to every request exactly as in production.
- Redis uses logical DB 15 of ``TEST_REDIS_URL`` and is flushed per test.
- Before each test every household that has a user is deleted (as the owner role, inside its
  own RLS context), so first-run setup always starts from an empty instance.
- WebAuthn runs through :class:`SoftAuthenticator`: real P-256 keys, real ``none`` attestation
  and real assertions signed with ``cryptography``. All data is synthetic.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import cbor2
import httpx
import pyotp
import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import Engine, create_engine, text, update

from app.config import Settings
from app.db_context import apply_context, create_household, unscoped_session
from app.main import announce_setup_token, create_app
from app.models import AppUser, AuthSession, Person
from app.security.crypto import generate_key
from app.services import sessions
from tests.conftest import _reachable, _unavailable, env_database_url, env_migrations_database_url
from tests.conftest import env_redis_url as _env_redis_url

BACKEND = Path(__file__).resolve().parents[2]
HOST = "localhost"
ORIGIN = f"https://{HOST}"
CSRF = {"X-Holdfast-CSRF": "1"}
REDIS_TEST_DB = 15


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


# --------------------------------------------------------------------------- services


def _need(url: str | None, name: str, port: int) -> str:
    if not url:
        _unavailable(f"{name} is not set; start Postgres/Redis to run API tests")
    assert url is not None
    if not _reachable(url, port):
        _unavailable(f"{name} is unreachable")
    return url


@pytest.fixture(scope="session")
def api_db_url() -> str:
    return _need(env_database_url(), "TEST_DATABASE_URL", 5432)


@pytest.fixture(scope="session")
def api_owner_url() -> str:
    return _need(env_migrations_database_url(), "TEST_MIGRATIONS_DATABASE_URL", 5432)


@pytest.fixture(scope="session")
def api_redis_url() -> str:
    url = _need(_env_redis_url(), "TEST_REDIS_URL", 6379)
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{REDIS_TEST_DB}"))


@pytest.fixture(scope="session")
def migrated_owner_engine(api_owner_url: str) -> Iterator[Engine]:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.attributes["database_url"] = api_owner_url
    cfg.attributes["skip_logging_config"] = True
    command.upgrade(cfg, "head")
    engine = create_engine(api_owner_url)
    yield engine
    engine.dispose()


def wipe_households(owner_engine: Engine) -> None:
    """Delete every household that has a user (cascades to people, users, sessions...)."""
    with owner_engine.begin() as c:
        ids = c.execute(text("SELECT DISTINCT household_id FROM app_user")).scalars().all()
    for hid in ids:
        with owner_engine.begin() as c:
            c.execute(text("SELECT set_config('app.household_id', :h, true)"), {"h": str(hid)})
            c.execute(text("DELETE FROM household WHERE id = :h"), {"h": hid})


@pytest.fixture
def settings(api_db_url: str, api_redis_url: str) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="test",
        e2e_hooks=True,
        app_host=HOST,
        app_base_url=ORIGIN,
        database_url=api_db_url,
        redis_url=api_redis_url,
        fernet_key=generate_key(),
        import_id_key=SecretStr("synthetic-import-id-key-for-tests-0123456789"),
    )


@pytest.fixture
async def app(
    settings: Settings, migrated_owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[FastAPI]:
    # crypto.encrypt falls back to get_settings(); point it at this test's key.
    monkeypatch.setenv("FERNET_KEY", settings.fernet_key.get_secret_value())  # type: ignore[union-attr]
    from app.config import get_settings

    get_settings.cache_clear()
    wipe_households(migrated_owner_engine)
    application = create_app(settings)
    redis: Redis = application.state.redis
    await redis.flushdb()
    yield application
    await redis.flushdb()
    await redis.aclose()
    await application.state.engine.dispose()
    wipe_households(migrated_owner_engine)
    get_settings.cache_clear()


def make_client(app: FastAPI, *, csrf: bool = True) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("203.0.113.10", 50000)),
        base_url=ORIGIN,
        headers=CSRF if csrf else {},
    )


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client(app) as c:
        yield c


@pytest.fixture
async def setup_token(app: FastAPI) -> str:
    token = await announce_setup_token(app)
    assert token is not None
    return token


# --------------------------------------------------------------------------- authenticator


@dataclass
class SoftCredential:
    credential_id: bytes
    key: ec.EllipticCurvePrivateKey
    user_handle: bytes
    rp_id: str
    sign_count: int = 0


@dataclass
class SoftAuthenticator:
    """A CTAP2-like platform authenticator with resident keys and user verification."""

    origin: str = ORIGIN
    credentials: list[SoftCredential] = field(default_factory=list)
    uv: bool = True
    counter_step: int = 1

    def _cose(self, key: ec.EllipticCurvePrivateKey) -> bytes:
        nums = key.public_key().public_numbers()
        return cbor2.dumps(
            {1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")}
        )

    def _client_data(self, kind: str, challenge: str, origin: str | None = None) -> bytes:
        return json.dumps(
            {
                "type": kind,
                "challenge": challenge,
                "origin": origin or self.origin,
                "crossOrigin": False,
            }
        ).encode()

    def _flags(self, *, attested: bool) -> int:
        flags = 0x01  # UP
        if self.uv:
            flags |= 0x04
        if attested:
            flags |= 0x40
        return flags

    def register(self, options: dict[str, Any], *, origin: str | None = None) -> dict[str, Any]:
        rp_id = options["rp"]["id"]
        key = ec.generate_private_key(ec.SECP256R1())
        cred_id = os.urandom(32)
        cred = SoftCredential(cred_id, key, b64url_decode(options["user"]["id"]), rp_id)
        self.credentials.append(cred)
        attested = b"\x00" * 16 + len(cred_id).to_bytes(2, "big") + cred_id + self._cose(key)
        auth_data = (
            hashlib.sha256(rp_id.encode()).digest()
            + bytes([self._flags(attested=True)])
            + (0).to_bytes(4, "big")
            + attested
        )
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": b64url(cred_id),
            "rawId": b64url(cred_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(
                    self._client_data("webauthn.create", options["challenge"], origin)
                ),
                "attestationObject": b64url(attestation),
                "transports": ["internal"],
            },
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }

    def assert_(
        self,
        options: dict[str, Any],
        *,
        credential: SoftCredential | None = None,
        origin: str | None = None,
        sign_count: int | None = None,
    ) -> dict[str, Any]:
        cred = credential or self.credentials[-1]
        cred.sign_count = cred.sign_count + self.counter_step if sign_count is None else sign_count
        client_data = self._client_data("webauthn.get", options["challenge"], origin)
        auth_data = (
            hashlib.sha256(cred.rp_id.encode()).digest()
            + bytes([self._flags(attested=False)])
            + cred.sign_count.to_bytes(4, "big")
        )
        signature = cred.key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": b64url(cred.credential_id),
            "rawId": b64url(cred.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(auth_data),
                "signature": b64url(signature),
                "userHandle": b64url(cred.user_handle),
            },
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }


@pytest.fixture
def authenticator() -> SoftAuthenticator:
    return SoftAuthenticator()


# --------------------------------------------------------------------------- flows


SETUP_BODY = {
    "household_name": "Example Household",
    "display_name": "Alex Example",
    "email": "Alex@Example.com",
}


async def do_setup(client: httpx.AsyncClient, token: str, **overrides: str) -> httpx.Response:
    return await client.post("/api/setup", json={**SETUP_BODY, "setup_token": token, **overrides})


async def register_passkey(
    client: httpx.AsyncClient, authenticator: SoftAuthenticator, nickname: str = "Laptop"
) -> httpx.Response:
    options = await client.post("/api/auth/passkey/register/options", json={})
    assert options.status_code == 200, options.text
    credential = authenticator.register(options.json())
    return await client.post(
        "/api/auth/passkey/register/verify", json={"credential": credential, "nickname": nickname}
    )


async def passkey_login(
    client: httpx.AsyncClient, authenticator: SoftAuthenticator, **kw: Any
) -> httpx.Response:
    options = await client.post("/api/auth/passkey/login/options", json={})
    assert options.status_code == 200, options.text
    return await client.post(
        "/api/auth/passkey/login/verify",
        json={"credential": authenticator.assert_(options.json(), **kw)},
    )


async def reauth_passkey(client: httpx.AsyncClient, authenticator: SoftAuthenticator) -> None:
    options = await client.post("/api/auth/reauth/passkey/options", json={})
    assert options.status_code == 200, options.text
    r = await client.post(
        "/api/auth/reauth/passkey/verify",
        json={"credential": authenticator.assert_(options.json())},
    )
    assert r.status_code == 204, r.text


@dataclass
class Owner:
    client: httpx.AsyncClient
    authenticator: SoftAuthenticator
    user: dict[str, Any]
    household: dict[str, Any]


@pytest.fixture
async def owner(
    client: httpx.AsyncClient, setup_token: str, authenticator: SoftAuthenticator
) -> Owner:
    """Setup done, owner signed in with a passkey (so the session is reauth-fresh)."""
    r = await do_setup(client, setup_token)
    assert r.status_code == 201, r.text
    assert (await register_passkey(client, authenticator)).status_code == 200
    login = await passkey_login(client, authenticator)
    assert login.status_code == 200, login.text
    body = r.json()
    return Owner(client, authenticator, login.json()["user"], body["household"])


def totp_code(secret: str, offset_steps: int = 0) -> str:
    return pyotp.TOTP(secret).at(time.time() + 30 * offset_steps)


async def enable_totp(client: httpx.AsyncClient) -> tuple[str, list[str]]:
    setup = await client.post("/api/auth/totp/setup", json={})
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    enable = await client.post("/api/auth/totp/enable", json={"code": totp_code(secret)})
    assert enable.status_code == 200, enable.text
    return secret, enable.json()["recovery_codes"]


# --------------------------------------------------------------------------- direct DB helpers


async def seed_household(
    app: FastAPI, name: str, display_name: str, email: str, role: str = "owner"
) -> tuple[uuid.UUID, uuid.UUID, str]:
    """A second tenant created directly (setup only ever creates one). Returns ids + cookie."""
    async with unscoped_session(app.state.session_factory) as s:
        hid = await create_household(s, name)
        await apply_context(s, hid, None)
        person = Person(household_id=hid, display_name=display_name, filing_role="primary")
        s.add(person)
        await s.flush()
        user = AppUser(household_id=hid, person_id=person.id, email=email, role=role)
        s.add(user)
        await s.flush()
        token = await sessions.create(s, user.id, ip=None, user_agent=None, strong=True)
    return hid, person.id, token


async def age_sessions(app: FastAPI, **values: Any) -> None:
    """Rewrite every session's timestamps (e.g. ``reauth_at=...``, ``expires_at=...``)."""
    async with unscoped_session(app.state.session_factory) as s:
        await s.execute(update(AuthSession).values(**values))


def session_cookie(client: httpx.AsyncClient) -> str | None:
    return client.cookies.get(sessions.COOKIE_NAME)
