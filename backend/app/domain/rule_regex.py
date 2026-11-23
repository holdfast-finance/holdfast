"""RE2-only description patterns and their save-time cost probe (ADR 0024)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from random import Random
from time import perf_counter_ns
from typing import Protocol
from uuid import UUID

try:
    import re2  # type: ignore[import-untyped]
except ImportError:
    re2 = None

MAX_PATTERN_LENGTH = 128
MAX_SUBJECT_LENGTH = 256
PROBE_LIMIT_MICROSECONDS = 10
MAX_CLASS_ATOMS = 16
REGEX_BUDGET_SECONDS = 10
INGEST_REGEX_US_PER_ROW = 500

PATTERNS_UNAVAILABLE = "Patterns aren't available on this server."
PATTERN_INVALID = "This pattern uses unsupported syntax."
PATTERN_TOO_SLOW = "This pattern is too slow to run on every transaction."
_PRINTABLE_ASCII = "".join(chr(code) for code in range(32, 127))
_DESCRIPTION_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789    *#-./"
# Provide representatives for whitespace and common non-ASCII scripts and categories.
_CLASS_SAMPLES = _PRINTABLE_ASCII + "\t\n\r\f\v" + "ÉΩЖԱאشअก中あ한٠Ⅳ²€—"


class RuleValidationError(ValueError):
    """A rule cannot be evaluated as configured."""


class RegexBudgetExceeded(RuntimeError):
    def __init__(self, rule_id: UUID) -> None:
        super().__init__("Pattern rules exceeded the time budget.")
        self.rule_id = rule_id


@dataclass(slots=True)
class RegexClock:
    """Per-run elapsed time, with an optional hard limit and per-rule attribution."""

    budget_ns: int | None = REGEX_BUDGET_SECONDS * 1_000_000_000
    elapsed_ns: int = field(default=0, init=False)
    by_rule_ns: dict[UUID, int] = field(default_factory=dict, init=False)

    def record(self, elapsed_ns: int, rule_id: UUID | None) -> None:
        self.elapsed_ns += elapsed_ns
        if rule_id is not None:
            self.by_rule_ns[rule_id] = self.by_rule_ns.get(rule_id, 0) + elapsed_ns
        if self.budget_ns is not None and self.elapsed_ns > self.budget_ns and self.by_rule_ns:
            raise RegexBudgetExceeded(max(self.by_rule_ns, key=self.by_rule_ns.__getitem__))


class CompiledPattern(Protocol):
    def search(
        self, text: str, clock: RegexClock | None = None, *, rule_id: UUID | None = None
    ) -> bool: ...


class _EnginePattern(Protocol):
    def search(self, text: str) -> object | None: ...
    def fullmatch(self, text: str) -> object | None: ...


@dataclass(frozen=True, slots=True, repr=False)
class _Pattern:
    engine: _EnginePattern

    def search(
        self, text: str, clock: RegexClock | None = None, *, rule_id: UUID | None = None
    ) -> bool:
        subject = text[:MAX_SUBJECT_LENGTH]
        if clock is None:
            return self.engine.search(subject) is not None
        started = perf_counter_ns()
        found = self.engine.search(subject) is not None
        clock.record(perf_counter_ns() - started, rule_id)
        return found


def _compile_cold(pattern: str) -> _EnginePattern:
    if re2 is None:
        raise RuleValidationError(PATTERNS_UNAVAILABLE)
    options = re2.Options()
    options.case_sensitive = False
    options.never_capture = True
    options.log_errors = False
    options.max_mem = 1 << 20
    try:
        # The pinned binding's public compile() shares an LRU of warmed DFA objects.
        compiled: _EnginePattern = re2._Regexp(pattern, options)
    except (re2.error, UnicodeError):
        raise RuleValidationError(PATTERN_INVALID) from None
    return compiled


def _escape_end(pattern: str, start: int) -> int:
    kind = pattern[start + 1]
    end = start + 2
    if kind in "pPx" and pattern[end : end + 1] == "{":
        return pattern.index("}", end) + 1
    if kind in "pP":
        return end + 1
    if kind == "x":
        return end + 2
    if kind in "01234567":
        while end < min(start + 4, len(pattern)) and pattern[end] in "01234567":
            end += 1
    return end


def _bracket_end(pattern: str, start: int) -> int:
    end = start + 1
    if pattern[end] == "^":
        end += 1
    if pattern[end] == "]":
        end += 1
    while pattern[end] != "]":
        if pattern[end] == "\\":
            end = _escape_end(pattern, end)
        elif (
            pattern.startswith("[:", end)
            and (close := pattern.find("]", end + 2)) != -1
            and pattern[close - 1] == ":"
        ):
            end = close + 1
        else:
            end += 1
    return end + 1


def _literal_escape(token: str) -> str:
    kind = token[1]
    controls = {"a": "\a", "f": "\f", "t": "\t", "n": "\n", "r": "\r", "v": "\v"}
    if kind == "x":
        return chr(int(token[2:].strip("{}"), 16))
    if kind in "01234567":
        return chr(int(token[1:], 8))
    return controls.get(kind, kind)


def _scan(pattern: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Scan syntax after RE2 validates it. Track literal runs across quoted text."""
    atoms: dict[str, None] = {}
    literals: dict[str, None] = {}
    runs: dict[str, None] = {}
    recent = ""
    i = 0
    while i < len(pattern):
        char = pattern[i]
        end = i + 1
        literal = ""
        atom = ""
        if char == "[":
            end = _bracket_end(pattern, i)
            atom = pattern[i:end]
        elif char == ".":
            atom = char
        elif char == "\\":
            kind = pattern[end]
            end = _escape_end(pattern, i)
            if kind in "dDwWsSpPC":
                atom = pattern[i:end]
            elif kind == "Q":
                close = pattern.find(r"\E", end)
                literal = pattern[end:] if close == -1 else pattern[end:close]
                end = len(pattern) if close == -1 else close + 2
            elif kind not in "bBAz":
                literal = _literal_escape(pattern[i:end])
        elif pattern.startswith("(?", i):
            end = i + 2
            while pattern[end] not in ":>)":
                end += 1
            end += 1
        elif char == "{" and (close := pattern.find("}", end)) != -1:
            count = pattern[end:close].split(",", 1)
            if (
                count[0].isascii()
                and count[0].isdigit()
                and (len(count) == 1 or not count[1] or (count[1].isascii() and count[1].isdigit()))
            ):
                end = close + 1
            else:
                literal = char
        elif char not in "^$|()*+?":
            literal = char

        if atom:
            atoms[atom] = None
        if literal:
            for letter in literal.upper():
                literals[letter] = None
                recent = (recent + letter)[-3:]
                for size in range(2, len(recent) + 1):
                    runs[recent[-size:]] = None
        else:
            recent = ""
        i = end
    return tuple(atoms), tuple(literals), tuple(runs)


