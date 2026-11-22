"""The one credential lookup (ADR 0015).

The deployment mode picks the only source: ``self_host`` reads the process environment
(``Settings``), ``hosted`` opens the caller-supplied ``household_setting.value_enc`` row. The other
source is never a candidate, an override or a fill-in. Names outside :data:`ALLOWED_NAMES`
(``fernet_key``, ``session_secret``, database URLs, ...) read neither source.
"""

from __future__ import annotations

import uuid

from pydantic import SecretStr

from app.config import Settings
from app.security import crypto
from app.security.secret_token import SecretToken

ALLOWED_NAMES: frozenset[str] = frozenset(
    {
        "plaid_client_id",
        "plaid_secret_sandbox",
        "plaid_secret_production",
        "simplefin_access_url",
        "anthropic_api_key",
        "openai_compat_api_key",
    }
)


class SecretError(Exception):
    """Base class. Messages are fixed strings: no name-derived value, no source value."""


class SecretNotFound(SecretError):
    def __init__(self) -> None:
        super().__init__("secret is not configured")


class SecretUnknown(SecretError):
    def __init__(self) -> None:
        super().__init__("secret name is not resolvable")


def get_secret(
    household_id: uuid.UUID,
    name: str,
    *,
    settings: Settings,
    household_value_enc: bytes | None = None,
) -> SecretToken:
    """Resolve ``name`` from the single source for ``settings.app_deployment``.

    ``household_value_enc`` is the ``household_setting`` row for ``(household_id, name)``, loaded
    by the caller through a session scoped to that household. It is ignored in ``self_host``.
    The environment value for ``name`` is ignored in ``hosted``. Never falls back across sources.
    """
    if name not in ALLOWED_NAMES:
        raise SecretUnknown
    if settings.app_deployment == "hosted":
        if household_value_enc is None:
            raise SecretNotFound
        value = crypto.open(household_value_enc, key=settings.fernet_key)
        if not value.reveal().strip():
            raise SecretNotFound
        return value
    raw: object = getattr(settings, name, None)
    text = raw.get_secret_value() if isinstance(raw, SecretStr) else raw
    if not isinstance(text, str) or not text.strip():
        raise SecretNotFound
    return SecretToken(text)
