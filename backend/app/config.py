"""Runtime settings, loaded from the environment (and `.env` in local dev).

Every variable from the Phase 0 contract is declared here. Only what is needed to boot has a
default; optional integrations default to ``None`` so a missing key never crashes startup.
Anything secret is a ``SecretStr`` so it cannot leak through ``repr()`` or logs.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

DEFAULT_DATABASE_URL = "postgresql+psycopg://holdfast@db:5432/holdfast"
DEFAULT_REDIS_URL = "redis://redis:6379/0"

CsvList = Annotated[list[str], NoDecode]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Repo-root .env first, then backend/.env; real env vars always win.
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=False,
    )

    # Core
    app_env: Literal["dev", "prod"] = "dev"
    app_base_url: str = "https://localhost"
    app_host: str = "localhost"
    caddy_bind: str = "127.0.0.1"
    tailscale_bind_ip: str | None = None
    cf_api_token: SecretStr | None = None
    session_secret: SecretStr | None = None
    fernet_key: SecretStr | None = None
    database_url: SecretStr = SecretStr(DEFAULT_DATABASE_URL)
    postgres_password: SecretStr | None = None
    redis_url: SecretStr = SecretStr(DEFAULT_REDIS_URL)
    document_store: Path = Path("/data/documents")

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

    @field_validator("plaid_products", "plaid_country_codes", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"

    @property
    def sqlalchemy_url(self) -> URL:
        """DATABASE_URL as a SQLAlchemy URL, filling the password from POSTGRES_PASSWORD
        when the URL carries none. Never log the rendered form of this value."""
        url = make_url(self.database_url.get_secret_value())
        if url.password is None and self.postgres_password is not None:
            url = url.set(password=self.postgres_password.get_secret_value())
        return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
