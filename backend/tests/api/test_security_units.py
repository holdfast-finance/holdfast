"""Unit tests for the security helpers (no services needed)."""

from __future__ import annotations

import ipaddress

from app.security import passwords, totp
from app.security.client_ip import resolve_client_ip
from app.security.tokens import new_token, same_hash, token_hash

TRUSTED = ("127.0.0.0/8", "172.16.0.0/12")


def test_forwarded_for_only_from_trusted_proxy() -> None:
    ip = ipaddress.ip_address
    assert resolve_client_ip("172.18.0.5", "198.51.100.7", TRUSTED) == ip("198.51.100.7")
    # Untrusted peer: header ignored (spoofing attempt).
    assert resolve_client_ip("203.0.113.9", "198.51.100.7", TRUSTED) == ip("203.0.113.9")
    # Client-supplied left part is ignored; right-most untrusted hop wins.
    assert resolve_client_ip("172.18.0.5", "1.2.3.4, 198.51.100.7", TRUSTED) == ip("198.51.100.7")
    assert resolve_client_ip("172.18.0.5", "198.51.100.7, 172.18.0.9", TRUSTED) == ip(
        "198.51.100.7"
    )
    assert resolve_client_ip("172.18.0.5", None, TRUSTED) == ip("172.18.0.5")
    assert resolve_client_ip("172.18.0.5", "garbage", TRUSTED) == ip("172.18.0.5")
    assert resolve_client_ip(None, "198.51.100.7", TRUSTED) is None


def test_tokens_are_random_and_hashed() -> None:
    a, b = new_token(), new_token()
    assert a != b and len(a) >= 43
    assert len(token_hash(a)) == 32
    assert same_hash(token_hash(a), token_hash(a))
    assert not same_hash(token_hash(a), token_hash(b))


def test_argon2id_password_hashing() -> None:
    h = passwords.hash_password("correct horse battery staple")
    assert h.startswith("$argon2id$")
    assert passwords.verify_password(h, "correct horse battery staple")
    assert not passwords.verify_password(h, "wrong")
    assert not passwords.verify_password(None, "anything")
    assert not passwords.verify_password("not-a-hash", "anything")


def test_recovery_codes() -> None:
    codes = totp.new_recovery_codes()
    assert len(codes) == 10 and len(set(codes)) == 10
    assert all(len(c) == 19 and c.count("-") == 3 for c in codes)
    c = codes[0]
    assert totp.recovery_code_hash(c) == totp.recovery_code_hash(c.upper().replace("-", ""))
    assert len(totp.recovery_code_hash(c)) == 64
