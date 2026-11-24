"""Pure categorisation rule matching, previews and the conditions codec (C3a, v2 in C3c).

Rules are ordered by ``(priority, created_at, id)``; a lower priority number wins.
Amount bounds are inclusive and signed. A spending range of 10 to 20 uses bounds
of -20 and -10. Use an outflow sign condition to match spending of any amount.

Who may write a row (ADR 0026 point 3): ``any`` rules touch joint rows and the
author's own rows, ``joint`` rules only joint rows, ``own`` rules only the
author's rows. Split parents are never changed (their splits own reporting).

The highest-priority matching rule can replace a category set by a lower-priority
rule. Replaying the same rule produces no change once its action is reflected in
the row. Disabling a rule does not undo its earlier changes.

Description patterns go through ``rule_regex.compile_pattern`` only. This module
never imports ``re`` or ``regex``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import UUID

from app.domain.category_precedence import can_replace_category
from app.domain.fingerprint import normalize_description
from app.domain.merchant import MAX_KEY_LENGTH, MERCHANT_KEY_VERSION, merchant_key
from app.domain.money import ZERO
from app.domain.rule_regex import CompiledPattern, compile_pattern, compile_stored_pattern
from app.domain.rule_regex import RuleValidationError as RuleValidationError

AccountScope = Literal["any", "joint", "own"]
ACCOUNT_SCOPES = ("any", "joint", "own")
CODEC_VERSION = 1
TOKEN_VERSION = 2
MAX_DESCRIPTION_VALUE_LENGTH = 128
MAX_AMOUNT = Decimal(10) ** 12
CENT = Decimal("0.01")


def _check_amount(amount: Decimal) -> None:
    if not amount.is_finite():
        raise RuleValidationError("Amount conditions require finite Decimal values.")
    if abs(amount) >= MAX_AMOUNT:  # before quantize, which raises on 30-digit values
        raise RuleValidationError("Amount is out of range.")
    if amount != amount.quantize(CENT):
        raise RuleValidationError("Amounts allow at most 2 decimal places.")


def _amount_text(amount: Decimal) -> str:
    cents = amount.quantize(CENT)
    return "0.00" if cents == 0 else format(cents, "f")


# A draft sorts after every saved rule of equal priority, where it will sit once saved.
DRAFT_CREATED_AT = datetime.max.replace(tzinfo=UTC)
DRAFT_ID = UUID(int=2**128 - 1)


class MerchantKeyVersionMismatch(RuleValidationError):
    """Stored conditions use another merchant key version. The loader reports needs_review.

    C3e note: catch this before ``RuleValidationError``, which it subclasses.
    """


@dataclass(frozen=True, slots=True)
class DescriptionCondition:
    """Match normalized descriptions.

    Regex mode goes through ``compile_pattern`` (with the save-time cost probe) on the
    create/save path. The loader builds rows that already passed that probe through the
    private ``_from_store``, which uses ``compile_stored_pattern`` so loading is
    deterministic (ADR 0024). Nothing under ``app/api`` or ``app/services`` may call it.
    """

    mode: Literal["contains", "starts-with", "exact", "regex"]
    value: str = field(repr=False)
    _literal: str = field(init=False, repr=False, compare=False)
    _regex: CompiledPattern | None = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._init(compile_pattern)

    @classmethod
    def _from_store(
        cls, mode: Literal["contains", "starts-with", "exact", "regex"], value: str
    ) -> DescriptionCondition:
        condition = object.__new__(cls)
        object.__setattr__(condition, "mode", mode)
        object.__setattr__(condition, "value", value)
        condition._init(compile_stored_pattern)
        return condition

    def _init(self, compile_regex: Callable[[str], CompiledPattern]) -> None:
        if self.mode not in ("contains", "starts-with", "exact", "regex"):
            raise RuleValidationError("Unknown description match mode.")
        if not self.value.strip():
            raise RuleValidationError("Description text is required.")
        if self.mode != "regex" and len(self.value) > MAX_DESCRIPTION_VALUE_LENGTH:
            raise RuleValidationError("Description text is too long.")
        literal = normalize_description(self.value) if self.mode != "regex" else ""
        regex = compile_regex(self.value) if self.mode == "regex" else None
        object.__setattr__(self, "_literal", literal)
        object.__setattr__(self, "_regex", regex)

    def matches(self, normalized: str) -> bool:
        if self._regex is not None:
            return self._regex.search(normalized)
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
                self.date_from,
                self.date_through,
            )
        ):
            raise RuleValidationError("A rule needs at least one condition.")
        for amount in amounts:
            if amount is not None:
                if not isinstance(amount, Decimal):
                    raise RuleValidationError("Amount conditions require finite Decimal values.")
                _check_amount(amount)
        key = self.merchant_key
        if key is not None and not (
            1 <= len(key) <= MAX_KEY_LENGTH and key == merchant_key(key, key)
        ):
            raise RuleValidationError("Merchant key is not in normalized form.")
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

    def matches(self, txn: RuleTransaction, normalized: str) -> bool:
        return (
            (self.merchant_key is None or self.merchant_key == txn.merchant_key)
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
    """Set the category. ``mark_transfer_candidate`` is never produced by the codec or API."""

    category_id: UUID
    mark_transfer_candidate: bool = False


@dataclass(frozen=True, slots=True)
class Rule:
    """Ordered by ``(priority, created_at, id)``; lower priority numbers win."""

    id: UUID
    priority: int
    enabled: bool
    conditions: RuleConditions
    action: RuleAction
    author_person_id: UUID
    created_at: datetime
    account_scope: AccountScope = "any"

    def __post_init__(self) -> None:
        if self.account_scope not in ACCOUNT_SCOPES:
            raise RuleValidationError("Unknown account scope.")
        if self.created_at.tzinfo is None:
            raise RuleValidationError("Rule creation time must be timezone-aware.")

    @property
    def order_key(self) -> tuple[int, datetime, UUID]:
        return (self.priority, self.created_at, self.id)


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
    # Required, no default: a loader that forgets the owner must fail, not look "joint".
    account_owner_person_id: UUID | None = field(kw_only=True)
    is_split: bool = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class RuleMatch:
    transaction_id: UUID
    rule_id: UUID
    action: RuleAction


@dataclass(frozen=True, slots=True)
class RulePreview:
    changes: tuple[RuleMatch, ...]
    skipped_user: int
    skipped_split: int
    skipped_not_writable: int


def can_apply_rule(current_source: str | None) -> bool:
    """Under ADR 0022 only the user outranks a rule, including a user's clear."""
    return can_replace_category(current_source, "rule")


