"""RE2-only description patterns and their save-time cost probe (ADR 0024)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache
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
MAX_PROBE_FAMILIES = 160
# Counted repetition above this is refused, nested counts multiplied: bank descriptions never
# need more, and it bounds the DFA state growth the probe can only sample.
MAX_REPEAT_COUNT = 16
# RE2 program size ceiling (instructions). See PHASE-4-contract 2.2 for the measurements.
MAX_PROGRAM_SIZE = 2000
REGEX_BUDGET_SECONDS = 10
INGEST_REGEX_US_PER_ROW = 500

PATTERNS_UNAVAILABLE = "Patterns aren't available on this server."
PATTERN_INVALID = "This pattern uses unsupported syntax."
PATTERN_TOO_SLOW = "This pattern is too slow to run on every transaction."
PATTERN_TOO_COMPLEX = "This pattern is too complex to check. Try a simpler one."
_PRINTABLE_ASCII = "".join(chr(code) for code in range(32, 127))
_DESCRIPTION_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789    *#-./"
# Provide representatives for whitespace and common non-ASCII scripts and categories.
_CLASS_SAMPLES = _PRINTABLE_ASCII + "\t\n\r\f\v" + "ÉΩЖԱאشअก中あ한٠Ⅳ²€—"


class RuleValidationError(ValueError):
    """A rule cannot be evaluated as configured."""


class RegexBudgetExceeded(RuntimeError):
    def __init__(self, rule_id: UUID | None = None) -> None:
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
        if self.budget_ns is not None and self.elapsed_ns > self.budget_ns:
            # Fail closed: an unattributed overrun still stops the run.
            worst = (
                max(self.by_rule_ns, key=self.by_rule_ns.__getitem__) if self.by_rule_ns else None
            )
            raise RegexBudgetExceeded(worst)


class CompiledPattern(Protocol):
    def search(
        self, text: str, clock: RegexClock | None = None, *, rule_id: UUID | None = None
    ) -> bool: ...


class _EnginePattern(Protocol):
    def search(self, text: str) -> object | None: ...
    def fullmatch(self, text: str) -> object | None: ...
    @property
    def programsize(self) -> int: ...


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
    try:
        options = re2.Options()
        options.case_sensitive = False
        options.never_capture = True
        options.log_errors = False
        options.max_mem = 1 << 20
        # The pinned binding's public compile() shares an LRU of warmed DFA objects.
        # _Regexp is private: if a binding upgrade drops it, patterns are unavailable.
        compiled: _EnginePattern = re2._Regexp(pattern, options)
    except AttributeError:
        raise RuleValidationError(PATTERNS_UNAVAILABLE) from None
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


@dataclass(frozen=True, slots=True)
class _Scan:
    atoms: tuple[str, ...]
    literals: tuple[str, ...]
    runs: tuple[str, ...]
    # One entry per parenthesised group: its literal letters and the class atoms inside it,
    # nested groups included.
    groups: tuple[tuple[str, tuple[str, ...]], ...]
    # The largest repetition count, multiplied through nesting: (?:a{4}){8} is 32.
    max_repeat: int


def _fold_key(text: str) -> str:
    """A case-insensitive dedupe key that never relies on full case mapping (ß is not SS)."""
    lowered = text.lower()
    return lowered if len(lowered) == len(text) else text


def _repeat_count(pattern: str, start: int) -> tuple[int, int] | None:
    """Parse ``{n}``, ``{n,}`` or ``{n,m}`` at ``start``. Return (end, largest count)."""
    close = pattern.find("}", start + 1)
    if close == -1:
        return None
    count = pattern[start + 1 : close].split(",", 1)
    if not (count[0].isascii() and count[0].isdigit()):
        return None
    if len(count) == 2 and count[1] and not (count[1].isascii() and count[1].isdigit()):
        return None
    return close + 1, max(int(part) for part in count if part)


def _scan(pattern: str) -> _Scan:
    """Scan syntax after RE2 validates it. Track literal runs across quoted text."""
    atoms: dict[str, None] = {}
    groups: dict[tuple[str, tuple[str, ...]], None] = {}
    # Each open group: literal letters, class atoms, and the largest repeat weight inside.
    open_groups: list[tuple[dict[str, str], dict[str, None], list[int]]] = []
    top_weight = [1]
    last_weight = 1
    literals: dict[str, str] = {}
    runs: dict[str, str] = {}
    recent = ""
    i = 0
    while i < len(pattern):
        char = pattern[i]
        end = i + 1
        literal = ""
        atom = ""
        repeated = False
        closed_weight = 0
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
            if pattern[end - 1] != ")":
                open_groups.append(({}, {}, [1]))
        elif char == "(":
            open_groups.append(({}, {}, [1]))
        elif char == ")":
            if open_groups:
                letters, members, inner = open_groups.pop()
                groups[("".join(letters.values()), tuple(members))] = None
                closed_weight = inner[0]
                level = open_groups[-1][2] if open_groups else top_weight
                level[0] = max(level[0], closed_weight)
                for parent in open_groups:
                    parent[1].update(members)
                    for key, letter in letters.items():
                        parent[0][key] = letter
        elif char == "{" and (parsed := _repeat_count(pattern, i)) is not None:
            end, count = parsed
            repeated = True
            weight = count * last_weight
            level = open_groups[-1][2] if open_groups else top_weight
            level[0] = max(level[0], weight)
            last_weight = weight
        elif char not in "^$|()*+?":
            literal = char

        if not repeated:
            last_weight = max(closed_weight, 1)
        if atom:
            atoms[atom] = None
            for _, members, _ in open_groups:
                members[atom] = None
        if literal:
            for letter in literal:
                literals.setdefault(_fold_key(letter), letter)
                for letters, _, _ in open_groups:
                    letters.setdefault(_fold_key(letter), letter)
                recent = (recent + letter)[-3:]
                for size in range(2, len(recent) + 1):
                    runs.setdefault(_fold_key(recent[-size:]), recent[-size:])
        else:
            recent = ""
        i = end
    return _Scan(
        tuple(atoms),
        tuple(literals.values()),
        tuple(runs.values()),
        tuple(groups),
        top_weight[0],
    )


@lru_cache(maxsize=256)
def _atom_alphabet(atom: str) -> str:
    atom_pattern = _compile_cold(atom)
    # Negated classes use the same non-ASCII-aware sample set as the rest, so a blow-up
    # built from "not ASCII" is probed over the characters it actually matches.
    matched = (char for char in _CLASS_SAMPLES + atom if atom_pattern.fullmatch(char))
    return "".join({_fold_key(char): char for char in matched}.values())


@lru_cache(maxsize=256)
def _literal_alphabet(letter: str) -> str:
    """Every character the case-insensitive literal matches, by RE2's own simple folding."""
    engine = _compile_cold(f"\\x{{{ord(letter):x}}}")
    candidates = dict.fromkeys(_CLASS_SAMPLES + letter + letter.upper() + letter.lower())
    matched = (char for char in candidates if engine.fullmatch(char))
    return "".join({_fold_key(char): char for char in matched}.values())


