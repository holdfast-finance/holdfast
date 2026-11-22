"""Plaid setup checklist: each manual step's state from configuration and probe outcomes."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.domain.plaid_preflight import (
    PreflightFacts,
    Probe,
    Step,
    evaluate,
    redirect_uri_problem,
    requestable_products,
)

OK = Probe("ok")
SUGGESTED = "https://localhost/plaid/oauth-return"


def facts(**overrides: object) -> PreflightFacts:
    base = PreflightFacts(
        env="sandbox",
        has_client_id=True,
        has_secret=True,
        products=("transactions", "balance", "investments"),
        redirect_uri=None,
        suggested_redirect_uri=SUGGESTED,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def step(result: object, step_id: str) -> Step:
    return next(s for s in result.steps if s.id == step_id)  # type: ignore[attr-defined]


def test_nothing_configured_asks_for_the_client_id_first() -> None:
    result = evaluate(facts(has_client_id=False, has_secret=False))
    assert step(result, "keys_present") == Step("keys_present", "action", "client_id_missing")
    assert step(result, "keys_valid").state == "unchecked"
    assert not result.ready and not result.checked


@pytest.mark.parametrize("env", ["sandbox", "production"])
def test_missing_secret_names_the_environment_it_is_missing_for(env: str) -> None:
    result = evaluate(facts(env=env, has_secret=False))
    assert step(result, "keys_present").reason == f"secret_{env}_missing"
    assert not result.ready


def test_configured_but_not_yet_checked_is_not_ready() -> None:
    result = evaluate(facts())
    assert step(result, "keys_present").state == "done"
    assert step(result, "keys_valid").state == "unchecked"
    assert step(result, "products").state == "unchecked"
    assert not result.ready and not result.checked


def test_all_probes_pass_is_ready_and_balance_is_implicit() -> None:
    result = evaluate(facts(keys=OK, product_probes={"transactions": OK, "investments": OK}))
    assert result.ready and result.checked
    assert [(p.product, p.state) for p in result.products] == [
        ("transactions", "enabled"),
        ("balance", "implicit"),
        ("investments", "enabled"),
    ]
    assert step(result, "products") == Step("products", "done")
    assert step(result, "redirect_uri") == Step("redirect_uri", "optional", "not_set")
    assert step(result, "item_slots").reason == "sandbox_is_unlimited"


def test_rejected_keys_report_plaids_code_and_hide_downstream_answers() -> None:
    result = evaluate(
        facts(
            keys=Probe("rejected", "INVALID_API_KEYS", "req9"),
            redirect_uri="https://money.example.com/plaid/oauth-return",
        )
    )
    assert step(result, "keys_valid") == Step("keys_valid", "action", "INVALID_API_KEYS", "req9")
    assert step(result, "products").state == "unchecked"
    assert step(result, "redirect_uri").state == "unchecked"
    assert not result.ready and result.checked


def test_plaid_unreachable_is_blocked_not_a_key_problem() -> None:
    result = evaluate(facts(keys=Probe("unreachable", "timeout")))
    assert step(result, "keys_valid") == Step("keys_valid", "blocked", "plaid_unreachable")
    assert not result.ready


def test_a_product_missing_from_the_plan_degrades_and_stays_ready() -> None:
    result = evaluate(
        facts(
            env="production",
            keys=OK,
            product_probes={
                "transactions": OK,
                "investments": Probe("rejected", "INVALID_PRODUCT", "r2"),
            },
        )
    )
    assert result.ready
    assert step(result, "products") == Step("products", "done", "some_products_unavailable")
    assert [p.state for p in result.products] == ["enabled", "implicit", "not_enabled"]


def test_no_enabled_product_needs_action() -> None:
    refused = Probe("rejected", "INVALID_PRODUCT")
    result = evaluate(
        facts(keys=OK, product_probes={"transactions": refused, "investments": refused})
    )
    assert step(result, "products") == Step("products", "action", "no_products_enabled")
    assert not result.ready


def test_only_implicit_products_configured_needs_action() -> None:
    result = evaluate(facts(products=("balance",), keys=OK))
    assert step(result, "products").reason == "no_products_configured"
    assert not result.ready


def test_product_probe_errors_block_when_nothing_else_is_enabled() -> None:
    result = evaluate(
        facts(
            products=("transactions",),
            keys=OK,
            product_probes={"transactions": Probe("unreachable", "timeout")},
        )
    )
    assert step(result, "products") == Step("products", "blocked", "product_check_failed")


def test_registered_redirect_uri_is_done() -> None:
    result = evaluate(
        facts(
            keys=OK,
            product_probes={"transactions": OK, "investments": OK},
            redirect_uri="https://money.example.com/plaid/oauth-return",
            redirect=OK,
        )
    )
    assert step(result, "redirect_uri") == Step("redirect_uri", "done")
    assert result.ready


def test_unregistered_redirect_uri_says_so_and_blocks_ready() -> None:
    result = evaluate(
        facts(
            keys=OK,
            product_probes={"transactions": OK, "investments": OK},
            redirect_uri="https://money.example.com/plaid/oauth-return",
            redirect=Probe("rejected", "INVALID_FIELD", "r3"),
        )
    )
    assert step(result, "redirect_uri") == Step("redirect_uri", "action", "not_registered", "r3")
    assert not result.ready


@pytest.mark.parametrize(
    ("uri", "env", "problem"),
    [
        ("https://money.example.com/plaid/oauth-return", "production", None),
        ("http://localhost:3000/plaid/oauth-return", "sandbox", None),
        ("http://localhost/plaid/oauth-return", "production", "https_required"),
        ("http://money.example.com/plaid/oauth-return", "sandbox", "http_only_on_localhost"),
        ("https://money.example.com/return?x=1", "production", "has_query_or_fragment"),
        ("https://money.example.com/return#frag", "sandbox", "has_query_or_fragment"),
        ("money.example.com/return", "sandbox", "not_a_url"),
        ("ftp://money.example.com/return", "sandbox", "not_a_url"),
    ],
)
def test_redirect_uri_shape_rules(uri: str, env: str, problem: str | None) -> None:
    assert redirect_uri_problem(uri, env) == problem


def test_a_malformed_redirect_uri_is_reported_without_asking_plaid() -> None:
    result = evaluate(
        facts(
            env="production",
            keys=OK,
            product_probes={"transactions": OK, "investments": OK},
            redirect_uri="http://money.example.com/plaid/oauth-return",
        )
    )
    assert step(result, "redirect_uri") == Step("redirect_uri", "action", "https_required")


@pytest.mark.parametrize(
    ("used", "state", "reason", "ready"),
    [
        (0, "done", None, True),
        (7, "done", None, True),
        (8, "done", "few_slots_left", True),
        (9, "done", "few_slots_left", True),
        (10, "blocked", "no_slots_left", False),
        (11, "blocked", "no_slots_left", False),
    ],
)
def test_production_item_slots(used: int, state: str, reason: str | None, ready: bool) -> None:
    result = evaluate(
        facts(
            env="production",
            keys=OK,
            product_probes={"transactions": OK, "investments": OK},
            production_items_used=used,
        )
    )
    assert step(result, "item_slots") == Step("item_slots", state, reason)  # type: ignore[arg-type]
    assert result.ready is ready
    assert (result.item_slots_used, result.item_slots_total) == (used, 10)


def test_sandbox_never_reports_production_slot_use() -> None:
    result = evaluate(facts(production_items_used=10, keys=OK, product_probes={"transactions": OK}))
    assert result.item_slots_used == 0
    assert step(result, "item_slots").state == "optional"


def test_requestable_products_drop_balance_and_duplicates_in_order() -> None:
    assert requestable_products(("transactions", "balance", "auth", "transactions")) == (
        "transactions",
        "auth",
    )
