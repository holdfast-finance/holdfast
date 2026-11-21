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
                         Statement Period 10/01/25 - 10/31/25   Pat Example
                         Account Number **********0000          1 Example St
        Pat Example's
        Debit Statement
        1 Example Plaza | current.com | help@example.test
        Date    Description                          Debits     Credits     Balance
                Starting Balance                                             $0.00
        10/03/25 Deposit from EXAMPLE PAYROLL 0001                $700.00     $700.00
        10/03/25 Internal Transfer to Secured Account  -$700.00               $0.00
        10/06/25 Withdrawal to Example Utility 0002     -$12.00              -$12.00
        10/06/25 Internal Transfer from Secured Account            $12.00       $0.00
        10/31/25 Paycheck Advance                                  $50.00      $50.00
                Ending Balance                                               $50.00
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
        01/06/26 - 02/08/26
        Flagship Checking - 1234560001
        Date Transaction Detail           Amount($)   Balance($)
        01-06 Beginning Balance                          100.00
        01-10 Debit Card Purchase 0000 01-09-26 Example Shop      5.98 -     94.02
        01-12 Deposit Example Payroll                           50.00      144.02
        02-03 Debit Card Purchase 0000 02-02-26 A Very Long Merchant Name Example Town EX
              Example               20.00 -     124.02
        02-08 Ending Balance                                              124.02
        Debit Card Summary
        01-10                 Example Shop     5.98
        Membership Savings - 1234560002
        Date Transaction Detail           Amount($)   Balance($)
        01-06 Beginning Balance                          5.00
        02-08 Ending Balance                              5.00
"""

NFCU_CARD = """\
        SUMMARY OF ACCOUNT ACTIVITY    Navy Federal
        Statement Closing Date 10/06/2026
        ACCOUNT NUMBER      xxx xxxx xxxx 0000
        Previous Balance  $100.00 New Balance   $135.00
        TRANSACTIONS
        PAYMENTS AND CREDITS
        Trans Date Post Date Reference No. Description Submitted By Amount
        09/11/26 09/11/26 7379550625 PAYMENT RECEIVED xxx xxxx xxxx 0000  $60.00
        TRANSACTIONS
        Trans Date Post Date Reference No. Description Amount
        10/02/26 10/05/26 P58QJ310239AFT Example Shop SC       $90.00
        10/03/26 10/05/26 E88QJ381543A8L Example Cafe NC       $5.00
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
    assert main.txn_sum == Decimal("50.00")
    withdrawal = next(t for t in main.txns if t.description.startswith("Withdrawal"))
    assert withdrawal.amount == Decimal("-12.00")
    assert "example" not in st.blocks[1].key  # pod label is hashed


def test_navy_federal_deposit_reconciles_with_wrapped_row_and_summary_section_ignored() -> None:
    st = _parse(NFCU_DEPOSIT)
    assert st.profile_id == "navy-federal-deposit"
    checking, savings = st.blocks
    assert checking.account_last4 == "0001"
    assert [t.amount for t in checking.txns] == [
        Decimal("-5.98"),
        Decimal("50.00"),
        Decimal("-20.00"),
    ]
    assert checking.opening_balance == Decimal("100.00")
    assert checking.closing_balance == Decimal("124.02")
    assert all(b.reconciliation.status == "reconciled" for b in st.blocks)
    assert savings.txns == ()
    assert checking.txns[0].posted_date.year == 2026


def test_navy_federal_card_signs_payment_inflow_purchase_outflow() -> None:
    st = _parse(NFCU_CARD)
    (block,) = st.blocks
    by_desc = {t.description.split()[0]: t.amount for t in block.txns}
    assert by_desc["PAYMENT"] == Decimal("60.00")
    assert sorted(t.amount for t in block.txns if t.amount < 0) == [
        Decimal("-90.00"),
        Decimal("-5.00"),
    ]
    assert block.opening_balance == Decimal("-100.00")
    assert block.closing_balance == Decimal("-135.00")
    assert block.reconciliation.status == "reconciled"
    assert block.account_last4 == "0000"


def test_removing_a_row_turns_reconciled_into_needs_review() -> None:
    broken = NFCU_CARD.replace(
        "        10/03/26 10/05/26 E88QJ381543A8L Example Cafe NC       $5.00\n", ""
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
    for secret in ("Example Utility", "EXAMPLE PAYROLL", "Pat Example", "Example Shop", "50.00"):
        assert secret not in out
    rows = harness.run(tmp_path)
    assert rows["current-debit"]["duplicate"] == 1
    assert rows["current-debit"]["reconciled"] == 1
