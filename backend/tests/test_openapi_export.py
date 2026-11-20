"""The client contract is reproducible from source, even on a configured dev machine."""

from __future__ import annotations

import hashlib
import importlib
import json
import pkgutil
from decimal import Decimal
from pathlib import Path
from typing import Annotated, get_args, get_origin, get_type_hints

import pytest
from fastapi import FastAPI
from pydantic import BaseModel, ValidationError
from pydantic_core import PydanticSerializationError
from pydantic_settings.main import DotEnvSettingsSource

import app.api as api_pkg
import app.schemas as schemas_pkg
from app.config import Settings
from app.openapi_export import openapi_document, openapi_text
from app.schemas.common import DECIMAL_STRING, DecimalStr, Model, MoneyStr, PriceStr, QuantityStr
from scripts.dump_openapi import main

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DTS = ROOT / "web" / "src" / "api" / "schema.d.ts"
# Keep in lockstep with the header `make api-types` writes.
SCHEMA_HASH_PREFIX = "// holdfast-schema-sha256: "


def _walk_models() -> list[type[BaseModel]]:
    modules = [api_pkg, schemas_pkg]
    for package in (api_pkg, schemas_pkg):
        prefix = package.__name__ + "."
        for info in pkgutil.walk_packages(package.__path__, prefix):
            modules.append(importlib.import_module(info.name))
    found: list[type[BaseModel]] = []
    seen: set[type[BaseModel]] = set()
    for module in modules:
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, BaseModel)
                and value is not BaseModel
                and value not in seen
            ):
                seen.add(value)
                found.append(value)
    return found


def has_bare_decimal(annotation: object) -> bool:
    """True when a field is ``Decimal`` without decimal string metadata (or a container of one)."""
    if annotation is Decimal:
        return True
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Annotated:
        base, *meta = args
        if base is Decimal:
            return DECIMAL_STRING not in meta
        return has_bare_decimal(base)
    return any(has_bare_decimal(arg) for arg in args)


def assert_no_json_number(node: object, where: str = "openapi") -> None:
    if isinstance(node, dict):
        if node.get("type") == "number":
            raise AssertionError(f"{where} contains a JSON number schema")
        for key, value in node.items():
            assert_no_json_number(value, f"{where}.{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            assert_no_json_number(item, f"{where}[{index}]")


def test_schema_export_is_offline_and_reproducible(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    main()
    expected = capsys.readouterr().out
    assert expected == openapi_text()
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("E2E_HOOKS", "true")
    monkeypatch.setenv("DATABASE_URL", "deliberately-invalid")
    main()
    assert capsys.readouterr().out == expected
    paths = json.loads(expected)["paths"]
    assert "/api/health" in paths
    assert not any(path.startswith("/api/test/") for path in paths)


def test_dump_does_not_read_dotenv_or_deployment_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real_init = DotEnvSettingsSource.__init__

    def wrapped(self: DotEnvSettingsSource, *args: object, **kwargs: object) -> None:
        calls.append("dotenv")
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(DotEnvSettingsSource, "__init__", wrapped)
    openapi_text()
    assert calls == []
    Settings(_env_file=None)
    assert calls == ["dotenv"]


def test_generated_types_match_the_contract() -> None:
    digest = hashlib.sha256(openapi_text().encode()).hexdigest()
    first = SCHEMA_DTS.read_text(encoding="utf-8").splitlines()[0]
    assert first == f"{SCHEMA_HASH_PREFIX}{digest}"


def test_shipping_contract_has_no_json_number() -> None:
    assert_no_json_number(openapi_document())


def test_json_number_schema_is_rejected() -> None:
    with pytest.raises(AssertionError, match="JSON number"):
        assert_no_json_number({"properties": {"amount": {"type": "number"}}})


def test_api_models_do_not_use_bare_decimal() -> None:
    bare: list[str] = []
    for model in _walk_models():
        hints = get_type_hints(model, include_extras=True)
        for name, annotation in hints.items():
            if has_bare_decimal(annotation):
                bare.append(f"{model.__module__}.{model.__name__}.{name}")
    assert bare == []


class _AmountIn(Model):
    amount: DecimalStr


class _StoredAmounts(Model):
    amount: MoneyStr
    price: PriceStr
    quantity: QuantityStr


class _Bare(Model):
    amount: Decimal


def money_openapi_document() -> dict[str, object]:
    app = FastAPI()

    @app.post("/amount", operation_id="genericAmount")
    def _post(body: _AmountIn) -> _AmountIn:
        return body

    @app.post("/stored-amounts", operation_id="money")
    def _post_stored(body: _StoredAmounts) -> _StoredAmounts:
        return body

    return app.openapi()


def test_decimal_str_is_a_string_and_rejects_json_numbers() -> None:
    parsed = _AmountIn.model_validate_json('{"amount": "-42.10"}')
    assert parsed.amount == Decimal("-42.10")
    assert parsed.model_dump(mode="json") == {"amount": "-42.10"}
    with pytest.raises(ValueError):
        _AmountIn.model_validate_json('{"amount": -42.10}')
    with pytest.raises(ValueError):
        _AmountIn.model_validate_json('{"amount": 1}')
    bare_hints = get_type_hints(_Bare, include_extras=True)
    good_hints = get_type_hints(_AmountIn, include_extras=True)
    assert has_bare_decimal(bare_hints["amount"])
    assert not has_bare_decimal(good_hints["amount"])

    dumped = json.dumps(money_openapi_document())
    assert '"type": "number"' not in dumped
    assert '"type": "string"' in dumped


@pytest.mark.parametrize(
    "value",
    [
        "1e9",
        "+1",
        "1_000",
        " 1 ",
        "1.",
        ".5",
        "01",
        "1\n",
        "1.٢",
        "1" * 30,
        "100000000000000",
        "0.123456789",
        "NaN",
        "Infinity",
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("1e50000000"),
        Decimal("1e-50000000"),
        Decimal("100000000000000"),
        Decimal("1.000000000"),
    ],
)
def test_decimal_str_rejects_invalid_syntax_and_out_of_bounds_values(
    value: str | Decimal,
) -> None:
    with pytest.raises(ValueError):
        _AmountIn.model_validate({"amount": value})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0", "0"),
        ("-0", "0"),
        (Decimal("-0.00"), "0"),
        ("-12.34", "-12.34"),
        ("123.45678901", "123.45678901"),
        ("-99999999999999.99999999", "-99999999999999.99999999"),
        (Decimal("1E+13"), "10000000000000"),
        (Decimal("1E-8"), "0.00000001"),
    ],
)
def test_decimal_str_serializes_bounded_fixed_point_strings(
    value: str | Decimal,
    expected: str,
) -> None:
    assert _AmountIn.model_validate({"amount": value}).model_dump(mode="json") == {
        "amount": expected
    }


