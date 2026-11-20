"""Offline OpenAPI document for the generated web client.

``Settings.model_construct()`` uses declared defaults only. ``Settings()`` would read the
process environment and dotenv files, so a developer's ``APP_ENV`` or ``.env`` must not
change the client or mount the e2e-only routes.
"""

from __future__ import annotations

import json
from typing import Any

from app.config import Settings
from app.main import create_app


def openapi_document() -> dict[str, Any]:
    document: dict[str, Any] = create_app(Settings.model_construct()).openapi()
    return document


def openapi_text() -> str:
    """Canonical JSON, including the trailing newline ``make api-types`` hashes."""
    return json.dumps(openapi_document(), sort_keys=True, indent=2) + "\n"
