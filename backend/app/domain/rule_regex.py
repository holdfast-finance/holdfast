"""RE2-only description patterns and their save-time cost probe (ADR 0024)."""

from __future__ import annotations

import unicodedata
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
MAX_PREFIX_RUNS = 4
# Total counted repetition above this is refused (sum along a sequence, max across |, nested
# counts multiplied). DFA state growth is 2^n in the count, so 8 bounds what the probe can
# only sample, and bank descriptions never need more.
MAX_REPEAT_BUDGET = 10
# Class positions (every ., class or branching group, times its count) per pattern. Written-out
# copies cost the same as a count, so this closes what the repetition budget cannot see.
MAX_CLASS_POSITIONS = 10
# RE2 program size ceiling (instructions). See PHASE-4-contract 2.2 for the measurements.
MAX_PROGRAM_SIZE = 2000
REGEX_BUDGET_SECONDS = 10
INGEST_REGEX_US_PER_ROW = 500

PATTERNS_UNAVAILABLE = "Patterns aren't available on this server."
PATTERN_INVALID = "This pattern uses unsupported syntax."
PATTERN_TOO_SLOW = "This pattern is too slow to run on every transaction."
PATTERN_UNSUPPORTED = (
    "This pattern uses syntax that isn't supported. See the supported syntax list."
)
PATTERN_INLINE_FLAGS = "Inline flags aren't supported. Matching is always case-insensitive."
PATTERN_TOO_COMPLEX = "This pattern is too complex to check. Try a simpler one."
_PRINTABLE_ASCII = "".join(chr(code) for code in range(32, 127))
_DESCRIPTION_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789    *#-./"
# Provide representatives for whitespace and common non-ASCII scripts and categories.
_CLASS_SAMPLES = (
    _PRINTABLE_ASCII + "\t\n\r\f\v" + "ÉΩЖԱאشअก中あ한٠Ⅳ²€—" + "\u212a\u2126\u212b\u017f\u1e9e"
)  # fold twins: Kelvin, Ohm, Angstrom, long s, capital sharp s


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


_ESCAPABLE = ".*+?()[]{}|\\^$-/#"
_META = ".*+?()[]{}|\\^$"
_HEX = frozenset("0123456789abcdefABCDEF")
MAX_COUNT = 1000  # RE2's own ceiling on a counted repetition


def _unsupported(message: str = PATTERN_UNSUPPORTED) -> RuleValidationError:
    return RuleValidationError(message)


def _hex_escape(pattern: str, i: int) -> tuple[int, str]:
    """Parse ``\\xHH`` or ``\\x{H..H}`` with ``i`` at the ``x``. Return (end, character)."""
    if pattern[i + 1 : i + 2] == "{":
        close = pattern.find("}", i + 2)
        digits = pattern[i + 2 : close] if close != -1 else ""
        end = close + 1
        valid = 1 <= len(digits) <= 6
    else:
        digits = pattern[i + 1 : i + 3]
        end = i + 3
        valid = len(digits) == 2
    if not valid or not _HEX.issuperset(digits):
        raise _unsupported()
    code = int(digits, 16)
    if code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
        raise _unsupported()
    return end, chr(code)


def _parse_escape(pattern: str, i: int, in_class: bool) -> tuple[int, str, str]:
    """Parse one allowed escape at ``i``. Return (end, kind, value).

    kind is ``atom`` (a class escape, value is its text), ``literal`` (value is the
    character) or ``zero`` (a ``\\b`` or ``\\B`` boundary). Anything else is unsupported.
    """
    if i + 1 >= len(pattern):
        raise _unsupported()
    kind = pattern[i + 1]
    if kind in "dDwWsS":
        return i + 2, "atom", pattern[i : i + 2]
    if kind in "bB" and not in_class:
        return i + 2, "zero", ""
    if kind == "x":
        end, char = _hex_escape(pattern, i + 1)
        return end, "literal", char
    if kind in "pP":
        if pattern[i + 2 : i + 3] == "{":
            close = pattern.find("}", i + 3)
            name = pattern[i + 3 : close] if close != -1 else ""
            if not name or not name.isascii() or not name.replace("_", "").isalpha():
                raise _unsupported()
            end = close + 1
        else:
            if not (pattern[i + 2 : i + 3].isascii() and pattern[i + 2 : i + 3].isalpha()):
                raise _unsupported()
            end = i + 3
        _compile_cold(pattern[i:end])  # RE2 decides whether the property name exists
        return end, "atom", pattern[i:end]
    if kind in _ESCAPABLE:
        return i + 2, "literal", kind
    raise _unsupported()


