"""Pure categorisation rule matching and change previews for C3a.

A lower priority number wins, with ties resolved by rule ID order. Amount
bounds are inclusive and signed. A spending range of 10 to 20 uses bounds
of -20 and -10. Use an outflow sign condition to match spending of any amount.

The highest-priority matching rule can replace a category set by a lower-priority
rule. Replaying the same rule produces no change once its action is reflected in
the row. Disabling a rule does not undo its earlier changes. If another enabled
rule matches, its action applies under the usual precedence.

Open storage decisions include whether to apply rules to split parents, whose
category is invisible in reports when their splits own reporting, how to handle
transfer legs, and where to store ``transfer_candidate``. Preview only sets the
flag when requested and never clears a stale flag.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from app.domain.category_precedence import can_replace_category
from app.domain.fingerprint import normalize_description
from app.domain.money import ZERO

MAX_REGEX_LENGTH = 128
MAX_REGEX_DESCRIPTION_LENGTH = 1024

# Consume classes and escapes as single atoms. Exclude ambiguous class starts.
_REGEX_TOKEN = re.compile(
    r"\\[AbBdDsSwWZnrt\\.\[\](){}^$*+?|\-]"
    r"|\[\^?(?:\\[^0-9]|[^\[\]\\^])(?:\\[^0-9]|[^\[\]\\])*\]"
    r"|[^\\\[\]{}]"
)


class RuleValidationError(ValueError):
    """A rule cannot be evaluated as configured."""


def _compile_safe_regex(pattern: str) -> re.Pattern[str]:
    """Compile a bounded subset of Python regex syntax.

    Accept Python literals, dot, character classes, anchors, the escapes in
    _REGEX_TOKEN, and at most one plain capturing group. Quantifiers apply
    only to single consuming atoms. Allow up to four optional atoms and at
    most one greedy unbounded repeat (* or +). Alternation is allowed at
    either level only without an unbounded repeat. Branches and the group
    must be nonempty. The total number of pipes plus one is at most eight,
    and that count times 2**optional is at most sixteen.

    For pattern length m <= 128 and subject length n <= 1024, search tries
    at most n + 1 starts. Each start has at most 16 paths before considering
    the unbounded repeat, which can try up to n + 1 lengths. Checking any
    single path takes O(m) work, giving O(16 * m * (n + 1)**2) search work
    with a repeat and O(16 * m * (n + 1)) without one. This is a static check,
    with no wall-clock timeout.
    """
    if len(pattern) > MAX_REGEX_LENGTH:
        raise RuleValidationError("Regex pattern is too long.")

    groups = repeats = optional = 0
    branches = 1
    previous = ""
    offset = 0
    while offset < len(pattern):
        atom_match = _REGEX_TOKEN.match(pattern, offset)
        if atom_match is None:
            raise RuleValidationError("Regex contains unsupported syntax.")
        atom = atom_match[0]
        if atom == "(":
            groups += 1
            if groups > 1 or pattern.startswith("?", atom_match.end()):
                raise RuleValidationError("Regex allows only one plain group.")
        if atom in ("|", ")") and previous in ("", "(", "|"):
            raise RuleValidationError("Regex cannot contain empty branches or groups.")
        if atom == "|":
            branches += 1
        if atom in ("*", "+", "?"):
            if previous in (")", "*", "+", "?"):
                raise RuleValidationError("Regex cannot repeat groups or quantifiers.")
            if atom == "?":
                optional += 1
            else:
                repeats += 1
            if repeats > 1 or optional > 4:
                raise RuleValidationError("Regex contains too many quantifiers.")
        previous = atom
        offset = atom_match.end()

    if previous == "|":
        raise RuleValidationError("Regex cannot contain empty branches or groups.")
    if branches > 8 or branches * 2**optional > 16:
        raise RuleValidationError("Regex has too many branch and optional paths.")
    if branches > 1 and repeats:
        raise RuleValidationError("Regex cannot combine alternation with * or +.")

    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        raise RuleValidationError("Regex pattern is invalid.") from None


@dataclass(frozen=True, slots=True)
class DescriptionCondition:
    """Match normalized descriptions. Regex uses search and skips overlong descriptions."""

    mode: Literal["contains", "starts-with", "exact", "regex"]
    value: str
    _literal: str = field(init=False, repr=False, compare=False)
    _regex: re.Pattern[str] | None = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.mode not in ("contains", "starts-with", "exact", "regex"):
            raise RuleValidationError("Unknown description match mode.")
        if not self.value.strip():
            raise RuleValidationError("Description text is required.")
        literal = normalize_description(self.value) if self.mode != "regex" else ""
        regex = _compile_safe_regex(self.value) if self.mode == "regex" else None
        object.__setattr__(self, "_literal", literal)
        object.__setattr__(self, "_regex", regex)

    def matches(self, normalized: str) -> bool:
        if self._regex is not None:
            return (
                len(normalized) <= MAX_REGEX_DESCRIPTION_LENGTH
                and self._regex.search(normalized) is not None
            )
        if self.mode == "contains":
            return self._literal in normalized
        if self.mode == "starts-with":
            return normalized.startswith(self._literal)
        return normalized == self._literal


@dataclass(frozen=True, slots=True)
class RuleConditions:
    """Require at least one condition and AND the populated fields.

    Amount and date bounds are inclusive and may be open. Outflows are
    negative, so a spending range of 10 to 20 uses bounds of -20 and -10.
    An outflow sign condition matches spending of any amount when used alone.
    """

    description: DescriptionCondition | None = None
    merchant_key: str | None = None
    amount_equals: Decimal | None = None
    amount_min: Decimal | None = None
    amount_max: Decimal | None = None
    amount_sign: Literal["outflow", "inflow"] | None = None
    account_ids: frozenset[UUID] | None = None
    date_from: date | None = None
    date_through: date | None = None

    def __post_init__(self) -> None:
        amounts = (self.amount_equals, self.amount_min, self.amount_max)
        if all(
            condition is None
            for condition in (
                self.description,
                self.merchant_key,
                *amounts,
                self.amount_sign,
                self.account_ids,
                self.date_from,
                self.date_through,
            )
        ):
            raise RuleValidationError("A rule needs at least one condition.")
        for amount in amounts:
            if amount is not None and (not isinstance(amount, Decimal) or not amount.is_finite()):
                raise RuleValidationError("Amount conditions require finite Decimal values.")
        if (
            self.amount_min is not None
            and self.amount_max is not None
            and self.amount_min > self.amount_max
        ):
            raise RuleValidationError("Minimum amount exceeds maximum amount.")
        if self.amount_sign not in (None, "outflow", "inflow"):
            raise RuleValidationError("Unknown amount sign.")
        if (
            self.date_from is not None
            and self.date_through is not None
            and self.date_from > self.date_through
        ):
            raise RuleValidationError("Start date is after end date.")
        if self.account_ids is not None:
            accounts = frozenset(self.account_ids)
            if not accounts:
                raise RuleValidationError("An account filter needs at least one account.")
            object.__setattr__(self, "account_ids", accounts)

    def matches(self, txn: RuleTransaction, normalized: str) -> bool:
        return (
            (self.merchant_key is None or self.merchant_key == txn.merchant_key)
            and (self.account_ids is None or txn.account_id in self.account_ids)
            and (self.date_from is None or self.date_from <= txn.posted_on)
            and (self.date_through is None or txn.posted_on <= self.date_through)
            and (self.amount_equals is None or txn.amount == self.amount_equals)
            and (self.amount_min is None or self.amount_min <= txn.amount)
            and (self.amount_max is None or txn.amount <= self.amount_max)
            and (self.amount_sign != "outflow" or txn.amount < ZERO)
            and (self.amount_sign != "inflow" or txn.amount > ZERO)
            and (self.description is None or self.description.matches(normalized))
        )


@dataclass(frozen=True, slots=True)
class RuleAction:
    """Set the category and optionally nominate the row for transfer detection."""

    category_id: UUID
    mark_transfer_candidate: bool = False


@dataclass(frozen=True, slots=True)
class Rule:
    """Lower priority numbers win, with ties resolved by rule ID.

    The winning rule can replace a lower-priority rule's category. Replaying
    its applied action produces no change. Disabling a rule leaves its earlier
    changes in place unless another enabled rule supplies a new action.
    """

    id: UUID
    priority: int
    enabled: bool
    conditions: RuleConditions
    action: RuleAction


@dataclass(frozen=True, slots=True)
class RuleTransaction:
    id: UUID
    description: str
    amount: Decimal
    account_id: UUID
    posted_on: date
    merchant_key: str | None = None
    category_id: UUID | None = None
    category_source: str | None = None
    transfer_candidate: bool = False


@dataclass(frozen=True, slots=True)
class RuleMatch:
    transaction_id: UUID
    rule_id: UUID
    action: RuleAction


def can_apply_rule(current_source: str | None) -> bool:
    """Under ADR 0022 only the user outranks a rule, including a user's clear."""
    return can_replace_category(current_source, "rule")


