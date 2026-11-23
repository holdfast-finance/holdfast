"""C3c: scope, splits, ordering, preview_for, preview_token and the conditions codec."""

from __future__ import annotations

import itertools
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.rule_regex import compile_pattern
from app.domain.rules import (
    DRAFT_CREATED_AT,
    DRAFT_ID,
    DescriptionCondition,
    MerchantKeyVersionMismatch,
    Rule,
    RuleAction,
    RuleConditions,
    RuleTransaction,
    RuleValidationError,
    conditions_from_json,
    conditions_to_json,
    match,
    preview,
    preview_for,
    preview_token,
    rule_may_touch,
)

ALICE = UUID(int=101)
BOB = UUID(int=102)
CAT_A = UUID(int=201)
CAT_B = UUID(int=202)
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def rule(
    n: int, *, priority: int = 10, scope: str = "any", author: UUID = ALICE, **kw: object
) -> Rule:
    base: dict[str, object] = {
        "id": UUID(int=n),
        "priority": priority,
        "enabled": True,
        "conditions": RuleConditions(merchant_key="MOONFERN"),
        "action": RuleAction(CAT_A),
        "author_person_id": author,
        "created_at": T0,
        "account_scope": scope,
    }
    base.update(kw)
    return Rule(**base)  # type: ignore[arg-type]


def txn(n: int, owner: UUID | None = None, **kw: object) -> RuleTransaction:
    base: dict[str, object] = {
        "id": UUID(int=n),
        "description": "Moonfern",
        "amount": Decimal("-5.00"),
        "account_id": UUID(int=900),
        "posted_on": date(2026, 11, 1),
        "merchant_key": "MOONFERN",
        "account_owner_person_id": owner,
        "is_split": False,
    }
    base.update(kw)
    return RuleTransaction(**base)  # type: ignore[arg-type]


# --- scope ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scope", "owner", "expected"),
    [
        ("any", None, True),
        ("any", ALICE, True),
        ("any", BOB, False),
        ("joint", None, True),
        ("joint", ALICE, False),
        ("joint", BOB, False),
        ("own", None, False),
        ("own", ALICE, True),
        ("own", BOB, False),
    ],
)
def test_rule_may_touch_table(scope: str, owner: UUID | None, expected: bool) -> None:
    assert rule_may_touch(rule(1, scope=scope), txn(1, owner)) is expected


def test_match_and_preview_never_touch_rows_a_rule_may_not() -> None:
    r = rule(1, scope="own")
    assert match([r], txn(1, BOB)) is None
    assert match([r], txn(2, ALICE)) is not None
    assert [m.transaction_id for m in preview([r], [txn(1, BOB), txn(2, ALICE), txn(3, None)])] == [
        UUID(int=2)
    ]


def test_unwritable_rule_does_not_shadow_a_lower_one() -> None:
    bobs = rule(1, priority=1, author=BOB, scope="own", action=RuleAction(CAT_B))
    alices = rule(2, priority=5)
    assert match([bobs, alices], txn(1, ALICE)) == match([alices], txn(1, ALICE))


# --- splits and ordering ----------------------------------------------------


def test_preview_skips_split_rows() -> None:
    assert preview([rule(1)], [txn(1, is_split=True), txn(2)]) == [match([rule(1)], txn(2))]


def test_order_is_priority_then_created_at_then_id() -> None:
    early = rule(9, created_at=T0)
    late = rule(1, created_at=T0 + timedelta(days=1), action=RuleAction(CAT_B))
    for perm in itertools.permutations([early, late]):
        found = match(perm, txn(1))
        assert found is not None and found.rule_id == early.id
    same_time = rule(2, action=RuleAction(CAT_B))
    for perm in itertools.permutations([early, same_time]):
        found = match(perm, txn(1))
        assert found is not None and found.rule_id == same_time.id  # lower id wins a full tie


