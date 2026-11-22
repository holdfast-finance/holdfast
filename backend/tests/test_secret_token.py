"""Secret tokens redact incidental display and reject plaintext serialization."""

import copy
import json
import logging
import pickle
import secrets
import traceback
from dataclasses import asdict, dataclass

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic_core import PydanticSerializationError

from app.logging import REDACTED, configure_logging, get_logger
from app.security.secret_token import SecretToken


@pytest.fixture
def credential() -> tuple[str, SecretToken]:
    canary = secrets.token_hex(23)
    return canary, SecretToken(canary)


def test_token_renders_as_constant_mask(credential: tuple[str, SecretToken]) -> None:
    canary, wrapped = credential
    assert not isinstance(wrapped, str)
    assert wrapped.reveal() == canary
    assert str(wrapped) == repr(wrapped) == REDACTED
    assert f"{wrapped:>40.2}" == REDACTED
    assert canary not in repr({"payload": [wrapped]})
    assert not hasattr(wrapped, "__dict__")


def test_serializers_cannot_export_plaintext(credential: tuple[str, SecretToken]) -> None:
    _, wrapped = credential

    @dataclass
    class Envelope:
        credential: SecretToken

    with pytest.raises(TypeError):
        json.dumps(wrapped)
    assert json.dumps(wrapped, default=str) == json.dumps(REDACTED)
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(wrapped)
    with pytest.raises(TypeError, match="cannot be serialized"):
        copy.deepcopy(wrapped)
    with pytest.raises(TypeError, match="cannot be serialized"):
        asdict(Envelope(credential=wrapped))


def test_pydantic_diagnostics_and_json_do_not_expose_token(
    credential: tuple[str, SecretToken],
) -> None:
    canary, wrapped = credential

    class Envelope(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)
        payload: SecretToken

    class PlainText(BaseModel):
        payload: str

    assert Envelope(payload=wrapped).model_dump()["payload"] is wrapped
    with pytest.raises(PydanticSerializationError) as serialization:
        Envelope(payload=wrapped).model_dump_json()
    assert canary not in str(serialization.value)

    with pytest.raises(ValidationError) as validation:
        PlainText.model_validate({"payload": wrapped})
    assert canary not in str(validation.value)
    assert canary not in validation.value.json()


def test_logging_and_tracebacks_mask_token(
    credential: tuple[str, SecretToken], capsys: pytest.CaptureFixture[str]
) -> None:
    canary, wrapped = credential
    configure_logging()
    logging.getLogger("test").warning("payload %s", wrapped, extra={"details": [wrapped]})
    get_logger("test").info("secret-token-canary", payload={"nested": wrapped})

    try:
        raise ValueError(wrapped)
    except ValueError as exc:
        rendered = "".join(traceback.format_exception(exc))

    raw = capsys.readouterr().out
    lines = [json.loads(line) for line in raw.splitlines()]
    assert lines[0]["event"] == f"payload {REDACTED}"
    assert lines[0]["details"] == [REDACTED]
    assert lines[1]["payload"] == {"nested": REDACTED}
    assert f"ValueError: {REDACTED}" in rendered
    assert canary not in raw
    assert canary not in rendered


def test_secret_token_cannot_be_subclassed() -> None:
    with pytest.raises(TypeError):

        class Leaky(SecretToken):  # type: ignore[misc]
            pass
