"""Request body cap for ``/api/*`` (1 MiB; larger for the statement upload route, ADR 0020).
Defense in depth behind Caddy's ``request_body``.

Pure ASGI: a declared ``Content-Length`` over the cap is refused before the app runs, and a
chunked body is counted as it streams: past the cap the app sees a client disconnect (so nothing
larger than the cap is ever buffered) and the client gets the 413 whatever the app answered. The
refusal uses the API error shape: ``413 {"error": {"code": "payload_too_large", ...}}``.
"""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY_BYTES = 1024 * 1024
# The one route that takes a file: a 20 MiB PDF (the importer's own cap) plus 1 MiB of multipart
# framing. Exact match on method + path; every other route keeps the default (ADR 0020).
UPLOAD_ROUTE = ("POST", "/api/imports/statements")
UPLOAD_MAX_BODY_BYTES = 21 * 1024 * 1024
_BODY = json.dumps(
    {"error": {"code": "payload_too_large", "message": "That request is too large."}}
).encode()


async def _refuse(send: Send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_BODY)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": _BODY})


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, prefix: str = "/api", max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.prefix = prefix
        self.max_bytes = max_bytes

    def _limit_for(self, scope: Scope) -> int:
        if (scope.get("method"), scope.get("path")) == UPLOAD_ROUTE:
            return max(self.max_bytes, UPLOAD_MAX_BODY_BYTES)
        return self.max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path: str = scope.get("path", "")
        if scope["type"] != "http" or not (
            path == self.prefix or path.startswith(self.prefix + "/")
        ):
            await self.app(scope, receive, send)
            return

        limit = self._limit_for(scope)
        for key, value in scope["headers"]:
            if key == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = limit + 1
                if declared > limit:
                    await _refuse(send)
                    return

        received = 0
        exceeded = False
        started = False

        async def counting_receive() -> Message:
            nonlocal received, exceeded
            if exceeded:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # Stop reading: the app sees a disconnect, never the oversized body.
                    exceeded = True
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if exceeded:
                # Whatever the app made of the cut-off body, the client gets the 413.
                if message["type"] == "http.response.start" and not started:
                    started = True
                    await _refuse(send)
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except Exception:
            if not exceeded:
                raise
        if exceeded and not started:
            await _refuse(send)
