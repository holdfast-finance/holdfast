from dataclasses import replace
from datetime import date
from decimal import Decimal
from unittest import TestCase
from uuid import UUID

from app.domain.rules import (
    MAX_REGEX_DESCRIPTION_LENGTH,
    MAX_REGEX_LENGTH,
    DescriptionCondition,
    Rule,
    RuleAction,
    RuleConditions,
    RuleMatch,
    RuleTransaction,
    RuleValidationError,
    can_apply_rule,
    match,
    preview,
)

TXN = RuleTransaction(
    id=UUID(int=1),
    description="  mOOnfern    stationery  37 ",
    amount=Decimal("-12.34"),
    account_id=UUID(int=2),
    posted_on=date(2026, 11, 21),
    merchant_key="moonfern",
)
CATEGORY = UUID(int=3)
RULE = Rule(UUID(int=4), 10, True, RuleConditions(), RuleAction(CATEGORY))


class TestRules(TestCase):
    def test_description_modes(self) -> None:
        cases = (
            (DescriptionCondition("contains", "stationery"), "station"),
            (DescriptionCondition("starts-with", " moonFERN   stationery "), "stationery 37"),
            (DescriptionCondition("exact", "moonfern stationery 37"), "moonfern stationery 370"),
            (DescriptionCondition("regex", r"^(moonfern|sunfern) stationery \d+$"), "moonfern!"),
        )
        for condition, other in cases:
            with self.subTest(mode=condition.mode):
                rule = replace(RULE, conditions=RuleConditions(description=condition))
                self.assertIsNotNone(match([rule], TXN))
                self.assertIsNone(match([rule], replace(TXN, description=other)))

    def test_conditions_are_anded(self) -> None:
        conditions = RuleConditions(
            description=DescriptionCondition("contains", "stationery"),
            merchant_key="moonfern",
            amount_equals=Decimal("-12.34"),
            amount_min=Decimal("-15.00"),
            amount_max=Decimal("-10.00"),
            amount_sign="outflow",
            account_ids=frozenset({TXN.account_id, UUID(int=5)}),
            date_from=TXN.posted_on,
            date_through=date(2026, 11, 23),
        )
        rule = replace(RULE, conditions=conditions)
        self.assertIsNotNone(match([rule], TXN))
        self.assertIsNotNone(
            match([rule], replace(TXN, posted_on=date(2026, 11, 23), account_id=UUID(int=5)))
        )
        for txn in (
            replace(TXN, description="moonfern"),
            replace(TXN, merchant_key=None),
            replace(TXN, merchant_key="MOONFERN"),
            replace(TXN, amount=Decimal("-12.33")),
            replace(TXN, account_id=UUID(int=6)),
            replace(TXN, posted_on=date(2026, 11, 20)),
            replace(TXN, posted_on=date(2026, 11, 24)),
        ):
            with self.subTest(txn=txn):
                self.assertIsNone(match([rule], txn))

    def test_decimal_range_boundaries(self) -> None:
        rule = replace(
            RULE,
            conditions=RuleConditions(amount_min=Decimal("-12.34"), amount_max=Decimal("0.01")),
        )
        for amount, expected in (
            ("-12.35", False),
            ("-12.34", True),
            ("0.00", True),
            ("0.01", True),
            ("0.02", False),
        ):
            with self.subTest(amount=amount):
                self.assertEqual(
                    match([rule], replace(TXN, amount=Decimal(amount))) is not None, expected
                )

    def test_sign_conditions_exclude_zero(self) -> None:
        outflow = replace(RULE, conditions=RuleConditions(amount_sign="outflow"))
        inflow = replace(RULE, conditions=RuleConditions(amount_sign="inflow"))
        positive = replace(TXN, amount=Decimal("0.01"))
        zero = replace(TXN, amount=Decimal("-0.00"))
        self.assertIsNotNone(match([outflow], TXN))
        self.assertIsNone(match([outflow], positive))
        self.assertIsNone(match([outflow], zero))
        self.assertIsNotNone(match([inflow], positive))
        self.assertIsNone(match([inflow], TXN))
        self.assertIsNone(match([inflow], zero))

    def test_priority_tie_break_and_disabled_rules(self) -> None:
        rules = [
            replace(RULE, id=UUID(int=7), priority=-10, enabled=False),
            replace(RULE, id=UUID(int=1), priority=11),
            replace(RULE, id=UUID(int=6)),
            RULE,
        ]
        expected = RuleMatch(TXN.id, RULE.id, RULE.action)
        self.assertEqual(match(rules, TXN), expected)
        self.assertEqual(match(reversed(rules), TXN), expected)
        self.assertIsNone(match(rules[:1], TXN))

    def test_regex_rejects_unbounded_syntax_and_invalid_patterns(self) -> None:
        for pattern in (
            "(a+)+$",
            r"(a)\1",
            "a" * (MAX_REGEX_LENGTH + 1),
            "^a*a*$",
            "(a|aa)+$",
            "(?=a)",
            "(",
        ):
            with self.subTest(pattern=pattern), self.assertRaises(RuleValidationError):
                DescriptionCondition("regex", pattern)

    def test_regex_treats_escaped_operators_and_classes_as_atoms(self) -> None:
        rule = replace(
            RULE,
            conditions=RuleConditions(
                description=DescriptionCondition("regex", r"^moonfern\+ [a-z*?]+$")
            ),
        )
        self.assertIsNotNone(match([rule], replace(TXN, description="Moonfern+ pens*?")))
        self.assertIsNone(match([rule], replace(TXN, description="Moonfern pens")))

    def test_regex_skips_overlong_descriptions(self) -> None:
        rule = replace(
            RULE, conditions=RuleConditions(description=DescriptionCondition("regex", "^a+$"))
        )
        self.assertIsNotNone(
            match([rule], replace(TXN, description="a" * MAX_REGEX_DESCRIPTION_LENGTH))
        )
        self.assertIsNone(
            match([rule], replace(TXN, description="a" * (MAX_REGEX_DESCRIPTION_LENGTH + 1)))
        )

    def test_source_precedence_including_manual_clear(self) -> None:
        for source in (None, "ml", "plaid", "rule", "user"):
            for category in (None, CATEGORY):
                with self.subTest(source=source, category=category):
                    txn = replace(TXN, category_id=category, category_source=source)
                    self.assertIsNotNone(match([RULE], txn))
                    self.assertEqual(can_apply_rule(source), source != "user")
                    should_change = source != "user" and (source != "rule" or category is None)
                    self.assertEqual(len(preview([RULE], [txn])), int(should_change))

    def test_preview_order_count_and_replay(self) -> None:
        rule = replace(RULE, action=RuleAction(CATEGORY, mark_transfer_candidate=True))
        rules = [replace(RULE, id=UUID(int=7), priority=11), rule]
        txns = [
            replace(TXN, id=UUID(int=9), category_source="plaid", category_id=CATEGORY),
            replace(TXN, id=UUID(int=8), category_source="rule", category_id=CATEGORY),
            TXN,
            replace(TXN, id=UUID(int=10), category_source="user"),
        ]

        changes = preview(iter(rules), iter(txns))
        self.assertEqual(changes, preview(reversed(rules), reversed(txns)))
        self.assertEqual(
            [hit.transaction_id for hit in changes], [TXN.id, UUID(int=8), UUID(int=9)]
        )
        by_id = {hit.transaction_id: hit for hit in changes}
        applied = [
            replace(
                txn,
                category_id=by_id[txn.id].action.category_id,
                category_source="rule",
                transfer_candidate=(
                    txn.transfer_candidate or by_id[txn.id].action.mark_transfer_candidate
                ),
            )
            if txn.id in by_id
            else txn
            for txn in txns
        ]
        applied_count = sum(before != after for before, after in zip(txns, applied, strict=True))
        self.assertEqual(len(changes), applied_count)
        self.assertEqual(preview(rules, applied), [])

    def test_existing_transfer_candidate_does_not_require_a_change(self) -> None:
        txn = replace(TXN, category_id=CATEGORY, category_source="rule", transfer_candidate=True)
        self.assertEqual(preview([RULE], [txn]), [])

    def test_duplicate_ids_are_rejected(self) -> None:
        with self.assertRaises(RuleValidationError):
            match([RULE, RULE], TXN)
        with self.assertRaises(RuleValidationError):
            preview([RULE], [TXN, TXN])