def _atom_alphabets(atoms: tuple[str, ...]) -> Iterator[str]:
    for atom in atoms:
        atom_pattern = _compile_cold(atom)
        samples = (
            _PRINTABLE_ASCII
            if atom.startswith(("[^", r"\P")) or atom in (r"\D", r"\W", r"\S")
            else _CLASS_SAMPLES + atom
        )
        yield "".join(
            dict.fromkeys(char.upper() for char in samples if atom_pattern.fullmatch(char))
        )


def _subjects(rng: Random, alphabet: str, count: int, trigger: str = "") -> tuple[str, ...]:
    if not trigger:
        return tuple("".join(rng.choices(alphabet, k=MAX_SUBJECT_LENGTH)) for _ in range(count))
    return tuple(
        "".join(
            trigger if rng.getrandbits(1) else rng.choice(alphabet)
            for _ in range(MAX_SUBJECT_LENGTH)
        )[:MAX_SUBJECT_LENGTH]
        for _ in range(count)
    )


def _probe_families(
    alphabets: tuple[str, ...], literals: tuple[str, ...], runs: tuple[str, ...]
) -> Iterator[tuple[str, ...]]:
    rng = Random(2404)  # noqa: S311 -- deterministic synthetic probe
    yield _subjects(rng, "ABC", 24)
    yield _subjects(rng, "AB", 8)
    yield _subjects(rng, _DESCRIPTION_ALPHABET, 31)
    yield ("A" * MAX_SUBJECT_LENGTH,)
    for alphabet in alphabets:
        # An atom can have no representative in the bounded sample, for example
        # a negated class that excludes all printable ASCII.
        alphabet = alphabet or _DESCRIPTION_ALPHABET
        yield _subjects(rng, alphabet, 32)
        for literal in literals:
            yield _subjects(rng, alphabet, 8 if len(literals) > 32 else 32, literal)
        for run in runs:
            yield _subjects(rng, alphabet, 32, run)


def _family_mean_ns(pattern: str, subjects: tuple[str, ...]) -> float:
    best = float("inf")
    for _ in range(3):
        search = _compile_cold(pattern).search
        started = perf_counter_ns()
        for subject in subjects:
            search(subject)
        best = min(best, perf_counter_ns() - started)
    return best / len(subjects)


def compile_stored_pattern(pattern: str) -> CompiledPattern:
    """Compile a previously validated stored pattern without a load-dependent cost probe."""
    if not 1 <= len(pattern) <= MAX_PATTERN_LENGTH or "\0" in pattern:
        raise RuleValidationError("Patterns must be 1 to 128 characters and contain no NUL.")
    engine = _compile_cold(pattern)
    if engine.search("") is not None:
        raise RuleValidationError("Patterns must not match an empty description.")
    return _Pattern(engine)


def compile_pattern(pattern: str) -> CompiledPattern:
    """Validate and measure a new pattern before admitting it to the rules engine."""
    if re2 is None:
        raise RuleValidationError(PATTERNS_UNAVAILABLE)
    compiled = compile_stored_pattern(pattern)
    try:
        atoms, literals, runs = _scan(pattern)
    except (IndexError, ValueError):
        # The scanner only sees patterns RE2 accepted; anything it can't parse is refused.
        raise RuleValidationError(PATTERN_INVALID) from None
    if len(atoms) > MAX_CLASS_ATOMS:
        raise RuleValidationError("This pattern has too many character classes.")
    alphabets = tuple(_atom_alphabets(atoms))
    for subjects in _probe_families(alphabets, literals, runs):
        if _family_mean_ns(pattern, subjects) > PROBE_LIMIT_MICROSECONDS * 1_000:
            raise RuleValidationError(PATTERN_TOO_SLOW)
    return compiled