def _atom_alphabets(atoms: tuple[str, ...]) -> Iterator[str]:
    for atom in atoms:
        yield _atom_alphabet(atom)


def _group_alphabets(groups: tuple[tuple[str, tuple[str, ...]], ...]) -> tuple[str, ...]:
    """A group draws on its literals (as RE2 folds them) and the samples of its classes."""
    found: dict[str, None] = {}
    for letters, members in groups:
        merged = "".join(_literal_alphabet(letter) for letter in letters)
        merged += "".join(_atom_alphabet(member) for member in members)
        alphabet = "".join(dict.fromkeys(merged))
        if len(alphabet) > 1:
            found[alphabet] = None
    return tuple(found)


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
    alphabets: tuple[str, ...],
    literals: tuple[str, ...],
    runs: tuple[str, ...],
    groups: tuple[str, ...],
) -> Iterator[tuple[str, ...]]:
    rng = Random(2404)  # noqa: S311 -- deterministic synthetic probe
    yield _subjects(rng, "ABC", 24)
    yield _subjects(rng, "AB", 8)
    yield _subjects(rng, _DESCRIPTION_ALPHABET, 31)
    yield ("A" * MAX_SUBJECT_LENGTH,)
    for members in groups:
        yield _subjects(rng, members, 32)
    # One bounded family over every class sample, group alphabet and literal together, so
    # classes that only interact through a mixed subject are still exercised.
    union = "".join(dict.fromkeys("".join(alphabets) + "".join(groups) + "".join(literals)))
    if len(union) > 1:
        yield _subjects(rng, union, 32)
    for alphabet in dict.fromkeys(alphabets):
        # An atom can have no representative in the bounded sample, for example
        # a negated class that excludes all printable ASCII.
        alphabet = alphabet or _DESCRIPTION_ALPHABET
        yield _subjects(rng, alphabet, 32)
        for literal in literals:
            yield _subjects(rng, alphabet, 8 if len(literals) > 32 else 32, literal)
        for run in runs:
            yield _subjects(rng, alphabet, 32, run)


