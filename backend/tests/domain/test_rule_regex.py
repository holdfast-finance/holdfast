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

    atoms, literals, runs, groups = rule_regex._scan(pattern)
    alphabets = tuple(rule_regex._atom_alphabets(atoms))
    union = "".join(dict.fromkeys("".join(alphabets))) or "ABCXYZ0129 "
    rng = Random(1729)  # noqa: S311 -- deterministic synthetic fixture
    subjects = tuple("".join(rng.choices(union, k=1024)) for _ in range(128))
    # Independent 1,024-character subjects plus longer versions of the probe families.
    subjects += tuple(
        prefix + "".join(rng.choices(union, k=1024 - len(prefix)))
        for family in rule_regex._probe_families(alphabets, literals, runs, groups)
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


def test_budget_overrun_without_a_rule_id_fails_closed() -> None:
    clock = RegexClock(budget_ns=10)
    with pytest.raises(RegexBudgetExceeded) as caught:
        clock.record(11, None)
    assert caught.value.rule_id is None


def test_budget_overrun_names_the_costliest_rule_even_when_last_call_is_unattributed() -> None:
    clock = RegexClock(budget_ns=10)
    clock.record(6, UUID(int=3))
    with pytest.raises(RegexBudgetExceeded) as caught:
        clock.record(6, None)
    assert caught.value.rule_id == UUID(int=3)


def test_missing_private_engine_class_is_patterns_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Bare:
        error = rule_regex.re2.error
        Options = rule_regex.re2.Options  # no _Regexp, as after a binding upgrade

    monkeypatch.setattr(rule_regex, "re2", Bare)
    for compile_ in (compile_pattern, rule_regex.compile_stored_pattern):
        with pytest.raises(RuleValidationError) as caught:
            compile_("PAYMENT")
        assert str(caught.value) == rule_regex.PATTERNS_UNAVAILABLE


# Patterns that run the most probe families while staying inside the family cap, and ones
# that hit the cap. Save time is bounded either way, and a cap hit rejects.
_WORST_CASES = (
    r"\d\D\w\W\s\S.[0][1][2][3][4][5][6][7][8]|"
    + "ABCDEFGHIJKLMNOPQRSTUVWXYZ9!%&',/:;<=>@_~`"[:60],
    "(?:0123456789ABCDEFGHIJKLMNOPRSTUVW)?\\w*Q\\w{20}#",
    "".join(f"[{c}]" for c in "ABCDEFGHIJKLMNOP") + "QRSTUVWXYZ" * 5,
    "(" * 30 + "A|B" + ")" * 30 + "[AB]*",
)


@pytest.mark.parametrize("pattern", _WORST_CASES)
def test_save_time_is_bounded_and_a_cap_hit_rejects(pattern: str) -> None:
    assert len(pattern) <= rule_regex.MAX_PATTERN_LENGTH
    started = perf_counter()
    try:
        compile_pattern(pattern)
    except RuleValidationError as error:
        assert str(error) in (
            rule_regex.PATTERN_TOO_COMPLEX,
            rule_regex.PATTERN_TOO_SLOW,
            "This pattern has too many character classes.",
        )
    assert perf_counter() - started < 1.5


def test_probe_family_cap_rejects_and_never_accepts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rule_regex, "MAX_PROBE_FAMILIES", 3)
    with pytest.raises(RuleValidationError) as caught:
        compile_pattern("PAYMENT")
    assert str(caught.value) == rule_regex.PATTERN_TOO_COMPLEX


def test_group_alphabets_are_probed_and_negated_classes_use_non_ascii_samples() -> None:
    atoms, _, _, groups = rule_regex._scan("(?:X|Y)*X(?:X|Y|Z){20}W")
    assert atoms == ()
    assert set(groups) == {"XY", "XYZ"}
    (alphabet,) = rule_regex._atom_alphabets(("[^ -~]",))
    assert "É" in alphabet
    assert "A" not in alphabet