def _parse_bracket(pattern: str, start: int) -> tuple[int, list[tuple[str, ...]]]:
    """Parse ``[...]`` or ``[^...]`` at ``start``. Return (end, items).

    Items are ``("c", ch)``, ``("class", text)`` or ``("range", lo, hi)``. No nested ``[``
    (so no POSIX ``[:alpha:]``), no leading ``]`` and no unescaped ``-`` except first or last.
    """
    i = start + 1 + (pattern[start + 1 : start + 2] == "^")
    items: list[tuple[str, ...]] = []
    while True:
        if i >= len(pattern):
            raise _unsupported()
        char = pattern[i]
        if char == "]":
            if not items:
                raise _unsupported()
            return i + 1, items
        if char == "[":
            raise _unsupported()
        token: tuple[str, ...]
        if char == "\\":
            end, kind, value = _parse_escape(pattern, i, True)
            token = ("class", value) if kind == "atom" else ("c", value)
            i = end
        else:
            if char == "-" and items and pattern[i + 1 : i + 2] != "]":
                raise _unsupported()
            token = ("c", char)
            i += 1
        if (
            token[0] == "c"
            and pattern[i : i + 1] == "-"
            and pattern[i + 1 : i + 2] not in ("]", "")
        ):
            high_at = i + 1
            if pattern[high_at] == "[":
                raise _unsupported()
            if pattern[high_at] == "\\":
                end, kind, value = _parse_escape(pattern, high_at, True)
                if kind != "literal":
                    raise _unsupported()
                high, i = value, end
            else:
                high, i = pattern[high_at], high_at + 1
            if ord(high) < ord(token[1]):
                raise _unsupported()
            items.append(("range", token[1], high))
            continue
        items.append(token)


def _parse_count(pattern: str, start: int) -> tuple[int, int]:
    """Parse ``{n}``, ``{n,}`` or ``{n,m}``. Return (end, largest count). Anything else is out."""
    close = pattern.find("}", start + 1)
    if close == -1:
        raise _unsupported()
    parts = pattern[start + 1 : close].split(",")
    if (
        len(parts) > 2
        or not parts[0]
        or any(part and not (part.isascii() and part.isdigit()) for part in parts)
    ):
        raise _unsupported()
    low = int(parts[0])
    high = int(parts[1]) if len(parts) == 2 and parts[1] else None
    if low > MAX_COUNT or (high is not None and (high > MAX_COUNT or high < low)):
        raise _unsupported()
    return close + 1, max(low, high or 0)


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
    # Every maximal literal run, uncapped, in pattern order. Probe prefixes come from these.
    maximal_runs: tuple[str, ...] = ()


def _scan(pattern: str) -> _Scan:
    """Parse the allowlisted grammar and measure it. Anything outside it is unsupported.

    Allowed: literals, escaped metacharacters, ``\\d \\D \\w \\W \\s \\S \\b \\B``, ``\\xHH``,
    ``\\x{H..}``, ``\\p{Name}``/``\\pL``, brackets without nesting, ``(?:...)`` and ``(...)``,
    ``|``, ``* + ?`` (with a lazy ``?``), ``{n}``/``{n,}``/``{n,m}``, ``^ $`` and ``.``.
    Inline flags, named groups, ``\\Q...\\E``, POSIX classes and bare ``]``/``}`` are refused.
    """
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
    run_now = ""
    maximal: list[str] = []
    # What the previous after allows next: "elem" takes a quantifier, "quant" takes a lazy ?.
    prev = "start"
    i = 0
    while i < len(pattern):
        char = pattern[i]
        end = i + 1
        literal = ""
        atom = ""
        closed_rep = closed_pos = 0
        after = "elem"
        if char == "[":
            end, _ = _parse_bracket(pattern, i)
            atom = pattern[i:end]
        elif char == ".":
            atom = char
        elif char == "\\":
            end, kind, value = _parse_escape(pattern, i, False)
            if kind == "atom":
                atom = value
            elif kind == "literal":
                literal = value
            else:
                after = "anchor"
        elif char == "(":
            if pattern.startswith("(?", i) and not pattern.startswith("(?:", i):
                # Flag syntax is `(?` then flag letters or `-`. Named groups and the rest are
                # plain unsupported syntax.
                is_flags = pattern[i + 2 : i + 3] in tuple("imsU-")
                raise _unsupported(PATTERN_INLINE_FLAGS if is_flags else PATTERN_UNSUPPORTED)
            end = i + (3 if pattern.startswith("(?:", i) else 1)
            open_groups.append(({}, {}))
            levels.append([0, 0, 0, 0])
            has_alternation.append(False)
            after = "start"
        elif char == "|":
            pure_literal = False
            top = levels[-1]
            levels[-1] = [max(top[:2]), 0, max(top[2:]), 0]
            has_alternation[-1] = True
            after = "start"
        elif char == ")":
            if not open_groups:
                raise _unsupported()
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
                parent[0].update(letters)
        elif char in "*+?":
            pure_literal = False
            if prev == "elem":
                after = "quant"
            elif prev == "quant" and char == "?":
                after = "lazy"
            else:
                raise _unsupported()
            closed_rep, closed_pos = last_rep, last_pos
        elif char == "{":
            if prev != "elem":
                raise _unsupported()
            end, count = _parse_count(pattern, i)
            pure_literal = False
            after = "quant"
            if last_pos:
                # A count on a single literal or a pure-literal group does not branch the DFA.
                closed_rep = count * max(last_rep, 1)
                closed_pos = count * last_pos
                levels[-1][1] += closed_rep - last_rep
                levels[-1][3] += closed_pos - last_pos
            else:
                closed_rep, closed_pos = last_rep, last_pos
        elif char in "^$":
            after = "anchor"
        elif char in "]}":
            raise _unsupported()
        else:
            literal = char

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
                literals.setdefault(letter, letter)
                for letters, _ in open_groups:
                    letters.setdefault(letter, letter)
                run_now += letter
                recent = (recent + letter)[-3:]
                for size in range(2, len(recent) + 1):
                    runs.setdefault(recent[-size:], recent[-size:])
        else:
            if after == "quant":
                run_now = run_now[:-1]  # the quantified character is optional or repeated
            if after not in ("quant", "lazy"):
                recent = ""
            if run_now and after != "lazy":
                maximal.append(run_now)
                run_now = ""
        prev = after
        i = end
    if open_groups:
        raise _unsupported()
    return _Scan(
        tuple(atoms),
        tuple(literals.values()),
        tuple(runs.values()),
        tuple(groups),
        max(levels[0][:2]),
        max(levels[0][2:]),
        pure_literal,
        tuple(maximal) + ((run_now,) if run_now else ()),
    )