def test_decimal_str_openapi_publishes_bounded_pattern() -> None:
    schema = json.loads(json.dumps(money_openapi_document()))
    amount = schema["components"]["schemas"]["_AmountIn"]["properties"]["amount"]
    assert amount["type"] == "string"
    assert amount["pattern"] == r"^-?(?:0|[1-9][0-9]{0,13})(?:\.[0-9]{1,8})?$"


@pytest.fixture
def stored_amounts() -> dict[str, str]:
    return {
        "amount": "-999999999999.99",
        "price": "9999999999.99999999",
        "quantity": "999999999999999999.9999999999",
    }


def test_storage_aliases_accept_column_limits(stored_amounts: dict[str, str]) -> None:
    assert _StoredAmounts.model_validate(stored_amounts).model_dump(mode="json") == stored_amounts


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("amount", "0.005"),
        ("amount", "1000000000000"),
        ("price", "0.123456789"),
        ("price", "10000000000"),
        ("quantity", "0.12345678901"),
        ("quantity", "1000000000000000000"),
    ],
)
def test_storage_aliases_reject_excess_precision(
    stored_amounts: dict[str, str], field: str, value: str
) -> None:
    for invalid in (value, Decimal(value)):
        with pytest.raises(ValidationError, match="bounds"):
            _StoredAmounts.model_validate({**stored_amounts, field: invalid})


def test_money_str_normalizes_negative_zero(stored_amounts: dict[str, str]) -> None:
    parsed = _StoredAmounts.model_validate({**stored_amounts, "amount": "-0"})
    assert parsed.amount.as_tuple() == Decimal("0").as_tuple()
    assert parsed.model_dump(mode="json")["amount"] == "0"


@pytest.mark.parametrize("value", ["1E+50000000", "1E-50000000", "10.005", "NaN"])
def test_money_serializer_bounds_unvalidated_decimals(value: str) -> None:
    constructed = _StoredAmounts.model_construct(amount=Decimal(value))
    with pytest.raises(PydanticSerializationError):
        constructed.model_dump(mode="json")


def test_storage_aliases_publish_exact_openapi_patterns() -> None:
    schema = json.loads(json.dumps(money_openapi_document()))
    fields = schema["components"]["schemas"]["_StoredAmounts"]["properties"]
    for name, pattern in {
        "amount": r"^-?(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,2})?$",
        "price": r"^-?(?:0|[1-9][0-9]{0,9})(?:\.[0-9]{1,8})?$",
        "quantity": r"^-?(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,10})?$",
    }.items():
        assert fields[name]["type"] == "string"
        assert fields[name]["pattern"] == pattern


if __name__ == "__main__":
    print(json.dumps(money_openapi_document()))