def rule_may_touch(rule: Rule, txn: RuleTransaction) -> bool:
    """ADR 0026 point 3: which account owners a rule may write to."""
    owner = txn.account_owner_person_id
    if rule.account_scope == "joint":
        return owner is None
    if rule.account_scope == "own":
        return owner == rule.author_person_id
    return owner is None or owner == rule.author_person_id


def _ordered_rules(rules: Iterable[Rule]) -> list[Rule]:
    ordered = sorted(rules, key=lambda rule: rule.order_key)
    if len({rule.id for rule in ordered}) != len(ordered):
        raise RuleValidationError("Rule IDs must be unique.")
    return [rule for rule in ordered if rule.enabled]


def _first_match(ordered: list[Rule], txn: RuleTransaction) -> RuleMatch | None:
    normalized = normalize_description(txn.description)
    for rule in ordered:
        if rule_may_touch(rule, txn) and rule.conditions.matches(txn, normalized):
            return RuleMatch(txn.id, rule.id, rule.action)
    return None


def _is_change(txn: RuleTransaction, hit: RuleMatch) -> bool:
    return (
        txn.category_source != "rule"
        or txn.category_id != hit.action.category_id
        or (hit.action.mark_transfer_candidate and not txn.transfer_candidate)
    )


def _sorted_txns(txns: Iterable[RuleTransaction]) -> list[RuleTransaction]:
    transactions = sorted(txns, key=lambda txn: txn.id)
    if len({txn.id for txn in transactions}) != len(transactions):
        raise RuleValidationError("Transaction IDs must be unique.")
    return transactions


def match(rules: Iterable[Rule], txn: RuleTransaction) -> RuleMatch | None:
    """Return the first enabled rule that matches and may touch the row, in rule order."""
    if txn.is_split:
        return None
    return _first_match(_ordered_rules(rules), txn)


def preview(rules: Iterable[Rule], txns: Iterable[RuleTransaction]) -> list[RuleMatch]:
    """Return actual changes in transaction ID order for a snapshot with unique IDs.

    Split parents and rows a user categorised are skipped. Applying a change sets
    the category source to "rule" and sets the transfer candidate flag only when
    requested. Replaying against that state is a no-op.
    """
    ordered = _ordered_rules(rules)
    changes = []
    for txn in _sorted_txns(txns):
        if txn.is_split or not can_apply_rule(txn.category_source):
            continue
        hit = _first_match(ordered, txn)
        if hit is not None and _is_change(txn, hit):
            changes.append(hit)
    return changes


def preview_for(
    target: Rule, others: Iterable[Rule], txns: Iterable[RuleTransaction]
) -> RulePreview:
    """Rows where ``target`` wins and something changes, plus why other matches were skipped.

    Only ``others`` ranking before ``target`` can beat it. A disabled target applies nothing.
    """
    competitors = [r for r in others if r.id != target.id and r.order_key < target.order_key]
    ordered = _ordered_rules([*competitors, target])
    changes: list[RuleMatch] = []
    skipped_user = skipped_split = skipped_not_writable = 0
    for txn in _sorted_txns(txns):
        if not target.enabled:
            break
        if not rule_may_touch(target, txn):
            if target.conditions.matches(txn, normalize_description(txn.description)):
                skipped_not_writable += 1
            continue
        if not target.conditions.matches(txn, normalize_description(txn.description)):
            continue
        if txn.is_split:
            skipped_split += 1
            continue
        if not can_apply_rule(txn.category_source):
            skipped_user += 1
            continue
        hit = _first_match(ordered, txn)
        if hit is not None and hit.rule_id == target.id and _is_change(txn, hit):
            changes.append(hit)
    return RulePreview(tuple(changes), skipped_user, skipped_split, skipped_not_writable)


