"""Structured JSON logging with secret redaction.

Both structlog loggers and stdlib loggers (uvicorn, sqlalchemy, arq) render through the same
JSON pipeline, and every event passes the redaction step first. Redaction is defense in depth:
code must still never log secrets on purpose (CLAUDE.md hard rule 3).
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog
from pydantic import SecretBytes, SecretStr

from app.security.secret_token import SecretToken

REDACTED = "***"

# Field names that always carry secrets. Over-masking is fine; under-masking is not.
SENSITIVE_KEY = re.compile(
    r"(token|key|secret|passw(or)?d|passwd|pass\b|pass_|_pass|pwd|authorization|cookie|"
    r"credential|ssn|seed|mnemonic|xprv|private)",
    re.IGNORECASE,
)

# Values that look like secrets wherever they appear.
_URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-z][a-z0-9+.\-]*://)(?P<user>[^:/@\s]*):[^@/\s]+@")
_PLAID_TOKEN = re.compile(r"\b(access|public|link)-(sandbox|development|production)-[\w\-]+")
_BEARER = re.compile(r"(?i)\bbearer\s+[\w\-.~+/]+=*")


def redact_text(text: str) -> str:
    """Mask credentials embedded in free text (URLs with passwords, Plaid tokens, bearer)."""
    text = _URL_CREDENTIALS.sub(rf"\g<scheme>\g<user>:{REDACTED}@", text)
    text = _PLAID_TOKEN.sub(rf"\1-\2-{REDACTED}", text)
    return _BEARER.sub(f"Bearer {REDACTED}", text)


def redact(value: Any, key: str | None = None) -> Any:
    """Return a copy of ``value`` with secret-looking fields and values masked."""
    if key is not None and SENSITIVE_KEY.search(key):
        return REDACTED
    if isinstance(value, SecretStr | SecretBytes | SecretToken):
        return REDACTED
    if isinstance(value, Mapping):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact(v) for v in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor applying :func:`redact` to every key/value of an event."""
    for k in list(event_dict):
        event_dict[k] = redact(event_dict[k], None if k == "event" else k)
    return event_dict


class RedactionFilter(logging.Filter):
    """Stdlib filter: masks secrets in the formatted message and in ``extra=`` attributes."""

    _STANDARD = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message"}

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            try:
                record.msg = record.getMessage()
            except (TypeError, ValueError):
                record.msg = str(record.msg)
            record.args = None
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        # Tracebacks quote exception messages (and source lines), which can carry DSNs or
        # tokens. Pre-render and redact exc_text so stdlib formatters use the masked copy;
        # the structlog pipeline masks its own rendering in redact_exception_processor.
        if record.exc_info and not record.exc_text:
            record.exc_text = _TRACEBACK_FORMATTER.formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact_text(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_text(record.stack_info)
        for attr in set(vars(record)) - self._STANDARD:
            setattr(record, attr, redact(getattr(record, attr), attr))
        return True


_TRACEBACK_FORMATTER = logging.Formatter()

# Keys structlog renders tracebacks / stacks into (format_exc_info, StackInfoRenderer).
_RENDERED_TRACE_KEYS = ("exception", "stack")


def redact_exception_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Mask credentials in rendered tracebacks. Must run after ``format_exc_info``."""
    for key in _RENDERED_TRACE_KEYS:
        value = event_dict.get(key)
        if isinstance(value, str):
            event_dict[key] = redact_text(value)
    return event_dict


def _drop_color_message(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """uvicorn adds an ANSI-colored duplicate of the message; it is noise in JSON."""
    event_dict.pop("color_message", None)
    return event_dict


_SHARED_PROCESSORS: list[Any] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.stdlib.ExtraAdder(),
    _drop_color_message,
    redact_processor,
]


def configure_logging(level: str | int = "INFO", *, json: bool = True) -> None:
    """Configure structlog and the stdlib root logger. Safe to call more than once."""
    renderer: Any = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_SHARED_PROCESSORS,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            redact_exception_processor,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(RedactionFilter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, "_holdfast", False):
            root.removeHandler(existing)
    handler._holdfast = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn installs its own handlers; route its records through ours instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True

    structlog.configure(
        processors=[
            *_SHARED_PROCESSORS,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return logger
