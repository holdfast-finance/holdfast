"""Secrets never leak into responses, logs or the audit log.

The setup token is logged exactly once, on purpose, and must survive the redaction filter
intact. Nothing else secret (session cookie, invite token, TOTP secret or codes, recovery codes,
passwords) may appear anywhere in captured output.
"""

from __future__ import annotations

import json
import logging
import uuid

import pytest
from fastapi import FastAPI
from sqlalchemy import text

from app.db_context import scoped_session
from app.logging import configure_logging
from app.main import announce_setup_token
from tests.api.conftest import (
    SoftAuthenticator,
    do_setup,
    make_client,
    passkey_login,
    register_passkey,
    session_cookie,
    totp_code,
)

PASSWORD = "correct horse battery staple"
FORBIDDEN_KEYS = {"password_hash", "totp_secret_enc", "public_key", "token_hash", "code_hash"}


async def test_no_secrets_in_logs_responses_or_audit(
    app: FastAPI, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging()  # bind the JSON handler to the captured stdout
    logging.getLogger("app").setLevel(logging.DEBUG)
    setup_token = await announce_setup_token(app)
    assert setup_token

    secrets: list[str] = [PASSWORD]
    bodies: list[str] = []
    key = SoftAuthenticator()
    async with make_client(app) as c:
        r = await do_setup(c, setup_token)
        bodies.append(r.text)
        secrets.append(session_cookie(c) or "missing")
        bodies.append((await register_passkey(c, key)).text)
        login = await passkey_login(c, key)
        bodies.append(login.text)
        secrets.append(session_cookie(c) or "missing")
        totp_setup = (await c.post("/api/auth/totp/setup", json={})).json()
        secret = totp_setup["secret"]
        code = totp_code(secret)
        enable = await c.post("/api/auth/totp/enable", json={"code": code})
        recovery = enable.json()["recovery_codes"]
        secrets += [secret, *recovery]
        await c.post("/api/auth/password", json={"password": PASSWORD})
        sam = (
            await c.post(
                "/api/household/people",
                json={"display_name": "Sam Example", "filing_role": "spouse"},
            )
        ).json()
        invite = (
            await c.post(
                f"/api/household/people/{sam['id']}/invite", json={"email": "sam@example.com"}
            )
        ).json()["invite_url"]
        invite_token = invite.split("#", 1)[1]
        secrets.append(invite_token)
        for path in ("/api/auth/me", "/api/auth/passkeys", "/api/household/people", "/api/audit"):
            bodies.append((await c.get(path)).text)
        # Failures log too: wrong code, wrong password, bad invite.
        await c.post("/api/auth/reauth/totp", json={"code": "999999"})
        async with make_client(app) as anon:
            await anon.post(
                "/api/auth/password/login",
                json={
                    "email": "alex@example.com",
                    "password": PASSWORD,
                    "recovery_code": recovery[0],
                },
            )
            secrets.append(session_cookie(anon) or "no-session-cookie-placeholder")
            await anon.post("/api/invites/accept", json={"token": invite_token + "x"})
            accepted = await anon.post("/api/invites/accept", json={"token": invite_token})
            bodies.append(accepted.text)

        hid = uuid.UUID(json.loads(bodies[0])["household"]["id"])
        async with scoped_session(app.state.session_factory, hid, None) as s:
            details = (
                (
                    await s.execute(
                        text("SELECT detail::text || coalesce(entity_id, '') FROM audit_log")
                    )
                )
                .scalars()
                .all()
            )

    out = capsys.readouterr()
    logs = out.out + out.err
    # The deliberate line: present once, unmangled, clearly marked.
    lines = [line for line in logs.splitlines() if setup_token in line]
    assert len(lines) == 1, logs
    assert "HOLDFAST FIRST-RUN SETUP TOKEN" in lines[0]
    assert json.loads(lines[0])["level"] == "warning"

    for value in secrets:
        assert value not in logs, "secret in logs"
        for b in bodies:
            assert value not in b, "secret in a response body"
        for d in details:
            assert value not in d, "secret in the audit log"
    # 6-digit codes can collide with timestamp digits, so look for them as quoted values only.
    assert f'"{code}"' not in logs
    for b in bodies:
        assert setup_token not in b
        for k in FORBIDDEN_KEYS:
            assert f'"{k}"' not in b
