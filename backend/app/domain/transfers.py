"""Pure transfer matcher (PLAN 8.3). No I/O, no models: the service loads rows and writes pairs.

A transfer is two legs: an outflow on one account and an inflow of the same absolute amount on
another account of the same currency, posted 0 to ``MAX_DAYS`` days apart. A credit-card payment
(outflow on a depository account, inflow on the card) is a transfer; the card's purchases have no
opposite leg on another account, so they never pair and stay counted as spending. Amounts are
never summed or compared across currencies.

**Eligibility.** Rows already in a transfer (paired or single-marked) or superseded take no part,
neither as a leg nor as a competitor. Two legs on one account never pair.

**Ranking.** An edge (a possible pair) has a key ``(days apart, -hints)``; smaller is better.
``hints`` counts (at most 3): transfer wording in either description, payment wording in either
description when one account is a card or loan, and either description naming the other leg's
account mask. Ties are never broken by id, amount order or input order.

**Resolution (deterministic, input-order independent).** Repeat: each unpaired leg looks at its
best edges (those sharing its smallest key). An edge whose two legs each have it as their one and
only best edge is a confirmed pair; all confirmed pairs of a round are taken together, those legs
leave the pool, and the next round re-ranks what is left. Because an edge's key is the same for
both of its legs, this only stalls on a real tie at the best key. Legs that remain with at least
one candidate are returned as ``AmbiguousLeg`` (candidates sorted best key first, then id) and are
never paired: a person decides. Legs with no candidate at all are simply unmatched.
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


@dataclass(frozen=True, slots=True)
class TransferCandidate:
    """One transaction as the matcher sees it. ``description`` is the normalized text."""

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


@dataclass(frozen=True, slots=True)
class ProposedPair:
    outflow_id: uuid.UUID
    inflow_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class AmbiguousLeg:
    transaction_id: uuid.UUID
    candidate_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True, slots=True)
class MatchResult:
    pairs: tuple[ProposedPair, ...]
    ambiguous: tuple[AmbiguousLeg, ...]


_Key = tuple[int, int]


def _hints(out: TransferCandidate, inn: TransferCandidate) -> int:
    texts = (out.description.casefold(), inn.description.casefold())
    n = 0
    if any(_TRANSFER_WORDS.search(t) for t in texts):
        n += 1
    if (out.account_type in CARD_TYPES or inn.account_type in CARD_TYPES) and any(
        _PAYMENT_WORDS.search(t) for t in texts
    ):
        n += 1
    named = ((inn.account_mask, texts[0]), (out.account_mask, texts[1]))
    if any(mask and mask in text for mask, text in named):
        n += 1
    return n


def _edge_key(out: TransferCandidate, inn: TransferCandidate) -> _Key:
    return (abs((out.posted_date - inn.posted_date).days), -_hints(out, inn))


def _can_pair(out: TransferCandidate, inn: TransferCandidate) -> bool:
    return (
        out.account_id != inn.account_id
        and out.currency == inn.currency
        and -out.amount == inn.amount
        and abs((out.posted_date - inn.posted_date).days) <= MAX_DAYS
    )


def match_transfers(candidates: Iterable[TransferCandidate]) -> MatchResult:
    """Propose transfer pairs and list the legs that are too ambiguous to pair."""
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
            if leg in outs and len(b) == 1 and best.get(next(iter(b))) == {leg}
        ]
        if not taken:
            break
        for oid, iid in taken:
            pairs.append(ProposedPair(oid, iid))
            live -= {oid, iid}

    ambiguous: list[AmbiguousLeg] = []
    for leg in live:
        options = {p: k for p, k in edges[leg].items() if p in live}
        if options:
            ordered = sorted(options, key=lambda p: (options[p], str(p)))
            ambiguous.append(AmbiguousLeg(leg, tuple(ordered)))
    pairs.sort(key=lambda p: (str(p.outflow_id), str(p.inflow_id)))
    ambiguous.sort(key=lambda a: str(a.transaction_id))
    return MatchResult(tuple(pairs), tuple(ambiguous))
