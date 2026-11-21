"""Pure transfer matcher (PLAN 8.3). No I/O, no models: the service loads rows and writes pairs.

A transfer is two legs: an outflow on one account and an inflow of the same absolute amount on
another account of the same currency, posted 0 to ``MAX_DAYS`` days apart. A credit-card payment
(outflow on a depository account, inflow on the card) is a transfer; the card's purchases have no
opposite leg on another account, so they never pair and stay counted as spending. Amounts are
never summed or compared across currencies.

**Eligibility.** Rows already in a transfer (paired or single-marked) or superseded take no part,
neither as a leg nor as a competitor. Two legs on one account never pair. Pending rows are
competitors only (they can make a leg ambiguous) and never become legs.

**Hints.** Each leg earns a hint for transfer wording in its own description, for payment wording
in its own description when either account is a card or loan, and for naming the other account's
mask (exactly four digits, matched on digit boundaries). An inflow on a card or loan with no
payment wording earns nothing: it is a refund candidate, not a payment.

**Ranking.** An edge (a possible pair) has the key ``(-hints, days apart)``; smaller is better, so
hints decide before days and a real, hinted partner a few days away beats an unrelated same-day
row. Ties are never broken by id, amount order or input order.

**Auto-apply vs suggest.** A confirmed edge goes to ``pairs`` only when both legs carry a hint of
their own, or a mask hint names the other account; a card or loan inflow must also say "payment"
itself (the checking side saying "payment" does not make a card refund a payment). Otherwise it
goes to ``suggested`` (``no_hint``, ``one_sided_hint``, ``refund_candidate``): a refund and a
purchase, or rent and a paycheck, look alike. It
also goes to ``suggested``, never ``pairs``, when a leg is ``protected`` (the person categorized it
or split it, so treated it as real spending) or when the two accounts have different owners (no
single person could undo the pair).

**Closer alternative.** If either leg of a confirmed edge has another live edge with strictly
fewer days, the edge is not resolved (neither paired nor suggested): hints outrank days, but never
silently. Those legs stay in the pool and come back as ``AmbiguousLeg`` with all their candidates,
so no equal-amount row that lost a contest is absent from every list.

**Resolution (deterministic, input-order independent).** Repeat: each unresolved leg looks at its
best edges (those sharing its smallest key). An edge whose two legs each have it as their one and
only best edge, and that touches no pending row, is resolved (pair or suggestion); all of a
round's resolutions are taken together, those legs leave the pool, and the next round re-ranks
what is left. Legs that remain with at least one candidate are returned as ``AmbiguousLeg``
(candidates sorted best key first, then id): a person decides. Legs with no candidate at all are
simply unmatched.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.domain.money import ZERO

MAX_DAYS = 5
CARD_TYPES = frozenset({"credit", "loan"})
_TRANSFER_WORDS = re.compile(r"\b(transfer|xfer|trnsfr|trf)\b")
_PAYMENT_WORDS = re.compile(r"\b(payment|pmt|pymt|autopay)\b")
MASK_DIGITS = 4

REASON_NO_HINT = "no_hint"
REASON_REFUND = "refund_candidate"
REASON_PROTECTED = "user_treated_as_spending"
REASON_OWNERS = "different_owners"
REASON_ONE_SIDED = "one_sided_hint"


@dataclass(frozen=True, slots=True)
class TransferCandidate:
    """One transaction as the matcher sees it. ``description`` is the normalized text.

    ``protected``: a person categorized or split the row. ``owner_person_id``: the account owner
    (``None`` is joint). ``is_pending``: a competitor only, never a leg.
    """

    transaction_id: uuid.UUID
    account_id: uuid.UUID
    posted_date: date
    amount: Decimal
    currency: str
    description: str
    account_type: str
    account_mask: str | None = None
    in_transfer: bool = False
    superseded: bool = False
    is_pending: bool = False
    protected: bool = False
    owner_person_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ProposedPair:
    outflow_id: uuid.UUID
    inflow_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class SuggestedPair:
    """A confident-looking edge that must not be auto-applied. ``reason`` says why."""

    outflow_id: uuid.UUID
    inflow_id: uuid.UUID
    reason: str


@dataclass(frozen=True, slots=True)
class AmbiguousLeg:
    transaction_id: uuid.UUID
    candidate_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True, slots=True)
class MatchResult:
    pairs: tuple[ProposedPair, ...]
    suggested: tuple[SuggestedPair, ...]
    ambiguous: tuple[AmbiguousLeg, ...]


_Key = tuple[int, int]


def _leg_hints(me: TransferCandidate, other: TransferCandidate) -> tuple[int, bool]:
    """Hints this leg's own description earns, and whether one of them is the mask."""
    text = me.description.casefold()
    n = 0
    if _TRANSFER_WORDS.search(text):
        n += 1
    if (me.account_type in CARD_TYPES or other.account_type in CARD_TYPES) and (
        _PAYMENT_WORDS.search(text)
    ):
        n += 1
    mask = other.account_mask
    named = bool(
        mask
        and len(mask) == MASK_DIGITS
        and mask.isdigit()
        and re.search(rf"(?<!\d){mask}(?!\d)", text)
    )
    return n + int(named), named


