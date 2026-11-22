"""A page cursor is caller input: anything malformed is a validation error, never a 500."""

import base64
import json

import pytest

from app.services.errors import ApiError
from app.services.ledger import decode_cursor


def _cursor(value: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()


@pytest.mark.parametrize(
    "value",
    [
        ["2024-01-01", 5],
        [20240101, "00000000-0000-0000-0000-000000000001"],
        ["2024-01-01", None],
        ["2024-01-01"],
        {"posted": "2024-01-01"},
        "2024-01-01",
        ["2024-13-40", "00000000-0000-0000-0000-000000000001"],
        ["2024-01-01", "not-a-uuid"],
    ],
)
def test_malformed_cursor_is_a_validation_error(value: object) -> None:
    with pytest.raises(ApiError):
        decode_cursor(_cursor(value))
