"""get_secret: one source per deployment mode, no fallback, fixed error text (ADR 0015)."""

from __future__ import annotations

import secrets
import uuid

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.config import Settings
from app.security import crypto
from app.security.secret_token import SecretToken
from app.security.secrets import SecretNotFound, SecretUnknown, get_secret
from tests.conftest import make_settings

HOUSEHOLD = uuid.uuid4()


def _canaries() -> tuple[str, str]:
    return "env-" + secrets.token_hex(12), "row-" + secrets.token_hex(12)


def _settings(mode: str, env_value: str | None, ring: str) -> Settings:
    return make_settings(
        app_deployment=mode,
        fernet_key=SecretStr(ring),
        anthropic_api_key=None if env_value is None else SecretStr(env_value),
    )


def test_self_host_returns_env_and_ignores_the_household_row() -> None:
    ring = crypto.generate_key()
    env_canary, row_canary = _canaries()
    row = _row(row_canary, ring)
    got = get_secret(
        HOUSEHOLD,
        "anthropic_api_key",
        settings=_settings("self_host", env_canary, ring),
        household_value_enc=row,
    )
    assert isinstance(got, SecretToken)
    assert got.reveal() == env_canary


def _row(
    value: str, ring: str, name: str = "anthropic_api_key", hid: uuid.UUID = HOUSEHOLD
) -> bytes:
    ctx = crypto.make_context(hid, "household_setting", "value_enc", name)
    return crypto.seal(SecretToken(value), ctx, key=SecretStr(ring))


def test_hosted_returns_the_row_and_ignores_env() -> None:
    ring = crypto.generate_key()
    env_canary, row_canary = _canaries()
    row = _row(row_canary, ring)
    got = get_secret(
        HOUSEHOLD,
        "anthropic_api_key",
        settings=_settings("hosted", env_canary, ring),
        household_value_enc=row,
    )
    assert got.reveal() == row_canary


def test_hosted_without_a_row_does_not_fall_back_to_env() -> None:
    ring = crypto.generate_key()
    env_canary, _ = _canaries()
    with pytest.raises(SecretNotFound) as excinfo:
        get_secret(HOUSEHOLD, "anthropic_api_key", settings=_settings("hosted", env_canary, ring))
    assert env_canary not in str(excinfo.value)


def test_self_host_without_env_does_not_use_the_row() -> None:
    ring = crypto.generate_key()
    _, row_canary = _canaries()
    row = _row(row_canary, ring)
    with pytest.raises(SecretNotFound) as excinfo:
        get_secret(
            HOUSEHOLD,
            "anthropic_api_key",
            settings=_settings("self_host", None, ring),
            household_value_enc=row,
        )
    assert row_canary not in str(excinfo.value)


@pytest.mark.parametrize("empty", ["", "   "])
def test_self_host_empty_env_value_is_not_found(empty: str) -> None:
    settings = make_settings(
        app_deployment="self_host", plaid_client_id=empty, anthropic_api_key=SecretStr(empty)
    )
    for name in ("plaid_client_id", "anthropic_api_key"):
        with pytest.raises(SecretNotFound):
            get_secret(HOUSEHOLD, name, settings=settings)


def test_hosted_empty_plaintext_is_not_found() -> None:
    ring = crypto.generate_key()
    row = _row("  ", ring)
    with pytest.raises(SecretNotFound):
        get_secret(
            HOUSEHOLD,
            "anthropic_api_key",
            settings=_settings("hosted", None, ring),
            household_value_enc=row,
        )


@pytest.mark.parametrize(
    "name",
    [
        "fernet_key",
        "session_secret",
        "database_url",
        "postgres_password",
        "smtp_pass",
        "ntfy_topic",
        "cf_api_token",
        "migrations_database_url",
        "nope",
    ],
)
@pytest.mark.parametrize("mode", ["self_host", "hosted"])
def test_names_outside_the_allowlist_read_neither_source(name: str, mode: str) -> None:
    ring = crypto.generate_key()
    settings = make_settings(
        app_deployment=mode,
        fernet_key=SecretStr(ring),
        session_secret=SecretStr("s" * 40),
        cf_api_token=SecretStr("c" * 40),
    )
    row = _row("row-value", ring)
    with pytest.raises(SecretUnknown) as excinfo:
        get_secret(HOUSEHOLD, name, settings=settings, household_value_enc=row)
    assert ring not in str(excinfo.value)


def test_unknown_deployment_mode_fails_startup() -> None:
    with pytest.raises(ValueError):
        make_settings(app_deployment="both")


def test_default_deployment_is_self_host(clean_env: pytest.MonkeyPatch) -> None:
    assert make_settings().app_deployment == "self_host"


@pytest.mark.parametrize(
    ("hid", "name"),
    [
        (uuid.uuid4(), "anthropic_api_key"),  # another household
        (HOUSEHOLD, "openai_compat_api_key"),  # another setting name
        (HOUSEHOLD, "plaid_secret_production"),
    ],
)
def test_hosted_row_copied_to_another_household_or_name_fails_closed(
    hid: uuid.UUID, name: str
) -> None:
    ring = crypto.generate_key()
    canary = "moved-" + secrets.token_hex(12)
    row = _row(canary, ring, name, hid)
    with pytest.raises(crypto.DecryptionError) as excinfo:
        get_secret(
            HOUSEHOLD,
            "anthropic_api_key",
            settings=_settings("hosted", None, ring),
            household_value_enc=row,
        )
    assert canary not in str(excinfo.value) and canary not in repr(excinfo.value)


def test_hosted_row_for_a_different_column_fails_closed() -> None:
    ring = crypto.generate_key()
    ctx = crypto.make_context(HOUSEHOLD, "bank_connection", "access_token_enc", "anthropic_api_key")
    row = crypto.seal(SecretToken("x-canary"), ctx, key=SecretStr(ring))
    with pytest.raises(crypto.DecryptionError):
        get_secret(
            HOUSEHOLD,
            "anthropic_api_key",
            settings=_settings("hosted", None, ring),
            household_value_enc=row,
        )


def test_hosted_refuses_an_unbound_row_even_if_it_opens_unbound() -> None:
    """A copied TOTP seed (hf1) or a bare Fernet token must not open as a household secret."""
    ring = crypto.generate_key()
    hf1 = crypto.seal_unbound(SecretToken("totp-seed-canary"), key=SecretStr(ring))
    bare = Fernet(ring.encode()).encrypt(b"legacy-canary")
    assert hf1.startswith(b"hf1.")
    for row in (hf1, bare):
        with pytest.raises(crypto.DecryptionError) as excinfo:
            get_secret(
                HOUSEHOLD,
                "anthropic_api_key",
                settings=_settings("hosted", None, ring),
                household_value_enc=row,
            )
        assert "canary" not in str(excinfo.value)
