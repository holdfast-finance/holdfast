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
from tests.domain.regex_corpus import (
    BUDGET_ONLY,
    CORPUS,
    MUST_ACCEPT,
    PROBE_ONLY_VARIANTS,
    REQUIRED_REJECTIONS,
)


@pytest.mark.parametrize("pattern", REQUIRED_REJECTIONS)
def test_required_corpus_patterns_are_refused(pattern: str) -> None:
    with pytest.raises(RuleValidationError) as caught:
        compile_pattern(pattern)
    assert str(caught.value) in (
        PATTERN_TOO_SLOW,
        rule_regex.PATTERN_TOO_COMPLEX,
        rule_regex.PATTERN_UNSUPPORTED,
    )


@pytest.mark.parametrize(
    "pattern", [p for p in (*REQUIRED_REJECTIONS, *PROBE_ONLY_VARIANTS) if p not in BUDGET_ONLY]
)
def test_probe_alone_still_refuses_the_required_patterns(
    pattern: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The structural limits are not the only defence: with them off, the probe refuses."""
    monkeypatch.setattr(rule_regex, "MAX_REPEAT_BUDGET", 10_000)
    monkeypatch.setattr(rule_regex, "MAX_PROGRAM_SIZE", 10**9)
    monkeypatch.setattr(rule_regex, "MAX_CLASS_POSITIONS", 10**9)
    with pytest.raises(RuleValidationError) as caught:
        compile_pattern(pattern)
    assert str(caught.value) in (
        PATTERN_TOO_SLOW,
        rule_regex.PATTERN_TOO_COMPLEX,
        rule_regex.PATTERN_UNSUPPORTED,
    )


@pytest.mark.parametrize(
    ("pattern", "allowed"),
    (
        (r"\d{8}", True),
        (r"\d{9}", False),
        (r"\d{2,9}", False),
        (r"\d{9,}", False),
        ("[W]{8}[W]{8}", False),
        ("[W]{4}[W]{4}", True),
        ("[W]{4}[W]{5}", False),
        (r"\d{4}|\d{8}", True),
        (r"\d{4}|\d{9}", False),
        (r"(?:\d{4}){2}", True),
        (r"(?:\d{4}){3}", False),
        (r"(?:(?:\d{2}){2}){2}", True),
        (r"(?:\d{2}){2}(?:\w{2}){3}", False),
        (r"(?:\d{3}|\w{2}){3}", False),
        (r"(?:\d{3}|\w{2}){2}\d{2}", True),
        (r"(?:\d{9})", False),
        (r"\d{3}-\d{4}", True),
        (r"A\{99\}", True),
        # Counts on literals do not branch the DFA, so they are free.
        ("A{9}", True),
        ("(?:AB){40}", True),
        (r"\*{8}\d{4}", True),
        (r"X{8}\d{4}", True),
    ),
)
def test_counted_repetition_is_capped_and_nested_counts_multiply(
    pattern: str, allowed: bool
) -> None:
    if allowed:
        assert compile_pattern(pattern)
        return
    with pytest.raises(RuleValidationError) as caught:
        compile_pattern(pattern)
    assert str(caught.value) == rule_regex.PATTERN_TOO_COMPLEX


def test_program_size_ceiling_rejects_oversized_programs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rule_regex, "MAX_PROGRAM_SIZE", 5)
    with pytest.raises(RuleValidationError) as caught:
        compile_pattern("AMZN MKTP|AMAZON")
    assert str(caught.value) == rule_regex.PATTERN_TOO_COMPLEX


def test_benign_corpus_stays_well_under_the_program_size_ceiling() -> None:
    benign = [p for p in CORPUS if p not in REQUIRED_REJECTIONS]
    sizes = [rule_regex._compile_cold(p).programsize for p in benign]
    assert max(sizes) * 8 <= rule_regex.MAX_PROGRAM_SIZE


def test_literal_case_folding_is_simple_not_full() -> None:
    assert "ẞ" in rule_regex._literal_alphabet("ß") or "ß" in rule_regex._literal_alphabet("ß")
    assert "SS" not in rule_regex._literal_alphabet("ß")
    scanned = rule_regex._scan("ß")
    assert scanned.literals == ("ß",)


def test_group_alphabet_includes_the_samples_of_classes_inside_it() -> None:
    scanned = rule_regex._scan("(?:[x]|[y])*[x](?:[x]|[y]|[z]){3}[w]")
    alphabets = {a.upper() for a in rule_regex._group_alphabets(scanned.groups)}
    assert any(set("XY") <= set(a) for a in alphabets)


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

    scanned = rule_regex._scan(pattern)
    literals, runs = scanned.literals, scanned.runs
    alphabets = tuple(rule_regex._atom_alphabets(scanned.atoms))
    groups = rule_regex._group_alphabets(scanned.groups)
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


def test_posix_and_nested_brackets_are_unsupported() -> None:
    for pattern in (
        "[[:a]",
        "[[:alpha:]]",
        "[[]",
        "[a[b]",
        "(?:[[:]|X)",
        "A{,99}",
        r"\QAB\E",
        "(?P<brand>AB|CD)E",
        "(?#note)A",
        "A]",
        "A}",
        "[]A]",
        "[a-b-c]",
        "^*A",
        "A**",
        r"\n",
    ):
        with pytest.raises(RuleValidationError) as caught:
            compile_pattern(pattern)
        # RE2 itself refuses a few of these first, which is as good.
        assert str(caught.value) in (rule_regex.PATTERN_UNSUPPORTED, rule_regex.PATTERN_INVALID)


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
        compile_pattern("PAY[A-Z]MENT")
    assert str(caught.value) == rule_regex.PATTERN_TOO_COMPLEX


def test_group_alphabets_are_probed_and_negated_classes_use_non_ascii_samples() -> None:
    scanned = rule_regex._scan("(?:X|Y)*X(?:X|Y|Z){20}W")
    assert scanned.atoms == ()
    assert {
        "".join(sorted(set(a.upper()))) for a in rule_regex._group_alphabets(scanned.groups)
    } == {
        "XY",
        "XYZ",
    }
    (alphabet,) = rule_regex._atom_alphabets(("[^ -~]",))
    assert "É" in alphabet
    assert "A" not in alphabet


def test_open_ended_count_costs_its_lower_bound() -> None:
    assert rule_regex._scan(r"\d{8,}").repeat_cost == 8
    assert rule_regex._scan(r"\d{3,9}").repeat_cost == 9


@pytest.mark.parametrize(
    "pattern", (r"[\x{10D0}-\x{10EF}]+#", r"\x{105}[\x{105}\x{142}]", r"[\x{10D0}-\x{10EF}]{3}")
)
def test_classes_written_as_escapes_are_sampled(pattern: str) -> None:
    scanned = rule_regex._scan(pattern)
    for alphabet in rule_regex._atom_alphabets(scanned.atoms):
        assert alphabet
    assert compile_pattern(pattern)


def test_class_with_no_probe_sample_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    # Fail closed: no fallback alphabet stands in for a class the probe cannot sample.
    monkeypatch.setattr(rule_regex, "_CLASS_SAMPLES", "")
    rule_regex._atom_alphabet.cache_clear()
    try:
        with pytest.raises(RuleValidationError) as caught:
            rule_regex._atom_alphabet(r"\p{Georgian}")
        assert str(caught.value) == rule_regex.PATTERN_TOO_COMPLEX
    finally:
        rule_regex._atom_alphabet.cache_clear()


def test_range_ends_and_midpoint_are_decoded() -> None:
    assert set(rule_regex._class_endpoints(r"[\x{10D0}-\x{10EF}]")) >= {
        "\u10d0",
        "\u10ef",
        "\u10df",
    }
    assert set(rule_regex._class_endpoints(r"[\x41-\x43x]")) >= {"A", "C", "B", "x"}


@pytest.mark.parametrize("pattern", MUST_ACCEPT)
def test_masked_account_and_literal_only_patterns_are_accepted(pattern: str) -> None:
    assert compile_pattern(pattern)


def test_pure_literal_patterns_skip_the_timing_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_probe(*_: object) -> int:
        pytest.fail("a literal-only pattern was timed")

    monkeypatch.setattr(rule_regex, "_family_mean_ns", no_probe)
    assert compile_pattern("A" * 128).search("a" * 128)
    assert rule_regex._scan("A" * 128).pure_literal
    assert not rule_regex._scan("AB|CD").pure_literal
    assert not rule_regex._scan("A*B").pure_literal
    assert not rule_regex._scan("A.B").pure_literal
    assert not rule_regex._scan("A{3}").pure_literal


@pytest.mark.parametrize(
    ("pattern", "positions"),
    (
        (".", 1),
        ("ABC", 0),
        (r"\d{4}-\d{4}", 8),
        (".*X.{3}", 4),
        ("(?:AB|CD)", 1),
        ("(?:AB|CD){5}", 5),
        ("(?:AB){5}", 0),
        ("(?:\\d|\\w){2}[A-Z]", 3),
        ("A|BC.", 1),
    ),
)
def test_class_positions_count_dots_classes_and_branching_groups(
    pattern: str, positions: int
) -> None:
    assert rule_regex._scan(pattern).class_positions == positions


def test_written_out_copies_hit_the_class_position_budget() -> None:
    with pytest.raises(RuleValidationError) as caught:
        compile_pattern("QJV.*X" + "." * 24 + "W")
    assert str(caught.value) == rule_regex.PATTERN_TOO_COMPLEX
    assert compile_pattern("QJV.*X" + "." * 4 + "W")


@pytest.mark.parametrize(
    "pattern",
    ("(?i)A", "(?-i)A", "(?s)A.B", "(?m)^A", "(?U)A+", "(?i:A)", "(?-i:A)B", "(?i-s:A)", "A(?i)B"),
)
def test_inline_flags_are_refused_on_every_path(pattern: str) -> None:
    for compile_ in (compile_pattern, rule_regex.compile_stored_pattern):
        with pytest.raises(RuleValidationError) as caught:
            compile_(pattern)
        assert str(caught.value) == rule_regex.PATTERN_UNSUPPORTED


@pytest.mark.parametrize(
    "pattern", (r"\(?i\)A", r"[(?i)]A", "(?:AB|CD)E", "(AB|CD)E", r"\[:alpha:\]A")
)
def test_non_flag_groups_and_escaped_text_are_accepted(pattern: str) -> None:
    assert compile_pattern(pattern)


def test_compatibility_twins_are_normalised_to_one_character() -> None:
    from app.domain.rules import normalize_for_rules

    assert normalize_for_rules("\u212a \u2126 \u212b") == "K \u03a9 \u00c5"
    assert compile_pattern("\u2126\u2126").search(normalize_for_rules("\u03a9\u2126"))


def test_probe_alphabets_keep_exact_code_points() -> None:
    assert {"\u03a9", "\u2126"} <= set(rule_regex._literal_alphabet("\u03a9"))
    assert {"\u03a9", "\u2126"} <= set(rule_regex._atom_alphabet(r"[\x{3a9}\x{2126}]"))


_DIFFERENTIAL_PIECES = (
    "A",
    "B",
    "x",
    "-",
    ":",
    "#",
    "/",
    " ",
    "[",
    "]",
    "[^",
    "[:",
    ":]",
    "^",
    "$",
    "\\",
    "|",
    "(",
    ")",
    "(?:",
    "(?",
    "?",
    "*",
    "+",
    ".",
    "{",
    "}",
    "{2}",
    "{1,3}",
    "{3,}",
    "{,2}",
    "{0}",
    r"\d",
    r"\w",
    r"\S",
    r"\b",
    r"\x41",
    r"\x{10D0}",
    r"\x{110000}",
    r"\p{Greek}",
    r"\pL",
    r"\p{Nope}",
    r"\Q",
    r"\E",
    r"\-",
    r"\]",
    r"\[",
    r"\\",
    "(?i)",
    "(?-i:",
    "(?P<n>",
    "\u03a9",
    "\u2126",
    "a-z",
    "z-a",
    "[a-z]",
    "[^a]",
    "[\\]]",
    "[-a]",
    "[a-]",
)


def test_scanner_and_re2_agree_on_random_allowlist_patterns(
    record_property: Callable[[str, object], None],
) -> None:
    """Whatever the strict parser accepts, RE2 compiles, and a bracket atom is one character."""
    rng = Random(20260124)  # noqa: S311 -- deterministic synthetic generator
    cases = accepted = brackets = 0
    while cases < 6000:
        cases += 1
        pattern = "".join(rng.choices(_DIFFERENTIAL_PIECES, k=rng.randint(1, 14)))[:128]
        try:
            scanned = rule_regex._scan(pattern)
        except (RuleValidationError, IndexError):
            continue
        accepted += 1
        # The scanner accepted it, so RE2 must too: no syntax is hidden from the controls.
        engine = rule_regex._compile_cold(pattern)
        assert engine is not None, pattern
        for atom in scanned.atoms:
            if not atom.startswith("["):
                continue
            brackets += 1
            atom_engine = rule_regex._compile_cold(atom)
            try:
                samples = rule_regex._atom_alphabet(atom)[:12]
            except RuleValidationError:
                continue
            for first in samples:
                assert atom_engine.fullmatch(first) is not None
                for second in samples:
                    assert atom_engine.fullmatch(first + second) is None, (atom, first, second)
    record_property("differential_cases", cases)
    record_property("differential_accepted", accepted)
    record_property("differential_bracket_atoms", brackets)
    assert accepted >= 300
    assert brackets >= 100
