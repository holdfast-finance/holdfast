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
    desc: str = "TRANSFER",
    kind: str = "depository",
    cur: str = "USD",
    mask: str | None = None,
    in_transfer: bool = False,
    superseded: bool = False,
    pending: bool = False,
    protected: bool = False,
    owner: uuid.UUID | None = None,
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
        pending,
        protected,
        owner,
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
            c(4, CARD, "70.00", desc="TRANSFER PAYMENT", kind="credit"),
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
            desc=draw(st.sampled_from(["TRANSFER", "EXAMPLE"])),
            pending=draw(st.booleans()),
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
        assert not o.is_pending and not i.is_pending
        assert o.amount < 0 < i.amount and o.amount == -i.amount
        assert o.account_id != i.account_id and o.currency == i.currency
        assert abs((o.posted_date - i.posted_date).days) <= 5
        assert not {p.outflow_id, p.inflow_id} & used
        used |= {p.outflow_id, p.inflow_id}
    for x in match_transfers(pool).suggested:
        assert not {x.outflow_id, x.inflow_id} & used
        used |= {x.outflow_id, x.inflow_id}


def test_refund_on_a_card_is_suggested_never_paired() -> None:
    r = match_transfers(
        [
            c(1, CHK, "-50.00", desc="EXAMPLE MART"),
            c(2, CARD, "50.00", days=3, desc="EXAMPLE MART REFUND", kind="credit"),
        ]
    )
    assert not r.pairs and not r.ambiguous
    assert [(s.outflow_id.int, s.inflow_id.int, s.reason) for s in r.suggested] == [
        (1, 2, "refund_candidate")
    ]


def test_rent_and_paycheck_are_suggested_never_paired() -> None:
    r = match_transfers(
        [c(1, CHK, "-1500.00", desc="RENT"), c(2, SAV, "1500.00", desc="PAYROLL EXAMPLE CO")]
    )
    assert not r.pairs and [s.reason for s in r.suggested] == ["no_hint"]


def test_a_nearer_row_makes_a_hinted_far_partner_ambiguous_not_paired() -> None:
    r = match_transfers(
        [
            c(1, CHK, "-80.00", desc="TRANSFER TO SAVINGS"),
            c(2, SAV, "80.00", days=2, desc="TRANSFER FROM CHECKING"),
            c(3, OTHER, "80.00", days=0, desc="SALARY"),
        ]
    )
    assert not r.pairs and not r.suggested
    assert {a.transaction_id.int for a in r.ambiguous} == {1, 2, 3}


def test_no_equal_amount_row_that_lost_a_contest_is_in_no_list() -> None:
    r = match_transfers(
        [
            c(1, CHK, "-100.00", desc="TRANSFER TO SAVINGS"),
            c(2, SAV, "100.00", desc="DEPOSIT"),
            c(3, OTHER, "100.00", days=5, desc="TRF FROM OTHER"),
        ]
    )
    assert not r.pairs and not r.suggested
    assert {a.transaction_id.int for a in r.ambiguous} == {1, 2, 3}


def test_a_hint_on_one_leg_only_is_suggested_not_paired() -> None:
    a = match_transfers(
        [
            c(1, CHK, "-100.00", desc="ONLINE TRANSFER TO EXT"),
            c(2, SAV, "100.00", days=2, desc="ACME PAYROLL"),
        ]
    )
    assert not a.pairs and [x.reason for x in a.suggested] == ["one_sided_hint"]
    b = match_transfers(
        [
            c(1, CHK, "-1500.00", desc="RENT LANDLORD"),
            c(2, CARD, "1500.00", days=1, desc="PAYMENT THANK YOU", kind="credit"),
        ]
    )
    assert not b.pairs and [x.reason for x in b.suggested] == ["one_sided_hint"]


def test_checking_payment_wording_does_not_make_a_card_refund_a_payment() -> None:
    r = match_transfers(
        [
            c(1, CHK, "-120.00", desc="ELECTRIC BILL PAYMENT"),
            c(2, CARD, "120.00", days=2, desc="AMAZON REFUND", kind="credit"),
        ]
    )
    assert not r.pairs and [x.reason for x in r.suggested] == ["refund_candidate"]


def test_mask_matches_on_digit_boundaries_only() -> None:
    near = c(1, CHK, "-9.00", desc="TO 55123 ACCT", mask="0000")
    r = match_transfers([near, c(2, SAV, "9.00", desc="EXAMPLE", mask="5512")])
    assert not r.pairs and r.suggested[0].reason == "no_hint"
    ok = c(1, CHK, "-9.00", desc="TO ACCT 5512", mask="0000")
    assert ids(match_transfers([ok, c(2, SAV, "9.00", desc="EXAMPLE", mask="5512")])) == {(1, 2)}
    short = c(1, CHK, "-9.00", desc="TO 12", mask="0000")
    assert not match_transfers([short, c(2, SAV, "9.00", desc="EXAMPLE", mask="12")]).pairs


def test_pending_competes_but_is_never_a_leg() -> None:
    assert not match_transfers([c(1, CHK, "-10.00"), c(2, SAV, "10.00", pending=True)]).pairs
    r = match_transfers(
        [c(1, CHK, "-10.00"), c(2, SAV, "10.00"), c(3, OTHER, "10.00", pending=True)]
    )
    assert not r.pairs
    assert {a.transaction_id.int for a in r.ambiguous} == {1, 2}


def test_user_treated_rows_are_suggested_not_paired() -> None:
    r = match_transfers([c(1, CHK, "-10.00", protected=True), c(2, SAV, "10.00")])
    assert not r.pairs and [s.reason for s in r.suggested] == ["user_treated_as_spending"]


def test_accounts_of_two_owners_are_suggested() -> None:
    a, b = uuid.UUID(int=1), uuid.UUID(int=2)
    r = match_transfers([c(1, CHK, "-10.00", owner=a), c(2, SAV, "10.00", owner=b)])
    assert not r.pairs and [s.reason for s in r.suggested] == ["different_owners"]
    joint = match_transfers([c(1, CHK, "-10.00", owner=a), c(2, SAV, "10.00")])
    assert ids(joint) == {(1, 2)}
