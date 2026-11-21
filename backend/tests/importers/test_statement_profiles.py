"""Bank profiles against sanitized fixtures (``make sanitize`` output) and synthetic layouts."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.statement_parse import ParsedStatement, detect_profile, parse_statement
from app.importers import harness
from app.importers.profile_loader import load_profiles

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "statements"
PROFILES = load_profiles()

DEBIT = """\
                         Statement Period 03/01/19 - 03/31/19   Pat Example
                         Account Number **********0000          1 Example St
        Pat Example's
        Debit Statement
        1 Example Plaza | current.com | help@example.test
        Date    Description                          Debits     Credits     Balance
                Starting Balance                                             $0.00
        03/04/19 Example Deposit One                              $800.00     $800.00
        03/04/19 Example Transfer Out                  -$800.00               $0.00
        03/07/19 Example Withdrawal One                 -$20.00              -$20.00
        03/07/19 Example Transfer In                               $20.00       $0.00
        03/28/19 Example Advance                                   $40.00      $40.00
                Ending Balance                                               $40.00
        Savings Pod
        "Example Pod"
        Date    Description                          Debits     Credits     Balance
                Starting Balance                                             $1.00
                Ending Balance                                               $1.00
"""

NFCU_DEPOSIT = """\
        Summary  of your deposit accounts
        Statement of Account
        Statement Period
        02/04/19 - 03/05/19
        Flagship Checking - 0000000001
        Date Transaction Detail           Amount($)   Balance($)
        02-04 Beginning Balance                          200.00
        02-10 Example Card Purchase 0000 02-09-19 Example Shop One      10.00 -    190.00
        02-12 Example Deposit                                   50.00      240.00
        03-03 Example Card Purchase 0000 03-02-19 Example Long Merchant Name Example Town EX
              Example               30.00 -     210.00
        03-05 Ending Balance                                              210.00
        Example Card Summary
        02-10                 Example Shop One    10.00
        Membership Savings - 0000000002
        Date Transaction Detail           Amount($)   Balance($)
        02-04 Beginning Balance                          8.00
        03-05 Ending Balance                              8.00
"""

NFCU_CARD = """\
        SUMMARY OF ACCOUNT ACTIVITY    Navy Federal
        Statement Closing Date 05/06/2019
        ACCOUNT NUMBER      xxx xxxx xxxx 0000
        Previous Balance  $200.00 New Balance   $245.00
        TRANSACTIONS
        PAYMENTS AND CREDITS
        Trans Date Post Date Reference No. Description Submitted By Amount
        04/11/19 04/11/19 0000000001 EXAMPLE PAYMENT xxx xxxx xxxx 0000  $80.00
        TRANSACTIONS
        Trans Date Post Date Reference No. Description Amount
        04/20/19 04/22/19 EXAMPLEREF0001 Example Shop One EX       $100.00
        04/25/19 04/26/19 EXAMPLEREF0002 Example Shop Two EX       $25.00
        INTEREST CHARGED
        INTEREST CHARGE-PURCHASES                              $0.00
