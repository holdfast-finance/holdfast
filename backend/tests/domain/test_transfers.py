"""Pure transfer matcher: pairs, edges, ambiguity, order independence. Synthetic data."""

from __future__ import annotations

import random
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.transfers import (
    REASON_AMBIGUOUS,
    REASON_LIKELY,
    MatchResult,
    ProposedPair,
    ReviewEdge,
    TransferCandidate,
    group_edges,
    match_transfers,
    review_edges,
)

D = Decimal
CHK, SAV, CARD, OTHER = (uuid.UUID(int=i) for i in (101, 102, 103, 104))
ALEX, SAM = (uuid.UUID(int=i) for i in (201, 202))
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
    owners = {account: draw(st.sampled_from([None, ALEX, SAM])) for account in accts}
    accounts = draw(st.lists(st.sampled_from(accts), max_size=10))
    return [
        c(
            i,
            account,
            draw(st.sampled_from(["5.00", "-5.00", "7.50", "-7.50"])),
            days=draw(st.integers(0, 8)),
            cur=draw(st.sampled_from(["USD", "USD", "EUR"])),
            desc=draw(st.sampled_from(["TRANSFER", "EXAMPLE"])),
            pending=draw(st.booleans()),
            owner=owners[account],
        )
        for i, account in enumerate(accounts, start=1)
    ]


@given(pools(), st.integers(0, 10_000))
def test_order_independent_and_invariants(pool: list[TransferCandidate], seed: int) -> None:
    shuffled = pool[:]
    random.Random(seed).shuffle(shuffled)  # noqa: S311
    result = match_transfers(pool)
    assert result == match_transfers(shuffled)
    assert review_edges(pool, result) == review_edges(shuffled, result)
    by_id = {t.transaction_id: t for t in pool}
    used: set[uuid.UUID] = set()
    for p in (*result.pairs, *result.links, *result.suggested):
        o, i = by_id[p.outflow_id], by_id[p.inflow_id]
        assert not o.is_pending and not i.is_pending
        assert o.amount < 0 < i.amount and o.amount == -i.amount
        assert o.account_id != i.account_id and o.currency == i.currency
        assert abs((o.posted_date - i.posted_date).days) <= 5
        assert not {p.outflow_id, p.inflow_id} & used
        used |= {p.outflow_id, p.inflow_id}
    for p in result.pairs:
        assert by_id[p.outflow_id].owner_person_id == by_id[p.inflow_id].owner_person_id
    for p in result.links:
        assert by_id[p.outflow_id].owner_person_id != by_id[p.inflow_id].owner_person_id


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


def test_same_person_accounts_pair_with_only_a_mask_hint() -> None:
    result = match_transfers(
        [
            c(1, CHK, "-10.00", owner=ALEX, desc="TO 0000"),
            c(2, SAV, "10.00", owner=ALEX, desc="DEPOSIT", mask="0000"),
        ]
    )
    assert ids(result) == {(1, 2)}
    assert not result.links and not result.suggested


@pytest.mark.parametrize("out_owner", [None, SAM])
def test_joint_or_other_person_card_payment_links(out_owner: uuid.UUID | None) -> None:
    out = c(1, CHK, "-10.00", owner=out_owner, desc="ONLINE PMT")
    inn = c(2, CARD, "10.00", owner=ALEX, desc="PAYMENT THANK YOU", kind="credit")
    result = match_transfers([out, inn])
    assert result.links == (ProposedPair(out.transaction_id, inn.transaction_id),)
    assert not result.pairs and not result.suggested and not result.ambiguous


def test_cross_owner_links_accept_a_mask_from_each_description() -> None:
    out = c(1, CHK, "-10.00", owner=ALEX, desc="TO 0000", mask="1111")
    inn = c(2, SAV, "10.00", desc="FROM 1111", mask="0000")
    result = match_transfers([out, inn])
    assert result.links == (ProposedPair(out.transaction_id, inn.transaction_id),)
    assert not result.pairs and not result.suggested


@pytest.mark.parametrize(
    ("out_desc", "in_desc", "in_kind", "protected", "reason"),
    [
        ("EXAMPLE", "EXAMPLE", "depository", False, "no_hint"),
        ("TRANSFER", "EXAMPLE", "depository", False, "one_sided_hint"),
        ("TRANSFER TO 0000", "EXAMPLE", "depository", False, "one_sided_hint"),
        ("EXAMPLE", "TRANSFER FROM 1111", "depository", False, "one_sided_hint"),
        ("ONLINE PMT", "TRANSFER REFUND", "credit", False, "refund_candidate"),
        ("TRANSFER", "TRANSFER", "depository", True, "user_treated_as_spending"),
    ],
)
def test_cross_owner_suggestions_explain_missing_evidence_or_protection(
    out_desc: str, in_desc: str, in_kind: str, protected: bool, reason: str
) -> None:
    out = c(1, CHK, "-10.00", owner=ALEX, desc=out_desc, mask="1111", protected=protected)
    inn = c(2, SAV, "10.00", days=2, desc=in_desc, kind=in_kind, mask="0000")
    result = match_transfers([out, inn])
    assert not result.pairs and not result.links
    assert [s.reason for s in result.suggested] == [reason]
    assert [edge.reason for edge in review_edges([out, inn], result)] == [reason]