def _family_mean_ns(pattern: str, subjects: tuple[str, ...]) -> int:
    # Integer nanoseconds: the best of three cold runs, averaged per subject.
    def one_run() -> int:
        search = _compile_cold(pattern).search
        started = perf_counter_ns()
        for subject in subjects:
            search(subject)
        return perf_counter_ns() - started

    return min(one_run() for _ in range(3)) // len(subjects)


def _compile_checked(pattern: str) -> _Pattern:
    if not 1 <= len(pattern) <= MAX_PATTERN_LENGTH or "\0" in pattern:
        raise RuleValidationError("Patterns must be 1 to 128 characters and contain no NUL.")
    engine = _compile_cold(pattern)
    if engine.search("") is not None:
        raise RuleValidationError("Patterns must not match an empty description.")
    return _Pattern(engine)


def compile_stored_pattern(pattern: str) -> CompiledPattern:
    """Compile a previously validated stored pattern without a load-dependent cost probe."""
    return _compile_checked(pattern)


def compile_pattern(pattern: str) -> CompiledPattern:
    """Validate and measure a new pattern before admitting it to the rules engine."""
    if re2 is None:
        raise RuleValidationError(PATTERNS_UNAVAILABLE)
    compiled = _compile_checked(pattern)
    # Structural limits run first and bound the whole class of blow-ups. The probe below
    # is a second line, not the only one.
    try:
        scanned = _scan(pattern)
    except (IndexError, ValueError):
        # The scanner only sees patterns RE2 accepted; anything it can't parse is refused.
        raise RuleValidationError(PATTERN_INVALID) from None
    if scanned.max_repeat > MAX_REPEAT_COUNT:
        raise RuleValidationError(PATTERN_TOO_COMPLEX)
    if compiled.engine.programsize > MAX_PROGRAM_SIZE:
        raise RuleValidationError(PATTERN_TOO_COMPLEX)
    if len(scanned.atoms) > MAX_CLASS_ATOMS:
        raise RuleValidationError("This pattern has too many character classes.")
    alphabets = tuple(_atom_alphabets(scanned.atoms))
    groups = _group_alphabets(scanned.groups)
    families = _probe_families(alphabets, scanned.literals, scanned.runs, groups)
    for count, subjects in enumerate(families, 1):
        if count > MAX_PROBE_FAMILIES:
            # A cap that is hit rejects. It never accepts what it could not finish probing.
            raise RuleValidationError(PATTERN_TOO_COMPLEX)
        if _family_mean_ns(pattern, subjects) > PROBE_LIMIT_MICROSECONDS * 1_000:
            raise RuleValidationError(PATTERN_TOO_SLOW)
    return compiled
