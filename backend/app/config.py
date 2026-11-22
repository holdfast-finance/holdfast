"""Runtime settings, loaded from the environment (and `.env` in local dev).

Every variable from the Phase 0 contract is declared here. Only what is needed to boot has a
default; optional integrations default to ``None`` so a missing key never crashes startup.
Anything secret is a ``SecretStr`` so it cannot leak through ``repr()`` or logs.
"""

from __future__ import annotations

import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

DEFAULT_DATABASE_URL = "postgresql+psycopg://holdfast_app@db:5432/holdfast"
DEFAULT_REDIS_URL = "redis://redis:6379/0"
# The compose `frontend` network (caddy <-> api/web); pinned in docker-compose.yml.
FRONTEND_SUBNET = "172.30.10.0/24"

CsvList = Annotated[list[str], NoDecode]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Repo-root .env first, then backend/.env; real env vars always win.
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        # A rejected secret must never be echoed into the startup error (and docker logs).
        hide_input_in_errors=True,
        case_sensitive=False,
    )

    # Core
    # "test" exists only for the automated e2e stack: it exposes the first-run setup token at
    # GET /api/test/setup-token (with E2E_HOOKS=true). "prod" can never expose it
    # (see app.api.routes_testing).
    app_env: Literal["dev", "test", "prod"] = "dev"
    # Where credentials come from (ADR 0015): "self_host" reads the environment, "hosted" reads the
    # per-household encrypted settings row. Exactly one source per mode, never both. Any other
    # value fails startup.
    app_deployment: Literal["self_host", "hosted"] = "self_host"
    # Second switch for the e2e hooks: /api/test/* is mounted only when APP_ENV=test *and*
    # E2E_HOOKS=true, so a stray APP_ENV=test alone never exposes the setup token.
    e2e_hooks: bool = False
    app_base_url: str = "https://localhost"
    app_host: str = "localhost"
    caddy_bind: str = "127.0.0.1"
    tailscale_bind_ip: str | None = None
    cf_api_token: SecretStr | None = None
    session_secret: SecretStr | None = None
    fernet_key: SecretStr | None = None
    # Stable (never rotated) key for CSV/OFX import id tokens. See ``imports.household_id_key``.
    import_id_key: SecretStr | None = None
    # Runtime connection: the holdfast_app role (no superuser, no BYPASSRLS, no DDL).
    database_url: SecretStr = SecretStr(DEFAULT_DATABASE_URL)
    # Alembic only: the holdfast_owner role. Unset falls back to DATABASE_URL.
    migrations_database_url: SecretStr | None = None
    # Bootstrap superuser (holdfast_admin). Read by the postgres image only; the app never uses it.
    postgres_password: SecretStr | None = None
    # Fill the password into DATABASE_URL / MIGRATIONS_DATABASE_URL when the URL carries none.
    postgres_app_password: SecretStr | None = None
    postgres_owner_password: SecretStr | None = None
    redis_url: SecretStr = SecretStr(DEFAULT_REDIS_URL)
    # Fill the password into REDIS_URL when the URL carries none.
    redis_password: SecretStr | None = None
    document_store: Path = Path("/data/documents")

    # Auth (PLAN 12). WebAuthn RP id is APP_HOST; the expected origin is https://{APP_HOST}
    # unless WEBAUTHN_ORIGIN overrides it (e.g. a non-443 port).
    webauthn_origin: str | None = None
    # Peers whose X-Forwarded-For is trusted. Comma-separated CIDRs. Default: loopback plus the
    # compose `frontend` network, whose subnet is pinned in docker-compose.yml (FRONTEND_SUBNET).
    # Only Caddy should be able to reach the API there; the API port is never published.
    trusted_proxies: CsvList = Field(
        default_factory=lambda: ["127.0.0.0/8", "::1/128", FRONTEND_SUBNET]
    )

    # Plaid
    plaid_env: Literal["sandbox", "production"] = "sandbox"
    plaid_client_id: str | None = None
    plaid_secret_sandbox: SecretStr | None = None
    plaid_secret_production: SecretStr | None = None
    plaid_products: CsvList = Field(
        default_factory=lambda: [
            "transactions",
            "auth",
            "balance",
            "investments",
            "liabilities",
            "statements",
        ]
    )
    plaid_country_codes: CsvList = Field(default_factory=lambda: ["US"])
    plaid_redirect_uri: str | None = None
    plaid_webhook_url: str | None = None
    plaid_tx_days_requested: int = Field(default=730, ge=1, le=730)

    # SimpleFIN
    simplefin_access_url: SecretStr | None = None

    # AI
    ai_provider: Literal["anthropic", "ollama", "openai_compatible"] = "anthropic"
    anthropic_api_key: SecretStr | None = None
    agent_model_review: str = "claude-opus-5-5"
    agent_model_chat: str = "claude-sonnet-5-5"
    agent_monthly_token_budget: int = Field(default=2_000_000, ge=0)
    ollama_base_url: str | None = None
    openai_compat_base_url: str | None = None
    openai_compat_api_key: SecretStr | None = None

    # Notifications
    smtp_host: str | None = None
    smtp_user: str | None = None
    smtp_pass: SecretStr | None = None
    alert_email: str | None = None
    ntfy_url: str | None = None
    # A ntfy topic name acts as a shared secret on public servers.
    ntfy_topic: SecretStr | None = None

    # Household (no personal defaults: this codebase ships to strangers)
    household_state: str | None = None
    history_start: date | None = None

    # Feature flags
    feature_tax: bool = False
    feature_crypto_spend: bool = False
    feature_public_sector: bool = False

    # Edge
    web_upstream: str = "web:80"

    @field_validator("redis_password")
    @classmethod
    def _redis_password_charset(cls, value: SecretStr | None) -> SecretStr | None:
        # arq's RedisSettings.from_dsn never URL-decodes, so reserved characters would reach the
        # worker still encoded. gen-keys only produces this alphabet.
        if value is not None and not re.fullmatch(r"[A-Za-z0-9_-]+", value.get_secret_value()):
            raise ValueError("REDIS_PASSWORD may only contain A-Z a-z 0-9 _ - (run make keys)")
        return value

    @field_validator("plaid_products", "plaid_country_codes", "trusted_proxies", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"

    @property
    def expected_origin(self) -> str:
        return self.webauthn_origin or f"https://{self.app_host}"

    @property
    def sqlalchemy_url(self) -> URL:
        """DATABASE_URL (app role) as a SQLAlchemy URL, filling the password from
        POSTGRES_APP_PASSWORD when the URL carries none. Never log the rendered form."""
        return _with_password(self.database_url, self.postgres_app_password)

    @property
    def migrations_sqlalchemy_url(self) -> URL:
        """MIGRATIONS_DATABASE_URL (owner role), password filled from POSTGRES_OWNER_PASSWORD.
        Falls back to :attr:`sqlalchemy_url` when MIGRATIONS_DATABASE_URL is unset."""
        if self.migrations_database_url is None:
            return self.sqlalchemy_url
        return _with_password(self.migrations_database_url, self.postgres_owner_password)

    @property
    def redis_dsn(self) -> str:
        """REDIS_URL with the password filled from REDIS_PASSWORD when the URL carries none.
        Never log it."""
        url = make_url(self.redis_url.get_secret_value())
        if url.password is None and self.redis_password is not None:
            # An empty username renders as `redis://:password@host`, which redis-py reads as
            # "default user".
            url = url.set(
                username=url.username or "", password=self.redis_password.get_secret_value()
            )
        return url.render_as_string(hide_password=False)


def _with_password(dsn: SecretStr, password: SecretStr | None) -> URL:
    url = make_url(dsn.get_secret_value())
    if url.password is None and password is not None:
        url = url.set(password=password.get_secret_value())
    return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
