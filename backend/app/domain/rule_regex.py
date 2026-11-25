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
# Total counted repetition above this is refused (sum along a sequence, max across |, nested
# counts multiplied). DFA state growth is 2^n in the count, so 8 bounds what the probe can
# only sample, and bank descriptions never need more.
MAX_REPEAT_BUDGET = 8
# Class positions (every ., class or branching group, times its count) per pattern. Written-out
# copies cost the same as a count, so this closes what the repetition budget cannot see.
MAX_CLASS_POSITIONS = 24
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
    # Total counted-repetition cost: counts add along a sequence, the larger alternative
    # counts across ``|``, nesting multiplies ((?:a{4}){8} is 32) and ``{n,}`` counts n.
    repeat_cost: int
    # Class positions: every ``.``, class or branching group in sequence counts 1, times its
    # ``{n}`` through the same calculator. Literals are free.
    class_positions: int
    # No class, dot, alternation or repetition anywhere: the DFA has at most len + 1 states.
    pure_literal: bool


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
    # Each open group: literal letters and class atoms inside it.
    open_groups: list[tuple[dict[str, str], dict[str, None]]] = []
    # Cost per nesting level: [repeat: finished max, current; positions: finished max, current].
    levels: list[list[int]] = [[0, 0, 0, 0]]
    has_alternation = [False]
    last_rep = last_pos = 0
    pure_literal = True
    literals: dict[str, str] = {}
    runs: dict[str, str] = {}
    recent = ""
    i = 0
    while i < len(pattern):
        char = pattern[i]
        end = i + 1
        literal = ""
        atom = ""
        closed_rep = closed_pos = 0
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
                open_groups.append(({}, {}))
                levels.append([0, 0, 0, 0])
                has_alternation.append(False)
        elif char == "(":
            open_groups.append(({}, {}))
            levels.append([0, 0, 0, 0])
            has_alternation.append(False)
        elif char == "|":
            pure_literal = False
            top = levels[-1]
            levels[-1] = [max(top[:2]), 0, max(top[2:]), 0]
            has_alternation[-1] = True
        elif char == ")":
            if open_groups:
                letters, members = open_groups.pop()
                groups[("".join(letters.values()), tuple(members))] = None
                done = levels.pop()
                closed_rep, closed_pos = max(done[:2]), max(done[2:])
                if has_alternation.pop():
                    closed_pos = max(closed_pos, 1)
                levels[-1][1] += closed_rep
                levels[-1][3] += closed_pos
                for parent in open_groups:
                    parent[1].update(members)
                    for key, letter in letters.items():
                        parent[0][key] = letter
        elif char == "{" and (parsed := _repeat_count(pattern, i)) is not None:
            end, count = parsed
            pure_literal = False
            if last_pos:
                # A count on a single literal or a pure-literal group does not branch the DFA.
                closed_rep = count * max(last_rep, 1)
                closed_pos = count * last_pos
                levels[-1][1] += closed_rep - last_rep
                levels[-1][3] += closed_pos - last_pos
        elif char not in "^$|()*+?":
            literal = char
        if char in "*+?" and not pattern.startswith("(?", i):
            pure_literal = False

        last_rep, last_pos = closed_rep, closed_pos
        if atom:
            pure_literal = False
            levels[-1][3] += 1
            last_pos = 1
            atoms[atom] = None
            for _, members in open_groups:
                members[atom] = None
        if literal:
            for letter in literal:
                literals.setdefault(_fold_key(letter), letter)
                for letters, _ in open_groups:
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
        max(levels[0][:2]),
        max(levels[0][2:]),
        pure_literal,
    )


def _class_endpoints(atom: str) -> str:
    """Characters a bracket class names by escape or range: both ends and each midpoint.

    ``[\\x{10D0}-\\x{10EF}]`` names no printable character, so without this it is never sampled.
    """
    if not atom.startswith("["):
        return ""
    body_start = 1 + (atom[1:2] == "^")
    tokens: list[str | None] = []
    i = body_start
    stop = len(atom) - 1
    while i < stop:
        if atom[i] == "\\":
            end = _escape_end(atom, i)
            tokens.append(None if atom[i + 1] in "dDwWsSpPC" else _literal_escape(atom[i:end]))
            i = end
        elif atom.startswith("[:", i) and (close := atom.find(":]", i + 2)) != -1:
            tokens.append(None)
            i = close + 2
        else:
            tokens.append(atom[i])
            i += 1
    found: dict[str, None] = {}
    for position, token in enumerate(tokens):
        if token is None:
            continue
        found[token] = None
        high = tokens[position + 2] if position + 2 < len(tokens) else None
        if tokens[position + 1 : position + 2] == ["-"] and high is not None:
            for code in (ord(token), ord(high), (ord(token) + ord(high)) // 2):
                if not 0xD800 <= code <= 0xDFFF:
                    found[chr(code)] = None
    return "".join(found)


@lru_cache(maxsize=256)
def _atom_alphabet(atom: str) -> str:
    atom_pattern = _compile_cold(atom)
    # Negated classes use the same non-ASCII-aware sample set as the rest, so a blow-up
    # built from "not ASCII" is probed over the characters it actually matches.
    candidates = _CLASS_SAMPLES + atom + _class_endpoints(atom)
    matched = (char for char in candidates if atom_pattern.fullmatch(char))
    alphabet = "".join({_fold_key(char): char for char in matched}.values())
    if not alphabet:
        # Fail closed: a class with no probe sample would never be exercised.
        raise RuleValidationError(PATTERN_TOO_COMPLEX)
    return alphabet


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
    # A literal prefix followed by a dense mix of the pattern's own literals and a few class
    # samples: the shape that written-out repetition after a literal anchor needs.
    prefix = max(runs, key=len, default="".join(literals[:1]))
    for alphabet in dict.fromkeys((*alphabets, *groups)):
        for dense in dict.fromkeys(
            (alphabet[:4], "".join(dict.fromkeys(alphabet[:4] + "".join(literals))))
        ):
            if len(dense) > 1:
                yield tuple(
                    prefix + "".join(rng.choices(dense, k=MAX_SUBJECT_LENGTH - len(prefix)))
                    for _ in range(32)
                )
    for alphabet in dict.fromkeys(alphabets):
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
    if scanned.repeat_cost > MAX_REPEAT_BUDGET:
        raise RuleValidationError(PATTERN_TOO_COMPLEX)
    if scanned.class_positions > MAX_CLASS_POSITIONS:
        raise RuleValidationError(PATTERN_TOO_COMPLEX)
    if compiled.engine.programsize > MAX_PROGRAM_SIZE:
        raise RuleValidationError(PATTERN_TOO_COMPLEX)
    if len(scanned.atoms) > MAX_CLASS_ATOMS:
        raise RuleValidationError("This pattern has too many character classes.")
    if scanned.pure_literal:
        # A literal-only DFA has at most len + 1 states, and programsize bounds it already.
        return compiled
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
