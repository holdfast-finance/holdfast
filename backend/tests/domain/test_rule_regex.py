from __future__ import annotations

import os
from collections.abc import Callable
from itertools import cycle, islice
from random import Random
from time import perf_counter
from uuid import UUID

import pytest

from app.domain import rule_regex
from app.domain.rule_regex import (
    PATTERN_TOO_SLOW,
    RegexBudgetExceeded,
    RegexClock,
    RuleValidationError,
    compile_pattern,
)
from tests.domain.regex_corpus import CORPUS, REQUIRED_REJECTIONS


@pytest.mark.parametrize("pattern", REQUIRED_REJECTIONS)
def test_required_corpus_patterns_are_refused(pattern: str) -> None:
    with pytest.raises(RuleValidationError) as caught:
        compile_pattern(pattern)
    assert str(caught.value) == PATTERN_TOO_SLOW


@pytest.mark.parametrize("pattern", CORPUS)
@pytest.mark.skipif(
    bool(os.environ.get("SKIP_REGEX_TIMING")) and not os.environ.get("CI"),
    reason="SKIP_REGEX_TIMING set locally",
)
def test_corpus_gate(pattern: str, record_property: Callable[[str, object], None]) -> None:
    try:
        compiled = compile_pattern(pattern)
    except RuleValidationError:
        return

    atoms, literals, runs = rule_regex._scan(pattern)
    alphabets = tuple(rule_regex._atom_alphabets(atoms))
    union = "".join(dict.fromkeys("".join(alphabets))) or "ABCXYZ0129 "
    rng = Random(1729)  # noqa: S311 -- deterministic synthetic fixture
    subjects = tuple("".join(rng.choices(union, k=1024)) for _ in range(128))
    # Independent 1,024-character subjects plus longer versions of the probe families.
    subjects += tuple(
        prefix + "".join(rng.choices(union, k=1024 - len(prefix)))
        for family in rule_regex._probe_families(alphabets, literals, runs)
        for prefix in family
    )
    started = perf_counter()
    for subject in islice(cycle(subjects), 50_000):
        compiled.search(subject)
    elapsed = perf_counter() - started
    record_property("search_seconds", elapsed)
    assert elapsed < 1


def test_case_insensitive_and_truncates_by_character() -> None:
    compiled = compile_pattern(r"Q\.$")
    assert compiled.search("é" * 254 + "q." + " suffix")
    assert not compiled.search("é" * 255 + "q.")


def test_class_with_literal_bracket_and_colon_is_not_a_posix_class() -> None:
    compiled = compile_pattern("[[:a]")
    assert compiled.search(":")
    assert compiled.search("A")


def test_stored_patterns_are_not_probed_again(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_clock_read() -> int:
        pytest.fail("stored pattern triggered the save-time probe")

    monkeypatch.setattr(rule_regex, "perf_counter_ns", unexpected_clock_read)
    assert rule_regex.compile_stored_pattern("^PAYMENT").search("payment")


@pytest.mark.parametrize("pattern", ("", "X" * 129, "PRIVATE\0TEXT", "PRIVATE(", "PRIVATE*|"))
def test_error_does_not_echo_input(pattern: str, capfd: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(RuleValidationError) as caught:
        compile_pattern(pattern)
    assert "PRIVATE" not in str(caught.value)
    assert capfd.readouterr().err == ""


def test_more_than_sixteen_distinct_class_atoms_are_refused() -> None:
    with pytest.raises(RuleValidationError, match="too many character classes"):
        compile_pattern("".join(f"[{letter}]" for letter in "ABCDEFGHIJKLMNOPQ"))


def test_missing_engine_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rule_regex, "re2", None)
    with pytest.raises(RuleValidationError) as caught:
        compile_pattern("PAYMENT")
    assert str(caught.value) == "Patterns aren't available on this server."


def test_search_budget_attributes_cost_to_the_costliest_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiled = compile_pattern("PAYMENT")
    timestamps = iter((0, 7, 10, 14))
    monkeypatch.setattr(rule_regex, "perf_counter_ns", lambda: next(timestamps))
    clock = RegexClock(budget_ns=10)
    expensive = UUID(int=1)
    assert compiled.search("PAYMENT", clock, rule_id=expensive)
    with pytest.raises(RegexBudgetExceeded) as caught:
        compiled.search("REFUND", clock, rule_id=UUID(int=2))
    assert caught.value.rule_id == expensive
    assert clock.elapsed_ns == 11
