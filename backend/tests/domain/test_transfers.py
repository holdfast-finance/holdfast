"""Pure transfer matcher: pairs, edges, ambiguity, order independence. Synthetic data."""

from __future__ import annotations

import random
import uuid
from datetime import date, timedelta
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from app.domain.transfers import MatchResult, TransferCandidate, match_transfers

D = Decimal
CHK, SAV, CARD, OTHER = (uuid.UUID(int=i) for i in (101, 102, 103, 104))
DAY = date(2026, 3, 10)


def c(
    n: int,
    account: uuid.UUID,
    amount: str,
    *,
    days: int = 0,
    desc: str = "EXAMPLE",
    kind: str = "depository",
    cur: str = "USD",
    mask: str | None = None,
    in_transfer: bool = False,
    superseded: bool = False,
) -> TransferCandidate:
    return TransferCandidate(
        uuid.UUID(int=n),
        account,
        DAY + timedelta(days=days),
        D(amount),
        cur,
        desc,
        kind,
        mask,
        in_transfer,
        superseded,
    )


def ids(r: MatchResult) -> set[tuple[int, int]]:
    return {(p.outflow_id.int, p.inflow_id.int) for p in r.pairs}


def test_exact_pair() -> None:
    r = match_transfers([c(1, CHK, "-50.00"), c(2, SAV, "50.00")])
    assert ids(r) == {(1, 2)} and not r.ambiguous


def test_five_day_edge_pairs_and_six_day_misses() -> None:
    assert ids(match_transfers([c(1, CHK, "-5.00"), c(2, SAV, "5.00", days=5)])) == {(1, 2)}
    assert ids(match_transfers([c(1, CHK, "-5.00", days=5), c(2, SAV, "5.00")])) == {(1, 2)}
    r = match_transfers([c(1, CHK, "-5.00"), c(2, SAV, "5.00", days=6)])
    assert not r.pairs and not r.ambiguous


def test_card_payment_pairs_but_purchases_do_not() -> None:
    r = match_transfers(
        [
            c(1, CHK, "-300.00", desc="ONLINE PMT CARD"),
            c(2, CARD, "300.00", desc="PAYMENT THANK YOU", kind="credit"),
            c(3, CARD, "-42.10", desc="EXAMPLE MART", kind="credit"),
            c(4, CARD, "-9.99", desc="EXAMPLE CAFE", kind="credit"),
        ]
    )
    assert ids(r) == {(1, 2)} and not r.ambiguous


def test_three_way_ambiguity_is_returned_not_guessed() -> None:
    r = match_transfers([c(1, CHK, "-20.00"), c(2, SAV, "20.00"), c(3, OTHER, "20.00")])
    assert not r.pairs
    assert {a.transaction_id.int for a in r.ambiguous} == {1, 2, 3}
    one = next(a for a in r.ambiguous if a.transaction_id.int == 1)
    assert {i.int for i in one.candidate_ids} == {2, 3}


def test_closest_date_breaks_a_tie() -> None:
    r = match_transfers(
        [c(1, CHK, "-20.00"), c(2, SAV, "20.00", days=3), c(3, OTHER, "20.00", days=1)]
    )
    assert ids(r) == {(1, 3)}


def test_hints_break_a_same_day_tie() -> None:
    r = match_transfers(
        [
            c(1, CHK, "-20.00", desc="TRANSFER TO SAVINGS 1234"),
            c(2, SAV, "20.00", mask="1234"),
            c(3, OTHER, "20.00", mask="9999"),
        ]
    )
    assert ids(r) == {(1, 2)}


def test_same_account_never_pairs() -> None:
    r = match_transfers([c(1, CHK, "-10.00"), c(2, CHK, "10.00")])
    assert not r.pairs and not r.ambiguous


def test_cross_currency_never_pairs() -> None:
    r = match_transfers([c(1, CHK, "-10.00"), c(2, SAV, "10.00", cur="EUR")])
    assert not r.pairs and not r.ambiguous


def test_already_paired_and_superseded_are_ignored() -> None:
    assert not match_transfers([c(1, CHK, "-10.00"), c(2, SAV, "10.00", in_transfer=True)]).pairs
    assert not match_transfers([c(1, CHK, "-10.00"), c(2, SAV, "10.00", superseded=True)]).pairs
    r = match_transfers(
        [c(1, CHK, "-10.00"), c(2, SAV, "10.00"), c(3, OTHER, "10.00", superseded=True)]
    )
    assert ids(r) == {(1, 2)} and not r.ambiguous


def test_unequal_or_same_sign_amounts_never_pair() -> None:
    assert not match_transfers([c(1, CHK, "-10.00"), c(2, SAV, "10.01")]).pairs
    assert not match_transfers([c(1, CHK, "10.00"), c(2, SAV, "10.00")]).pairs


def test_two_independent_pairs() -> None:
    r = match_transfers(
        [
            c(1, CHK, "-10.00"),
            c(2, SAV, "10.00"),
            c(3, CHK, "-70.00"),
            c(4, CARD, "70.00", kind="credit"),
        ]
    )
    assert ids(r) == {(1, 2), (3, 4)}


@st.composite
def pools(draw: st.DrawFn) -> list[TransferCandidate]:
    accts = [CHK, SAV, CARD, OTHER]
    n = draw(st.integers(0, 10))
    return [
        c(
            i + 1,
            draw(st.sampled_from(accts)),
            draw(st.sampled_from(["5.00", "-5.00", "7.50", "-7.50"])),
            days=draw(st.integers(0, 8)),
            cur=draw(st.sampled_from(["USD", "USD", "EUR"])),
        )
        for i in range(n)
    ]


@given(pools(), st.integers(0, 10_000))
def test_order_independent_and_invariants(pool: list[TransferCandidate], seed: int) -> None:
    shuffled = pool[:]
    random.Random(seed).shuffle(shuffled)  # noqa: S311
    assert match_transfers(pool) == match_transfers(shuffled)
    by_id = {t.transaction_id: t for t in pool}
    used: set[uuid.UUID] = set()
    for p in match_transfers(pool).pairs:
        o, i = by_id[p.outflow_id], by_id[p.inflow_id]
        assert o.amount < 0 < i.amount and o.amount == -i.amount
        assert o.account_id != i.account_id and o.currency == i.currency
        assert abs((o.posted_date - i.posted_date).days) <= 5
        assert not {p.outflow_id, p.inflow_id} & used
        used |= {p.outflow_id, p.inflow_id}