def _ordered_rules(rules: Iterable[Rule]) -> list[Rule]:
    ordered = sorted(rules, key=lambda rule: (rule.priority, rule.id))
    if len({rule.id for rule in ordered}) != len(ordered):
        raise RuleValidationError("Rule IDs must be unique.")
    return [rule for rule in ordered if rule.enabled]


def _first_match(ordered: list[Rule], txn: RuleTransaction) -> RuleMatch | None:
    normalized = normalize_description(txn.description)
    for rule in ordered:
        if rule.conditions.matches(txn, normalized):
            return RuleMatch(txn.id, rule.id, rule.action)
    return None


def match(rules: Iterable[Rule], txn: RuleTransaction) -> RuleMatch | None:
    """Return the first enabled match in priority and ID order regardless of category source."""
    return _first_match(_ordered_rules(rules), txn)


def preview(rules: Iterable[Rule], txns: Iterable[RuleTransaction]) -> list[RuleMatch]:
    """Return actual changes in transaction ID order for a snapshot with unique IDs.

    Applying a change sets the category source to "rule" and sets the transfer
    candidate flag only when requested. Replaying against that state is a no-op.
    """
    ordered = _ordered_rules(rules)
    transactions = sorted(txns, key=lambda txn: txn.id)
    if len({txn.id for txn in transactions}) != len(transactions):
        raise RuleValidationError("Transaction IDs must be unique.")

    changes = []
    for txn in transactions:
        if not can_apply_rule(txn.category_source):
            continue
        hit = _first_match(ordered, txn)
        if hit is not None and (
            txn.category_source != "rule"
            or txn.category_id != hit.action.category_id
            or (hit.action.mark_transfer_candidate and not txn.transfer_candidate)
        ):
            changes.append(hit)
    return changes