def test_naive_created_at_and_bad_scope_are_refused() -> None:
    with pytest.raises(RuleValidationError):
        rule(1, created_at=datetime(2026, 1, 1))
    with pytest.raises(RuleValidationError):
        rule(1, scope="everyone")


def test_plaid_to_rule_with_same_category_is_a_change() -> None:
    t = txn(1, category_id=CAT_A, category_source="plaid")
    assert len(preview([rule(1)], [t])) == 1


# --- preview_for ------------------------------------------------------------


def test_preview_for_counts_and_changes() -> None:
    target = rule(5)
    rows = [
        txn(1),
        txn(2, category_source="user", category_id=CAT_B),
        txn(3, is_split=True),
        txn(4, BOB),
        txn(5, description="other", merchant_key="OTHER"),
    ]
    result = preview_for(target, [], rows)
    assert [c.transaction_id for c in result.changes] == [UUID(int=1)]
    assert (result.skipped_user, result.skipped_split, result.skipped_not_writable) == (1, 1, 1)


def test_preview_for_earlier_rule_wins_later_does_not() -> None:
    target = rule(5, priority=10)
    earlier = rule(6, priority=1, action=RuleAction(CAT_B))
    later = rule(7, priority=20, action=RuleAction(CAT_B))
    assert preview_for(target, [earlier], [txn(1)]).changes == ()
    assert len(preview_for(target, [later], [txn(1)]).changes) == 1


def test_draft_sorts_last_among_equal_priorities() -> None:
    saved = rule(5)
    draft = rule(DRAFT_ID.int, created_at=DRAFT_CREATED_AT, action=RuleAction(CAT_B))
    assert preview_for(draft, [saved], [txn(1)]).changes == ()
    assert len(preview_for(saved, [draft], [txn(1)]).changes) == 1


def test_preview_for_matches_full_preview_for_the_target() -> None:
    rules = [
        rule(
            1,
            priority=1,
            action=RuleAction(CAT_B),
            conditions=RuleConditions(amount_min=Decimal("-3")),
        ),
        rule(2),
    ]
    rows = [txn(1), txn(2, amount=Decimal("-1.00"))]
    full = {m.transaction_id for m in preview(rules, rows) if m.rule_id == rules[1].id}
    assert {c.transaction_id for c in preview_for(rules[1], rules, rows).changes} == full


def test_disabled_target_changes_nothing() -> None:
    assert preview_for(rule(1, enabled=False), [], [txn(1)]).changes == ()


@given(st.permutations(range(1, 5)))
def test_preview_for_is_order_independent(order: list[int]) -> None:
    rows = [txn(i, BOB if i % 2 else None) for i in range(1, 5)]
    base = preview_for(rule(9), [], rows)
    assert preview_for(rule(9), [], [rows[i - 1] for i in order]) == base


# --- preview_token ----------------------------------------------------------


def test_draft_token_equals_saved_token() -> None:
    saved = rule(5)
    draft = replace(saved, id=DRAFT_ID, created_at=DRAFT_CREATED_AT)
    rows = [txn(1), txn(2)]
    assert preview_token(draft, preview_for(draft, [], rows).changes) == preview_token(
        saved, preview_for(saved, [], rows).changes
    )


def test_token_changes_with_content_and_changed_rows() -> None:
    r = rule(5)
    changes = preview_for(r, [], [txn(1), txn(2)]).changes
    base = preview_token(r, changes)
    assert len(base) == 64
    assert preview_token(r, changes[:1]) != base
    assert preview_token(r, tuple(reversed(changes))) == base
    for other in (
        replace(r, priority=11),
        replace(r, enabled=False),
        replace(r, account_scope="joint"),
        replace(r, action=RuleAction(CAT_B)),
        replace(r, conditions=RuleConditions(merchant_key="OTHER")),
    ):
        assert preview_token(other, changes) != base


# --- codec ------------------------------------------------------------------


