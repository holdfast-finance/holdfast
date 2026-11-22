"""Settings: safe defaults, contract coverage, and secrets that never leak through repr."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from app.config import Settings
from tests.conftest import CONTRACT_ENV_VARS, make_settings


def test_defaults_boot_with_empty_environment(clean_env: pytest.MonkeyPatch) -> None:
    s = make_settings()
    assert s.app_env == "dev"
    assert s.plaid_env == "sandbox"
    assert s.feature_tax is False
    assert s.feature_crypto_spend is False
    assert s.feature_public_sector is False
    assert s.ai_provider == "anthropic"
    assert s.plaid_country_codes == ["US"]
    assert s.plaid_tx_days_requested == 730
    assert s.sqlalchemy_url.host == "db"
    assert s.redis_url.get_secret_value() == "redis://redis:6379/0"
    # Optional integrations are absent, not crashing.
    assert s.plaid_client_id is None
    assert s.anthropic_api_key is None
    assert s.fernet_key is None
    # No personal assumptions baked in.
    assert s.household_state is None
    assert s.history_start is None


def test_every_contract_variable_is_a_setting() -> None:
    fields = {name.upper() for name in Settings.model_fields}
    assert set(CONTRACT_ENV_VARS) <= fields


def test_plaid_env_rejects_unknown_values(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("PLAID_ENV", "development")
    with pytest.raises(ValidationError):
        make_settings()


def test_empty_env_values_mean_unset(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("PLAID_ENV", "")
    clean_env.setenv("PLAID_WEBHOOK_URL", "")
    s = make_settings()
    assert s.plaid_env == "sandbox"
    assert s.plaid_webhook_url is None


def test_csv_lists_and_flags_parse_from_env(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("PLAID_PRODUCTS", "transactions, auth ,balance")
    clean_env.setenv("PLAID_COUNTRY_CODES", "US,CA")
    clean_env.setenv("FEATURE_TAX", "true")
    clean_env.setenv("APP_ENV", "prod")
    s = make_settings()
    assert s.plaid_products == ["transactions", "auth", "balance"]
    assert s.plaid_country_codes == ["US", "CA"]
    assert s.feature_tax is True
    assert s.is_prod


def test_secrets_are_secretstr_and_not_in_repr(clean_env: pytest.MonkeyPatch) -> None:
    secrets = {
        "SESSION_SECRET": "session-value-123",
        "FERNET_KEY": "fernet-value-456",
        "PLAID_SECRET_SANDBOX": "plaid-sandbox-789",
        "PLAID_SECRET_PRODUCTION": "plaid-prod-000",
        "ANTHROPIC_API_KEY": "anthropic-key-abc",
        "OPENAI_COMPAT_API_KEY": "compat-key-def",
        "SMTP_PASS": "smtp-pass-ghi",
        "CF_API_TOKEN": "cf-token-jkl",
        "POSTGRES_PASSWORD": "pg-pass-mno",
        "POSTGRES_APP_PASSWORD": "pg-app-pass-vwx",
        "POSTGRES_OWNER_PASSWORD": "pg-owner-pass-abc",
        "MIGRATIONS_DATABASE_URL": "postgresql+psycopg://holdfast_owner:own-pass@db:5432/holdfast",
        "SIMPLEFIN_ACCESS_URL": "https://user:sf-pass-pqr@simplefin.example/x",
        "DATABASE_URL": "postgresql+psycopg://holdfast:db-pass-stu@db:5432/holdfast",
    }
    for name, value in secrets.items():
        clean_env.setenv(name, value)
    s = make_settings()
    rendered = repr(s) + str(s) + s.model_dump_json()
    for name, value in secrets.items():
        assert isinstance(getattr(s, name.lower()), SecretStr), name
        assert value not in rendered, name


def test_app_password_fills_runtime_url(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("POSTGRES_APP_PASSWORD", "pw-from-env")
    clean_env.setenv("POSTGRES_PASSWORD", "admin-must-not-be-used")
    url = make_settings().sqlalchemy_url
    assert url.username == "holdfast_app"
    assert url.password == "pw-from-env"
    assert "pw-from-env" not in str(url)  # SQLAlchemy masks it when stringified


def test_explicit_url_password_wins(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("POSTGRES_APP_PASSWORD", "ignored")
    clean_env.setenv("DATABASE_URL", "postgresql+psycopg://holdfast_app:explicit@db:5432/holdfast")
    assert make_settings().sqlalchemy_url.password == "explicit"


def test_migrations_url_uses_owner_role_and_password(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("DATABASE_URL", "postgresql+psycopg://holdfast_app@db:5432/holdfast")
    clean_env.setenv(
        "MIGRATIONS_DATABASE_URL", "postgresql+psycopg://holdfast_owner@db:5432/holdfast"
    )
    clean_env.setenv("POSTGRES_APP_PASSWORD", "app-pw")
    clean_env.setenv("POSTGRES_OWNER_PASSWORD", "owner-pw")
    s = make_settings()
    assert (s.sqlalchemy_url.username, s.sqlalchemy_url.password) == ("holdfast_app", "app-pw")
    mig = s.migrations_sqlalchemy_url
    assert (mig.username, mig.password) == ("holdfast_owner", "owner-pw")


def test_migrations_url_falls_back_to_database_url(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("DATABASE_URL", "postgresql+psycopg://holdfast_app:explicit@db:5432/holdfast")
    assert make_settings().migrations_sqlalchemy_url.username == "holdfast_app"


def test_redis_password_is_filled_into_a_passwordless_url(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("REDIS_PASSWORD", "pw-1_x")
    s = make_settings()
    assert s.redis_dsn == "redis://:pw-1_x@redis:6379/0"
    assert "pw-1_x" not in repr(s)


def test_redis_url_with_its_own_password_wins(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("REDIS_URL", "redis://:inline@redis:6379/3")
    clean_env.setenv("REDIS_PASSWORD", "other")
    assert make_settings().redis_dsn == "redis://:inline@redis:6379/3"


def test_redis_dsn_without_a_password_is_the_plain_url(clean_env: pytest.MonkeyPatch) -> None:
    assert make_settings().redis_dsn == "redis://redis:6379/0"