def _hints(out: TransferCandidate, inn: TransferCandidate) -> int:
    return _leg_hints(out, inn)[0] + _leg_hints(inn, out)[0]


def _inflow_says_payment(inn: TransferCandidate) -> bool:
    return bool(_PAYMENT_WORDS.search(inn.description.casefold()))


def _edge_key(out: TransferCandidate, inn: TransferCandidate) -> _Key:
    return (-_hints(out, inn), abs((out.posted_date - inn.posted_date).days))


def _can_pair(out: TransferCandidate, inn: TransferCandidate) -> bool:
    return (
        out.account_id != inn.account_id
        and out.currency == inn.currency
        and -out.amount == inn.amount
        and abs((out.posted_date - inn.posted_date).days) <= MAX_DAYS
    )


def _suggest_reason(out: TransferCandidate, inn: TransferCandidate) -> str | None:
    """Why this confirmed edge must not be auto-applied, or ``None`` if it may be."""
    if out.protected or inn.protected:
        return REASON_PROTECTED
    if (
        out.owner_person_id is not None
        and inn.owner_person_id is not None
        and out.owner_person_id != inn.owner_person_id
    ):
        return REASON_OWNERS
    (n_out, mask_out), (n_in, mask_in) = _leg_hints(out, inn), _leg_hints(inn, out)
    if inn.account_type in CARD_TYPES and not _inflow_says_payment(inn):
        return REASON_REFUND  # a card inflow must say "payment" itself, else it is a refund
    if n_out + n_in == 0:
        return REASON_NO_HINT
    if not ((n_out > 0 and n_in > 0) or mask_out or mask_in):
        return REASON_ONE_SIDED
    return None


def match_transfers(candidates: Iterable[TransferCandidate]) -> MatchResult:
    """Propose transfer pairs, suggestions and ambiguous legs."""
    pool = {
        c.transaction_id: c
        for c in candidates
        if not c.in_transfer and not c.superseded and c.amount != ZERO
    }
    outs = {i: c for i, c in pool.items() if c.amount < ZERO}
    ins = {i: c for i, c in pool.items() if c.amount > ZERO}

    # Index inflows by (currency, amount) so the edge search is not quadratic over the window.
    by_value: dict[tuple[str, Decimal], list[uuid.UUID]] = defaultdict(list)
    for i, c in ins.items():
        by_value[(c.currency, c.amount)].append(i)
    edges: dict[uuid.UUID, dict[uuid.UUID, _Key]] = defaultdict(dict)
    for oid, o in outs.items():
        for iid in by_value.get((o.currency, -o.amount), ()):
            if _can_pair(o, ins[iid]):
                key = _edge_key(o, ins[iid])
                edges[oid][iid] = key
                edges[iid][oid] = key

    pairs: list[ProposedPair] = []
    suggested: list[SuggestedPair] = []
    live = set(edges)
    while True:
        best: dict[uuid.UUID, set[uuid.UUID]] = {}
        for leg in live:
            options = {p: k for p, k in edges[leg].items() if p in live}
            if options:
                low = min(options.values())
                best[leg] = {p for p, k in options.items() if k == low}
        taken = [
            (leg, next(iter(b)))
            for leg, b in best.items()
            if leg in outs
            and len(b) == 1
            and best.get(next(iter(b))) == {leg}
            and not pool[leg].is_pending
            and not pool[next(iter(b))].is_pending
        ]
        if not taken:
            break
        resolved = 0
        round_live = set(live)
        for oid, iid in taken:
            days = edges[oid][iid][1]
            closer = any(
                k[1] < days
                for leg in (oid, iid)
                for p, k in edges[leg].items()
                if p in round_live and p not in (oid, iid)
            )
            if closer:
                continue  # a nearer equal-amount row exists: leave all of them to a person
            reason = _suggest_reason(pool[oid], pool[iid])
            if reason is None:
                pairs.append(ProposedPair(oid, iid))
            else:
                suggested.append(SuggestedPair(oid, iid, reason))
            live -= {oid, iid}
            resolved += 1
        if not resolved:
            break

    ambiguous: list[AmbiguousLeg] = []
    for leg in live:
        if pool[leg].is_pending:
            continue
        options = {p: k for p, k in edges[leg].items() if p in live}
        if options:
            ordered = sorted(options, key=lambda p: (options[p], str(p)))
            ambiguous.append(AmbiguousLeg(leg, tuple(ordered)))
    pairs.sort(key=lambda p: (str(p.outflow_id), str(p.inflow_id)))
    suggested.sort(key=lambda p: (str(p.outflow_id), str(p.inflow_id)))
    ambiguous.sort(key=lambda a: str(a.transaction_id))
    return MatchResult(tuple(pairs), tuple(suggested), tuple(ambiguous))
