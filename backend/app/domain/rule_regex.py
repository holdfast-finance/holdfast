"""Rule pattern compilation, C3c stub.

ADR 0024 puts description patterns on RE2 only, with a cost probe and no
fallback. That engine lands in C3d. Until then every pattern is refused, so no
path (API, CLI, backfill or agent) can compile a user pattern with a
backtracking engine. This module must never import ``re`` or ``regex``;
``tests/domain/test_regex_imports.py`` enforces that.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

MAX_PATTERN_LENGTH = 128
MAX_SUBJECT_LENGTH = 256
PROBE_LIMIT_MICROSECONDS = 10
MAX_CLASS_ATOMS = 16
REGEX_BUDGET_SECONDS = 10
INGEST_REGEX_MS_PER_ROW = Decimal("0.5")

PATTERNS_UNAVAILABLE = "Patterns aren't available yet."


class RuleValidationError(ValueError):
    """A rule cannot be evaluated as configured."""


class CompiledPattern(Protocol):
    def search(self, text: str) -> bool: ...


def compile_pattern(pattern: str) -> CompiledPattern:
    """Refuse every pattern until C3d wires RE2. The input is never inspected or echoed."""
    raise RuleValidationError(PATTERNS_UNAVAILABLE)
