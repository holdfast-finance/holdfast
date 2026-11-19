"""Structured logs mask secret fields and secret-looking values."""

from __future__ import annotations

import json
import logging

import pytest
from pydantic import SecretStr

from app.logging import REDACTED, configure_logging, get_logger, redact

FAKE_TOKEN = "access-sandbox-11111111-2222-3333-4444-555555555555"


def _lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, object]]:
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def test_redact_masks_sensitive_keys_recursively() -> None:
    data = {
        "access_token": "abc",
        "api_key": "def",
        "client_secret": "ghi",
        "password": "jkl",
        "nested": {"SMTP_PASS": "mno", "ok": "visible"},
        "items": [{"refresh_token": "pqr"}],
        "amount": "-42.10",
    }
    out = redact(data)
    assert out["access_token"] == REDACTED
    assert out["api_key"] == REDACTED
    assert out["client_secret"] == REDACTED
    assert out["password"] == REDACTED
    assert out["nested"] == {"SMTP_PASS": REDACTED, "ok": "visible"}
    assert out["items"] == [{"refresh_token": REDACTED}]
    assert out["amount"] == "-42.10"


def test_redact_masks_values_that_look_secret() -> None:
    text = f"dsn=postgresql://holdfast:pw123@db:5432/holdfast tok={FAKE_TOKEN} h=Bearer abc.def"
    out = redact(text)
    assert "pw123" not in out
    assert "11111111" not in out
    assert "abc.def" not in out
    assert "holdfast:***@db" in out
    assert redact(SecretStr("x")) == REDACTED


def test_structlog_events_are_json_and_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging()
    get_logger("test").info(
        "plaid.exchange", access_token=FAKE_TOKEN, item_id="item-1", secret=SecretStr("s")
    )
    (line,) = [e for e in _lines(capsys) if e.get("event") == "plaid.exchange"]
    assert line["access_token"] == REDACTED
    assert line["secret"] == REDACTED
    assert line["item_id"] == "item-1"
    assert line["level"] == "info"
    assert FAKE_TOKEN not in json.dumps(line)


def test_stdlib_records_are_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging()
    logging.getLogger("third.party").warning(
        "connecting to %s", "redis://:hunter2@redis:6379/0", extra={"api_key": "sk-fake"}
    )
    raw = capsys.readouterr().out
    assert "hunter2" not in raw
    assert "sk-fake" not in raw
    (line,) = [json.loads(x) for x in raw.splitlines() if "connecting" in x]
    assert line["api_key"] == REDACTED
    assert "redis://:***@redis:6379/0" in str(line["event"])


# Synthetic credentials only.
EXC_URL = "https://user:secret@example.invalid/path"
EXC_DSN = "postgresql+psycopg://holdfast_app:dsn-pw-123@db:5432/holdfast"


def _boom() -> None:
    raise RuntimeError(f"upstream failed: {EXC_URL} via {EXC_DSN}")


def _assert_masked(raw: str) -> None:
    assert ":secret@" not in raw
    assert "dsn-pw-123" not in raw
    assert "user:***@example.invalid" in raw
    assert "holdfast_app:***@db" in raw


def test_structlog_exception_text_is_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging()
    try:
        _boom()
    except RuntimeError:
        get_logger("test").exception("job.failed")
    raw = capsys.readouterr().out
    (line,) = [json.loads(x) for x in raw.splitlines() if "job.failed" in x]
    assert "RuntimeError" in str(line["exception"])
    _assert_masked(raw)


def test_stdlib_exception_text_is_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging()
    try:
        _boom()
    except RuntimeError:
        logging.getLogger("third.party").exception("worker crashed")
    raw = capsys.readouterr().out
    (line,) = [json.loads(x) for x in raw.splitlines() if "worker crashed" in x]
    assert "RuntimeError" in str(line["exception"])
    _assert_masked(raw)


def test_filter_redacts_exc_text_for_plain_stdlib_formatters() -> None:
    from app.logging import RedactionFilter

    try:
        _boom()
    except RuntimeError:
        import sys

        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "boom", None, sys.exc_info())
    assert RedactionFilter().filter(record)
    rendered = logging.Formatter().format(record)
    assert "RuntimeError" in rendered
    _assert_masked(rendered)