def test_rejected_edge_leaves_other_edges_of_its_legs_available() -> None:
    out = c(1, CHK, "-10.00")
    inn = c(2, SAV, "10.00", days=2)
    closer = c(3, OTHER, "10.00", desc="DEPOSIT", owner=ALEX)
    pool = [out, inn, closer]
    assert match_transfers(pool).ambiguous
    result = match_transfers(
        pool, rejected_pairs=frozenset({(out.transaction_id, closer.transaction_id)})
    )
    assert ids(result) == {(1, 2)}
    assert not result.links and not result.suggested and not result.ambiguous


@pytest.mark.parametrize("pending", [False, True])
def test_leg_rejection_removes_even_a_pending_competitor(pending: bool) -> None:
    out = c(1, CHK, "-10.00")
    inn = c(2, SAV, "10.00")
    competitor = c(3, OTHER, "10.00", pending=pending)
    pool = [out, inn, competitor]
    assert match_transfers(pool).ambiguous
    result = match_transfers(pool, rejected_legs=frozenset({competitor.transaction_id}))
    assert result == match_transfers([out, inn])
    assert ids(result) == {(1, 2)}


@settings(max_examples=500)
@given(pools(), st.data())
def test_rejected_edges_never_reappear_in_random_pools(
    pool: list[TransferCandidate], data: st.DataObject
) -> None:
    # Independent matches ensure that every pool exercises rejection of a pair, link and suggestion.
    joint_a, joint_b, owned = (uuid.UUID(int=n) for n in (111, 112, 113))
    pool = [
        *pool,
        c(11, joint_a, "-11.00"),
        c(12, joint_b, "11.00"),
        c(13, joint_a, "-12.00"),
        c(14, owned, "12.00", owner=ALEX),
        c(15, joint_a, "-13.00", desc="EXAMPLE"),
        c(16, joint_b, "13.00", desc="EXAMPLE"),
    ]
    result = match_transfers(pool)
    expected_rejections = set()
    for n, edges in ((11, result.pairs), (13, result.links), (15, result.suggested)):
        expected_edge = (uuid.UUID(int=n), uuid.UUID(int=n + 1))
        assert expected_edge in {(edge.outflow_id, edge.inflow_id) for edge in edges}
        expected_rejections.add(expected_edge)
    outflows = [row.transaction_id for row in pool if row.amount < 0]
    inflows = [row.transaction_id for row in pool if row.amount > 0]
    rejected_pairs = frozenset(expected_rejections) | data.draw(
        st.frozensets(st.tuples(st.sampled_from(outflows), st.sampled_from(inflows)))
    )
    rejected_legs = data.draw(st.frozensets(st.sampled_from(outflows + inflows)))
    result = match_transfers(pool, rejected_pairs=rejected_pairs, rejected_legs=rejected_legs)
    for edge in (*result.pairs, *result.links, *result.suggested, *review_edges(pool, result)):
        assert (edge.outflow_id, edge.inflow_id) not in rejected_pairs
        assert not {edge.outflow_id, edge.inflow_id} & rejected_legs
    for leg in result.ambiguous:
        assert not {leg.transaction_id, *leg.candidate_ids} & rejected_legs
        for partner in leg.candidate_ids:
            assert (leg.transaction_id, partner) not in rejected_pairs
            assert (partner, leg.transaction_id) not in rejected_pairs


@pytest.mark.parametrize("in_owner", [None, ALEX])
def test_hidden_household_competitor_becomes_likely_in_person_view(
    in_owner: uuid.UUID | None,
) -> None:
    out = c(2, CHK, "-10.00", days=2)
    inn = c(1, SAV, "10.00", owner=in_owner)
    hidden = c(3, OTHER, "10.00", owner=SAM)
    household = match_transfers([out, inn, hidden])
    assert household.ambiguous and not household.pairs and not household.links
    visible = [inn, out]
    person = match_transfers(iter(visible))
    edge = ProposedPair(out.transaction_id, inn.transaction_id)
    assert (person.pairs if in_owner is None else person.links) == (edge,)
    assert review_edges(iter(visible), person) == (
        ReviewEdge(out.transaction_id, inn.transaction_id, REASON_LIKELY, 2, 2),
    )


def test_review_edges_orient_deduplicate_and_skip_pending_legs() -> None:
    out = c(4, CHK, "-10.00", days=2, desc="TRANSFER TO 0000")
    first = c(1, SAV, "10.00", mask="0000")
    second = c(2, OTHER, "10.00", days=4, mask="0000")
    pending = c(3, CARD, "10.00", mask="0000", pending=True)
    pool = [out, pending, second, first]
    result = match_transfers(pool)
    assert {leg.transaction_id for leg in result.ambiguous} == {
        out.transaction_id,
        first.transaction_id,
        second.transaction_id,
    }
    assert review_edges(pool, result) == (
        ReviewEdge(out.transaction_id, first.transaction_id, REASON_AMBIGUOUS, 2, 3),
        ReviewEdge(out.transaction_id, second.transaction_id, REASON_AMBIGUOUS, 2, 3),
    )
    assert review_edges([out, pending], match_transfers([out, pending])) == ()


def review_edge(out: int, inn: int) -> ReviewEdge:
    return ReviewEdge(uuid.UUID(int=out), uuid.UUID(int=inn), REASON_AMBIGUOUS, 1, 2)


@given(
    st.permutations([review_edge(1, 4), review_edge(1, 5), review_edge(2, 6), review_edge(3, 5)])
)
def test_group_edges_returns_order_independent_transitive_components(
    edges: list[ReviewEdge],
) -> None:
    assert group_edges(iter(edges)) == (
        (review_edge(1, 4), review_edge(1, 5), review_edge(3, 5)),
        (review_edge(2, 6),),
    )


def test_group_edges_accepts_an_empty_iterable() -> None:
    assert group_edges([]) == ()