def preview_token(target: Rule, changes: Sequence[RuleMatch]) -> str:
    """SHA-256 of the rule's content (not its id) and the sorted changed ids.

    An unkeyed content hash: never authorisation by itself.
    """
    payload = {
        "v": TOKEN_VERSION,
        "conditions": conditions_to_json(target.conditions),
        "category_id": str(target.action.category_id),
        "priority": target.priority,
        "enabled": target.enabled,
        "account_scope": target.account_scope,
        "changes": sorted(str(c.transaction_id) for c in changes),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- Codec (v: 1) -----------------------------------------------------------

_KEYS = frozenset(
    {
        "v",
        "description",
        "merchant_key",
        "merchant_key_version",
        "amount_equals",
        "amount_min",
        "amount_max",
        "amount_sign",
        "date_from",
        "date_through",
    }
)
_AMOUNT_CHARS = frozenset("0123456789.-")


def conditions_to_json(c: RuleConditions) -> dict[str, object]:
    out: dict[str, object] = {"v": CODEC_VERSION}
    if c.description is not None:
        out["description"] = {"mode": c.description.mode, "value": c.description.value}
    if c.merchant_key is not None:
        out["merchant_key"] = c.merchant_key
        out["merchant_key_version"] = MERCHANT_KEY_VERSION
    for name in ("amount_equals", "amount_min", "amount_max"):
        amount: Decimal | None = getattr(c, name)
        if amount is not None:
            out[name] = _amount_text(amount)
    if c.amount_sign is not None:
        out["amount_sign"] = c.amount_sign
    for name in ("date_from", "date_through"):
        day: date | None = getattr(c, name)
        if day is not None:
            out[name] = day.isoformat()
    return out


def _amount(obj: Mapping[str, object], key: str) -> Decimal | None:
    raw = obj.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw or not set(raw) <= _AMOUNT_CHARS:
        raise RuleValidationError("Amounts must be decimal strings.")
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise RuleValidationError("Amounts must be decimal strings.") from None


def _day(obj: Mapping[str, object], key: str) -> date | None:
    raw = obj.get(key)
    if raw is None:
        return None
    try:
        if isinstance(raw, str) and len(raw) == 10:
            parsed = date.fromisoformat(raw)
            if parsed.isoformat() == raw:
                return parsed
    except ValueError:
        pass
    raise RuleValidationError("Dates must be ISO dates.")


def conditions_from_json(obj: Mapping[str, object], *, stored: bool = False) -> RuleConditions:
    """Strict inverse of ``conditions_to_json``. Unknown keys and wrong types are refused.

    ``stored=True`` is for the loader reading saved rows: regexes are recompiled without the
    cost probe. Anything that creates or edits a rule leaves it False and is probed.
    """
    if set(obj) - _KEYS:
        raise RuleValidationError("Unknown condition key.")
    version = obj.get("v")
    if type(version) is not int or version != CODEC_VERSION:
        raise RuleValidationError("Unsupported conditions version.")

    description: DescriptionCondition | None = None
    raw_desc = obj.get("description")
    if raw_desc is not None:
        if (
            not isinstance(raw_desc, Mapping)
            or set(raw_desc) != {"mode", "value"}
            or not isinstance(raw_desc["mode"], str)
            or not isinstance(raw_desc["value"], str)
        ):
            raise RuleValidationError("Description condition is malformed.")
        mode = raw_desc["mode"]
        if mode not in ("contains", "starts-with", "exact", "regex"):
            raise RuleValidationError("Unknown description match mode.")
        if stored:
            description = DescriptionCondition._from_store(mode, raw_desc["value"])  # type: ignore[arg-type]
        else:
            description = DescriptionCondition(mode, raw_desc["value"])  # type: ignore[arg-type]

    key = obj.get("merchant_key")
    key_version = obj.get("merchant_key_version")
    if key is not None and not isinstance(key, str):
        raise RuleValidationError("Merchant key must be a string.")
    if (key is None) != (key_version is None):
        raise RuleValidationError("Merchant key and its version go together.")
    if key_version is not None:
        if type(key_version) is not int:
            raise RuleValidationError("Merchant key version must be an integer.")
        if key_version != MERCHANT_KEY_VERSION:
            raise MerchantKeyVersionMismatch("Merchant key version is out of date.")

    sign = obj.get("amount_sign")
    if sign not in (None, "outflow", "inflow"):
        raise RuleValidationError("Unknown amount sign.")

    return RuleConditions(
        description=description,
        merchant_key=key,
        amount_equals=_amount(obj, "amount_equals"),
        amount_min=_amount(obj, "amount_min"),
        amount_max=_amount(obj, "amount_max"),
        amount_sign=sign,
        date_from=_day(obj, "date_from"),
        date_through=_day(obj, "date_through"),
    )