"""


def _parse(text: str) -> ParsedStatement:
    profile = detect_profile(text, PROFILES)
    assert profile is not None
    return parse_statement(text, profile)


def test_profiles_load_with_unique_ids() -> None:
    assert {p.id for p in PROFILES} >= {
        "current-account-summary",
        "current-build-card",
        "current-debit",
        "current-secured",
        "navy-federal-card",
        "navy-federal-deposit",
    }


@pytest.mark.parametrize(
    ("fixture", "profile_id", "status"),
    [
        ("secured.sanitized.txt", "current-secured", "reconciled"),
        ("build_card.sanitized.txt", "current-build-card", "reconciled"),
        ("account_summary.sanitized.txt", "current-account-summary", "balances_only"),
    ],
)
def test_sanitized_fixture_detects_and_reconciles(
    fixture: str, profile_id: str, status: str
) -> None:
    st = _parse((FIXTURES / "current" / fixture).read_text())
    assert st.profile_id == profile_id
    assert st.status == status


def test_sanitized_build_card_outflow_is_negative_and_reconciles_in_holdfast_sign() -> None:
    st = _parse((FIXTURES / "current" / "build_card.sanitized.txt").read_text())
    (block,) = st.blocks
    assert block.txns, "fixture should carry transactions"
    assert block.opening_balance is not None and block.closing_balance is not None
    assert abs(block.closing_balance - block.opening_balance - block.txn_sum) <= Decimal("0.01")
    # A printed charge (positive, owed up) must be stored as an outflow (negative).
    charges = [t for t in block.txns if "Payment" not in t.description]
    assert all(t.amount < 0 for t in charges)


def test_sanitized_secured_debits_negative_credits_positive() -> None:
    st = _parse((FIXTURES / "current" / "secured.sanitized.txt").read_text())
    assert any(t.amount < 0 for t in st.blocks[0].txns)
    assert any(t.amount > 0 for t in st.blocks[0].txns)


def test_current_debit_main_account_and_pod_reconcile_as_separate_blocks() -> None:
    st = _parse(DEBIT)
    assert st.profile_id == "current-debit"
    assert [b.reconciliation.status for b in st.blocks] == ["reconciled", "reconciled"]
    main = st.blocks[0]
    assert main.account_last4 == "0000"
    assert main.txn_sum == Decimal("40.00")
    withdrawal = next(t for t in main.txns if t.description.startswith("Example Withdrawal"))
    assert withdrawal.amount == Decimal("-20.00")
    assert "example" not in st.blocks[1].key  # pod label is hashed


def test_navy_federal_deposit_reconciles_with_wrapped_row_and_summary_section_ignored() -> None:
    st = _parse(NFCU_DEPOSIT)
    assert st.profile_id == "navy-federal-deposit"
    checking, savings = st.blocks
    assert checking.account_last4 == "0001"
    assert [t.amount for t in checking.txns] == [
        Decimal("-10.00"),
        Decimal("50.00"),
        Decimal("-30.00"),
    ]
    assert checking.opening_balance == Decimal("200.00")
    assert checking.closing_balance == Decimal("210.00")
    assert all(b.reconciliation.status == "reconciled" for b in st.blocks)
    assert savings.txns == ()
    assert checking.txns[0].posted_date.year == 2019


def test_navy_federal_card_signs_payment_inflow_purchase_outflow() -> None:
    st = _parse(NFCU_CARD)
    (block,) = st.blocks
    by_desc = {t.description.split()[0]: t.amount for t in block.txns}
    assert by_desc["EXAMPLE"] == Decimal("80.00")
    assert sorted(t.amount for t in block.txns if t.amount < 0) == [
        Decimal("-100.00"),
        Decimal("-25.00"),
    ]
    assert block.opening_balance == Decimal("-200.00")
    assert block.closing_balance == Decimal("-245.00")
    assert block.reconciliation.status == "reconciled"
    assert block.account_last4 == "0000"


def test_removing_a_row_turns_reconciled_into_needs_review() -> None:
    broken = NFCU_CARD.replace(
        "        04/25/19 04/26/19 EXAMPLEREF0002 Example Shop Two EX       $25.00\n", ""
    )
    assert _parse(broken).status == "needs_review"


def test_reparsing_yields_identical_fingerprints() -> None:
    a = [t.fingerprint for b in _parse(DEBIT).blocks for t in b.txns]
    b = [t.fingerprint for b_ in _parse(DEBIT).blocks for t in b_.txns]
    assert a == b
    assert len(set(a)) == len(a)


def test_harness_reports_counts_and_no_statement_content(tmp_path: Path) -> None:
    (tmp_path / "debit.txt").write_text(DEBIT)
    (tmp_path / "debit-copy.txt").write_text(DEBIT)  # identical bytes: a duplicate
    (tmp_path / "card.txt").write_text(NFCU_CARD)
    (tmp_path / "junk.txt").write_text("not a statement")
    out = harness.render(harness.run(tmp_path))
    assert "current-debit" in out
    assert "(no profile)" in out
    assert "TOTAL" in out
    for secret in ("Example Deposit One", "Pat Example", "Example Shop", "800.00"):
        assert secret not in out
    rows = harness.run(tmp_path)
    assert rows["current-debit"]["duplicate"] == 1
    assert rows["current-debit"]["reconciled"] == 1
