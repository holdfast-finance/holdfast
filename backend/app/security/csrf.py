"""CSRF: every non-GET ``/api/*`` request must carry ``X-Holdfast-CSRF: 1``.

SameSite=Strict on the session cookie already stops cross-site sends. The custom header is the
second layer: a cross-origin page can't set it without a CORS preflight, which we never allow.
Pure ASGI so it runs before routing, body parsing and auth.
"""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Receive, Scope, Send

CSRF_HEADER = b"x-holdfast-csrf"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_BODY = json.dumps(
    {"error": {"code": "csrf_failed", "message": "Missing CSRF header. Reload the page."}}
).encode()


class CSRFMiddleware:
    def __init__(self, app: ASGIApp, prefix: str = "/api") -> None:
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in SAFE_METHODS:
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        if not (path == self.prefix or path.startswith(self.prefix + "/")):
            await self.app(scope, receive, send)
            return
        values = [v for k, v in scope["headers"] if k == CSRF_HEADER]
        if values == [b"1"]:
            await self.app(scope, receive, send)
            return
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(_BODY)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _BODY})