FULL = RuleConditions(
    description=DescriptionCondition("contains", "moon"),
    merchant_key="MOONFERN",
    amount_equals=Decimal("-12.34"),
    amount_min=Decimal("-20"),
    amount_max=Decimal("0.01"),
    amount_sign="outflow",
    date_from=date(2026, 1, 1),
    date_through=date(2026, 12, 31),
)


def test_codec_round_trip_and_shape() -> None:
    obj = conditions_to_json(FULL)
    assert obj["v"] == 1 and obj["merchant_key_version"] == 1
    assert obj["amount_equals"] == "-12.34" and obj["date_from"] == "2026-01-01"
    assert obj["description"] == {"mode": "contains", "value": "moon"}
    assert conditions_from_json(json.loads(json.dumps(obj))) == FULL


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"v": 2, "amount_sign": "inflow"},
        {"v": True, "amount_sign": "inflow"},
        {"v": 1, "bogus": 1, "amount_sign": "inflow"},
        {"v": 1, "account_ids": ["x"], "amount_sign": "inflow"},
        {"v": 1, "amount_min": 5},
        {"v": 1, "amount_min": 5.5},
        {"v": 1, "amount_min": "1e3"},
        {"v": 1, "amount_min": "NaN"},
        {"v": 1, "amount_min": " 5"},
        {"v": 1, "amount_min": ""},
        {"v": 1, "date_from": "20260101"},
        {"v": 1, "date_from": "2026-1-1"},
        {"v": 1, "date_from": 20260101},
        {"v": 1, "amount_sign": "sideways"},
        {"v": 1, "merchant_key": "MOONFERN"},
        {"v": 1, "merchant_key_version": 1, "amount_sign": "inflow"},
        {"v": 1, "merchant_key": "MOONFERN", "merchant_key_version": "1"},
        {"v": 1, "merchant_key": "MOONFERN", "merchant_key_version": True},
        {"v": 1, "description": {"mode": "contains"}},
        {"v": 1, "description": {"mode": "contains", "value": "x", "extra": 1}},
        {"v": 1, "description": {"mode": "fuzzy", "value": "x"}},
        {"v": 1, "description": {"mode": "contains", "value": 5}},
        {"v": 1, "description": "moon"},
        {"v": 1, "amount_min": "5", "amount_max": "1"},
    ],
)
def test_codec_is_strict(bad: dict[str, object]) -> None:
    with pytest.raises(RuleValidationError):
        conditions_from_json(bad)


def test_merchant_key_version_mismatch_is_distinct() -> None:
    with pytest.raises(MerchantKeyVersionMismatch):
        conditions_from_json({"v": 1, "merchant_key": "MOONFERN", "merchant_key_version": 2})


# --- regex goes through RE2 only (C3d) --------------------------------------


@pytest.mark.parametrize("pattern", ["(", "", "x" * 500, "a*"])
def test_compile_pattern_refuses_invalid_empty_long_or_empty_matching(pattern: str) -> None:
    with pytest.raises(RuleValidationError):
        compile_pattern(pattern)


@pytest.mark.parametrize(
    ("pattern", "subject"), [("a", "A1"), ("^moonfern$", "MOONFERN"), (r"\d+", "store 42")]
)
def test_compile_pattern_accepts_plain_patterns(pattern: str, subject: str) -> None:
    assert compile_pattern(pattern).search(subject) is True


def test_regex_condition_and_codec_go_through_the_engine() -> None:
    assert DescriptionCondition("regex", "^moon").mode == "regex"
    conditions_from_json({"v": 1, "description": {"mode": "regex", "value": "^moon"}})
    with pytest.raises(RuleValidationError):
        DescriptionCondition("regex", "(")
    with pytest.raises(RuleValidationError):
        conditions_from_json({"v": 1, "description": {"mode": "regex", "value": "("}})


def test_description_value_never_appears_in_repr() -> None:
    secret = "zzq-private-note"
    cond = DescriptionCondition("contains", secret)
    assert secret not in repr(cond)
    assert secret not in repr(RuleConditions(description=cond))
    assert secret not in repr(rule(1, conditions=RuleConditions(description=cond)))