def _class_endpoints(atom: str) -> str:
    """Characters a bracket class names by escape or range: both ends and each midpoint.

    ``[\\x{10D0}-\\x{10EF}]`` names no printable character, so without this it is never sampled.
    """
    if not atom.startswith("["):
        return ""
    found: dict[str, None] = {}
    for item in _parse_bracket(atom, 0)[1]:
        if item[0] == "c":
            found[item[1]] = None
        elif item[0] == "range":
            low, high = ord(item[1]), ord(item[2])
            for code in (low, high, (low + high) // 2):
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
    alphabet = "".join(dict.fromkeys(matched))
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
    return "".join(dict.fromkeys(matched))


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
    prefixes: tuple[str, ...] = (),
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
    # One family per maximal literal run, longest first, so a long prefix is never skipped.
    chosen = sorted(dict.fromkeys(prefixes), key=len, reverse=True)[:MAX_PREFIX_RUNS]
    for prefix in chosen or [max(runs, key=len, default="".join(literals[:1]))]:
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


def _compile_checked(pattern: str) -> tuple[_Pattern, _Scan]:
    if not 1 <= len(pattern) <= MAX_PATTERN_LENGTH or "\0" in pattern:
        raise RuleValidationError("Patterns must be 1 to 128 characters and contain no NUL.")
    engine = _compile_cold(pattern)
    try:
        # Stored and new patterns both go through the allowlist parser. Only what it fully
        # understands reaches the budgets, the probe or the matcher.
        scanned = _scan(pattern)
    except IndexError:
        raise _unsupported() from None
    if engine.search("") is not None:
        raise RuleValidationError("Patterns must not match an empty description.")
    return _Pattern(engine), scanned


def _nfkc(pattern: str) -> str:
    """Patterns see the same NFKC text as subjects, so compatibility twins cannot differ."""
    return unicodedata.normalize("NFKC", pattern)


def compile_stored_pattern(pattern: str) -> CompiledPattern:
    """Compile a previously validated stored pattern without a load-dependent cost probe."""
    return _compile_checked(_nfkc(pattern))[0]


def compile_pattern(pattern: str) -> CompiledPattern:
    """Validate and measure a new pattern before admitting it to the rules engine."""
    if re2 is None:
        raise RuleValidationError(PATTERNS_UNAVAILABLE)
    pattern = _nfkc(pattern)
    compiled, scanned = _compile_checked(pattern)
    # Structural limits run first and bound the whole class of blow-ups. The probe below
    # is a second line, not the only one.
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
    families = _probe_families(
        alphabets, scanned.literals, scanned.runs, groups, scanned.maximal_runs
    )
    for count, subjects in enumerate(families, 1):
        if count > MAX_PROBE_FAMILIES:
            # A cap that is hit rejects. It never accepts what it could not finish probing.
            raise RuleValidationError(PATTERN_TOO_COMPLEX)
        if _family_mean_ns(pattern, subjects) > PROBE_LIMIT_MICROSECONDS * 1_000:
            raise RuleValidationError(PATTERN_TOO_SLOW)
    return compiled
